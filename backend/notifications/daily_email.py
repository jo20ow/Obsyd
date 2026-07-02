"""
Daily Briefing Email — compact morning briefing.

Mon-Fri at 07:00 UTC via APScheduler (price refresh at 06:45).
Uses Resend API (free tier: 100 emails/day, capped at 95 for safety).
"""

import json
import logging
import re
import secrets
from datetime import datetime, timezone

import httpx

from backend.config import settings
from backend.database import SessionLocal
from backend.models.alerts import Alert
from backend.models.analytics import (
    DisruptionScoreHistory,
    EIAPredictionHistory,
    MarketReport,
)
from backend.models.crypto import CryptoPrice
from backend.models.energy import EnergyPrice
from backend.models.pro_features import (
    EmailSubscriber,
    EquitySnapshot,
)
from backend.models.subscription import Subscription
from backend.models.vessels import FloatingStorageEvent
from backend.models.watchlist import WatchlistItem
from backend.power.zones import POWER_ZONES
from backend.routes.atlas import CRITICAL_MATERIALS, _concentration, _material_rows
from backend.routes.briefing import _build_briefing
from backend.routes.power import load_power_situation
from backend.signals.crack_spread import get_crack_spread
from backend.signals.tonnage_proxy import compute_rerouting_index
from backend.situation.physical import build_physical_situation

logger = logging.getLogger(__name__)

DAILY_SEND_LIMIT = 95
RESEND_FREE_TIER_LIMIT = 100
LIMIT_WARN_THRESHOLD = 90
LIMIT_WARN_RESET = 85
OPS_EMAIL = "obsyd.dev@pm.me"

_limit_warning_sent = False


async def send_daily_email():
    """Main entry point called by scheduler at 07:00 UTC Mon-Fri."""
    global _limit_warning_sent

    api_key = settings.resend_api_key
    if not api_key:
        logger.warning("Daily email: RESEND_API_KEY not configured, skipping")
        return

    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()

    db = SessionLocal()
    try:
        _sync_pro_subscribers(db)

        # The brief is free (Weg B): it goes to every active EmailSubscriber —
        # both comp/Pro rows synced above and free opt-ins from POST /api/email/subscribe
        # (tier="free"). The legacy free Waitlist is a separate email-capture table and
        # is deliberately not mailed here.
        subscribers = (
            db.query(EmailSubscriber)
            .filter(EmailSubscriber.active == True)  # noqa: E712
            .all()
        )

        if not subscribers:
            logger.info("Daily email: no subscribers, skipping")
            return

        # --- Resend free tier safety check ---
        total_recipients = len(subscribers)
        logger.info("Daily email: %d Pro recipients", total_recipients)

        if total_recipients > DAILY_SEND_LIMIT:
            logger.warning(
                "Resend free tier limit: %d recipients but only sending to first %d",
                total_recipients,
                DAILY_SEND_LIMIT,
            )
            if not _limit_warning_sent:
                try:
                    await _send_limit_warning(api_key, total_recipients)
                    _limit_warning_sent = True
                except Exception as e:
                    logger.error("Failed to send limit warning email: %s", e)
        elif total_recipients > LIMIT_WARN_THRESHOLD:
            logger.warning(
                "Approaching Resend free tier limit (%d/%d subscribers)",
                total_recipients,
                RESEND_FREE_TIER_LIMIT,
            )
            if not _limit_warning_sent:
                try:
                    await _send_limit_warning(api_key, total_recipients)
                    _limit_warning_sent = True
                except Exception as e:
                    logger.error("Failed to send limit warning email: %s", e)
        elif total_recipients < LIMIT_WARN_RESET:
            _limit_warning_sent = False

        # Build data
        briefing = await _build_briefing()
        rerouting = compute_rerouting_index(days=365)
        crack = await get_crack_spread()
        email_data = _gather_email_data(db, crack)
        physical = email_data.get("physical_situation") or {}
        overall_state = physical.get("overall") if physical.get("available") else None
        subject = _build_subject_line(briefing, rerouting, crack, physical_state=overall_state)
        html_template = _build_full_html(briefing, rerouting, crack, email_data)

        sent = 0
        for sub in subscribers:
            if sent >= DAILY_SEND_LIMIT:
                break
            # Per-recipient personalisation: their watched materials/zones.
            html = (
                html_template.replace("{{email}}", sub.email)
                .replace("{{token}}", sub.unsubscribe_token or "")
                .replace("{{watch_block}}", _build_watch_block(db, sub.email))
            )
            try:
                await _send_via_resend(api_key, sub.email, subject, html, sub.unsubscribe_token or "")
                sent += 1
            except Exception as e:
                logger.error("Daily email failed for %s: %s", sub.email, e)

        logger.info("Daily email: sent %d emails", sent)
    except Exception as e:
        logger.error("Daily email build failed: %s", e)
    finally:
        db.close()


# ─── Sending helpers ─────────────────────────────────────────────────────────


async def _send_limit_warning(api_key: str, count: int):
    """Send a one-time warning email to ops when approaching the Resend free tier limit."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": "OBSYD <briefing@obsyd.dev>",
                "to": [OPS_EMAIL],
                "subject": f"OBSYD: Approaching email limit — {count}/{RESEND_FREE_TIER_LIMIT} subscribers",
                "html": (
                    f"<p>You have <strong>{count}</strong> active subscribers.</p>"
                    f"<p>Resend free tier allows {RESEND_FREE_TIER_LIMIT} emails/day. "
                    "Consider upgrading to Resend Pro ($20/mo) or enabling paid subscriptions.</p>"
                ),
            },
        )
        resp.raise_for_status()
    logger.info("Limit warning email sent to %s (%d subscribers)", OPS_EMAIL, count)


def _sync_pro_subscribers(db):
    """Ensure all active Pro subscribers have an EmailSubscriber entry."""
    active_subs = db.query(Subscription).filter(Subscription.status == "active").all()
    for sub in active_subs:
        existing = db.query(EmailSubscriber).filter(EmailSubscriber.email == sub.email).first()
        if not existing:
            db.add(
                EmailSubscriber(
                    email=sub.email,
                    tier="pro",
                    unsubscribe_token=secrets.token_urlsafe(32),
                    active=True,
                )
            )
    db.commit()


async def _send_via_resend(api_key: str, to_email: str, subject: str, html: str, unsubscribe_token: str):
    """Send a single email via Resend API."""
    unsubscribe_url = f"https://obsyd.dev/api/email/unsubscribe?token={unsubscribe_token}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": "OBSYD <briefing@obsyd.dev>",
                "to": [to_email],
                "subject": subject,
                "html": html,
                "headers": {
                    "List-Unsubscribe": f"<{unsubscribe_url}>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                },
            },
        )
        resp.raise_for_status()


# ─── Formatting helpers ──────────────────────────────────────────────────────


def _safe(val, fmt=".2f", fallback="---"):
    if val is None:
        return fallback
    try:
        return f"{val:{fmt}}"
    except (TypeError, ValueError):
        return fallback


def _pct(val, fallback="---"):
    if val is None:
        return fallback
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def _build_subject_line(briefing: dict, rerouting: dict, crack: dict, physical_state: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    parts = [f"OBSYD Daily -- {now.strftime('%d %b')}"]

    # The physical energy system is the front door — surface a non-calm overall state first.
    if physical_state and physical_state != "CALM":
        parts.append(f"Energy {physical_state}")

    market = briefing.get("market_snapshot", {})
    wti = market.get("wti", {})
    if wti.get("price") is not None:
        parts.append(f"WTI ${_safe(wti['price'])}")

    if crack and not crack.get("error"):
        spread = crack.get("spread_321")
        if spread is not None:
            parts.append(f"3-2-1 ${_safe(spread)}")

    anomalies = briefing.get("anomalies", [])
    if anomalies:
        top = anomalies[0]
        parts.append(f"{top['chokepoint'].capitalize()} {_pct(top.get('drop_pct'))}")

    return " | ".join(parts)


# ─── Data helpers ────────────────────────────────────────────────────────────


def _get_floating_storage_count() -> tuple[int, int]:
    db = SessionLocal()
    try:
        active = db.query(FloatingStorageEvent).filter(FloatingStorageEvent.status == "active").all()
        total = len(active)
        vlcc = sum(1 for e in active if e.ship_name and "vlcc" in e.ship_name.lower())
        return total, vlcc
    except Exception:
        return 0, 0
    finally:
        db.close()


def _gather_email_data(db, crack: dict) -> dict:
    """Gather all data for the email template."""
    data: dict = {}

    try:
        data["crack_analysis"] = _get_crack_analysis(db, crack)
    except Exception as e:
        logger.warning("Email crack analysis failed: %s", e)

    try:
        data["equity_movers"] = _get_equity_movers(db)
    except Exception as e:
        logger.warning("Email equity movers failed: %s", e)

    try:
        data["chokepoint_detail"] = _get_chokepoint_detail()
    except Exception as e:
        logger.warning("Email chokepoint detail failed: %s", e)

    try:
        data["disruption_context"] = _get_disruption_context(db)
    except Exception as e:
        logger.warning("Email disruption context failed: %s", e)

    try:
        data["eia_prediction"] = _get_eia_prediction(db)
    except Exception as e:
        logger.warning("Email EIA prediction failed: %s", e)

    try:
        data["power_situations"] = [load_power_situation(db, z) for z in POWER_ZONES]
    except Exception as e:
        logger.warning("Email power situations failed: %s", e)

    try:
        data["physical_situation"] = build_physical_situation(db)
    except Exception as e:
        logger.warning("Email physical situation failed: %s", e)

    return data


def _get_crack_analysis(db, crack: dict) -> dict | None:
    if not crack or crack.get("error"):
        return None
    s321 = crack.get("spread_321")
    if s321 is None:
        return None

    avg_30d = crack.get("avg_30d")
    percentile = crack.get("percentile_1y")

    pct_vs_30d = None
    if avg_30d and avg_30d != 0:
        pct_vs_30d = ((s321 - avg_30d) / avg_30d) * 100

    return {
        "spread": s321,
        "avg_30d": avg_30d,
        "percentile": percentile,
        "pct_vs_30d": round(pct_vs_30d, 1) if pct_vs_30d is not None else None,
    }


def _get_equity_movers(db) -> dict | None:
    latest_date = db.query(EquitySnapshot.date).order_by(EquitySnapshot.date.desc()).first()
    if not latest_date:
        return None
    snapshots = db.query(EquitySnapshot).filter(EquitySnapshot.date == latest_date[0]).all()
    if not snapshots:
        return None

    movers = sorted(
        [s for s in snapshots if s.change_pct is not None],
        key=lambda s: abs(s.change_pct),
        reverse=True,
    )
    top3 = [{"ticker": s.ticker, "change_pct": s.change_pct} for s in movers[:3]]

    corr_candidates = [s for s in snapshots if s.wti_corr_30d is not None]
    best_corr = max(corr_candidates, key=lambda s: abs(s.wti_corr_30d)) if corr_candidates else None

    return {
        "top_movers": top3,
        "highest_corr": {"ticker": best_corr.ticker, "corr": best_corr.wti_corr_30d} if best_corr else None,
    }


def _get_chokepoint_detail() -> list[dict] | None:
    from backend.signals.historical_lookup import find_anomalies

    chokepoints = ["hormuz", "malacca", "suez", "cape", "bab_el_mandeb"]
    details = []
    for cp in chokepoints:
        try:
            result = find_anomalies(cp, threshold_pct=40.0)
            current = result.get("current", {})
            drop = current.get("drop_pct", 0)
            if abs(drop) > 10:
                details.append(
                    {
                        "name": result.get("chokepoint", cp),
                        "current": current.get("n_total", 0),
                        "avg_30d": round(current.get("avg_30d", 0)),
                        "drop_pct": round(drop, 1),
                    }
                )
        except Exception as e:  # noqa: PERF203
            logger.debug("Chokepoint detail for %s failed: %s", cp, e)
            continue
    return details if details else None


def _get_disruption_context(db) -> dict:
    """Get disruption score and catalyst sentence from latest market report."""
    result: dict = {"score": None, "catalyst": ""}

    ds = db.query(DisruptionScoreHistory).order_by(DisruptionScoreHistory.date.desc()).first()
    if ds:
        result["score"] = ds.composite_score

    report = db.query(MarketReport).order_by(MarketReport.created_at.desc()).first()
    if report:
        # Always use full sections_json (headlines_json is truncated)
        try:
            sections = json.loads(report.sections_json) if report.sections_json else {}
            result["catalyst"] = sections.get("catalyst", "")
        except (json.JSONDecodeError, TypeError):
            pass

    return result


def _get_eia_prediction(db) -> dict | None:
    """Get latest EIA prediction. Only relevant on Wednesdays."""
    if datetime.now(timezone.utc).weekday() != 2:
        return None
    latest = db.query(EIAPredictionHistory).order_by(EIAPredictionHistory.date.desc()).first()
    if not latest:
        return None
    tanker_change_pct = None
    if latest.tanker_count_30d_avg and latest.tanker_count_30d_avg > 0:
        tanker_change_pct = ((latest.tanker_count - latest.tanker_count_30d_avg) / latest.tanker_count_30d_avg) * 100

    # Only include pearson_r if meaningful (r > 0.2 and >= 8 weeks of data)
    weeks_count = db.query(EIAPredictionHistory).count()
    r_val = latest.pearson_r
    show_r = r_val is not None and abs(r_val) > 0.2 and weeks_count >= 8

    return {
        "prediction": latest.prediction,
        "tanker_count": latest.tanker_count,
        "tanker_count_30d_avg": latest.tanker_count_30d_avg,
        "tanker_change_pct": round(tanker_change_pct, 1) if tanker_change_pct is not None else None,
        "pearson_r": r_val if show_r else None,
    }


# ─── Per-user "YOUR WATCH" block ─────────────────────────────────────────────

# key → (label, kind, spec) for reusing the atlas concentration helpers.
_MATERIAL_SPECS = {key: (label, kind, spec) for key, label, kind, spec in CRITICAL_MATERIALS}


def _build_watch_block(db, email: str) -> str:
    """Personalised 'YOUR WATCH' HTML fragment for the daily brief.

    For each watched material: current supply concentration (top producer + HHI).
    For each watched zone: the latest radar anomaly, or 'no anomaly'. Returns ''
    when the user watches nothing, so the global brief is unchanged for them.
    """
    items = db.query(WatchlistItem).filter(WatchlistItem.email == email).all()
    if not items:
        return ""

    lines: list[str] = []
    for it in items:
        if it.kind == "material":
            spec_entry = _MATERIAL_SPECS.get(it.key)
            if not spec_entry:
                continue
            _label, kind, spec = spec_entry
            try:
                conc = _concentration(_material_rows(db, kind, spec))
            except Exception:  # noqa: BLE001 — brief must never fail on one item
                conc = None
            if conc:
                lines.append(
                    f"{it.label} &middot; top producer {conc['top_country']} "
                    f"{round(conc['top_share'] * 100)}%, HHI {conc['hhi']:.2f}"
                )
            else:
                lines.append(f"{it.label} &middot; no supply data")
        elif it.kind == "zone":
            alert = (
                db.query(Alert)
                .filter(Alert.zone == it.key)
                .order_by(Alert.created_at.desc())
                .first()
            )
            if alert:
                lines.append(f"{it.label} &middot; {alert.title}")
            else:
                lines.append(f"{it.label} &middot; no anomaly flagged")
        elif it.kind == "symbol":
            # Latest stored close if we persist this symbol (TTF/COPPER/POWER_DE);
            # otherwise just surface the item so it isn't silently dropped.
            row = (
                db.query(EnergyPrice)
                .filter(EnergyPrice.symbol == it.key)
                .order_by(EnergyPrice.date.desc())
                .first()
            )
            if row is not None and row.close is not None:
                lines.append(f"{it.label} &middot; {row.close:.2f}")
            else:
                lines.append(f"{it.label} &middot; on your watchlist")
        elif it.kind == "crypto":
            crow = (
                db.query(CryptoPrice)
                .filter(CryptoPrice.symbol == it.key)
                .order_by(CryptoPrice.date.desc())
                .first()
            )
            if crow is not None:
                chg = f" ({crow.change_24h_pct:+.1f}% 24h)" if crow.change_24h_pct is not None else ""
                lines.append(f"{it.label} &middot; ${crow.price_usd:,.0f}{chg}")
            else:
                lines.append(f"{it.label} &middot; on your watchlist")

    if not lines:
        return ""

    rows = "".join(
        f'<div style="font-size:14px;color:{_TEXT};line-height:1.6;margin-bottom:4px">&bull; {ln}</div>'
        for ln in lines
    )
    return (
        _DIVIDER
        + '<div style="margin-bottom:8px">'
        f'<span style="display:inline-block;background:{_ACCENT};color:#0f1923;font-size:11px;'
        "font-weight:600;letter-spacing:1px;text-transform:uppercase;"
        'padding:3px 8px;border-radius:4px">YOUR WATCH</span></div>'
        + rows
    )


# ─── HTML Template ───────────────────────────────────────────────────────────

# Design tokens
_FONT = "-apple-system,'Segoe UI',Roboto,Arial,sans-serif"
_BG_OUTER = "#0f1923"
_BG_CARD = "#162230"
_BORDER = "#2a3a4a"
_TEXT = "#e8eaed"
_MUTED = "#9aa0a6"
_GREEN = "#34a853"
_RED = "#ea4335"
_ACCENT = "#5bbfb5"
_AMBER = "#f59e0b"
_DIVIDER = f'<div style="border-top:1px solid {_BORDER};margin:20px 0"></div>'

# European Power Desk is the front door → its situation leads the brief.
_POWER_STATE_COLOR = {"CALM": _GREEN, "ELEVATED": _AMBER, "STRESSED": _RED}


def _fmt_signed_pct(v) -> str:
    return "--" if v is None else f"{'+' if v > 0 else ''}{v:.1f}%"


def _build_physical_block(situation: dict | None) -> str:
    """Lead the brief with the whole physical energy system: oil + gas + power in one
    summary, each with its state and (where anomalous) the honest price context."""
    if not situation or not situation.get("available"):
        return ""
    overall = situation.get("overall", "CALM")
    ocolor = _POWER_STATE_COLOR.get(overall, _MUTED)
    domains = situation.get("domains") or {}
    rows = []
    for k in ("oil", "gas", "power"):
        d = domains.get(k)
        if not d or not d.get("available"):
            continue
        state = d.get("state", "CALM")
        color = _POWER_STATE_COLOR.get(state, _MUTED)
        headline = d.get("headline", "")
        fwd = d.get("forward") or {}
        if fwd.get("residual_mw") is not None:
            headline += f' &middot; D+1 residual {fwd["residual_mw"] / 1000:.1f} GW'
        rows.append(
            "<tr>"
            f'<td style="padding:4px 10px 4px 0;white-space:nowrap;font-weight:700;color:{_TEXT}">{d.get("label", k)}</td>'
            f'<td style="padding:4px 10px 4px 0;white-space:nowrap;font-weight:700;color:{color}">{state}</td>'
            f'<td style="padding:4px 0;color:{_MUTED};font-size:13px">{headline}</td>'
            "</tr>"
        )
        ctx = d.get("context")
        if ctx:
            txt = (
                f'&#8627; last {ctx["n"]} {ctx["event_label"]} &rarr; {ctx["price_label"]} '
                f'{_fmt_signed_pct(ctx.get("median_30d_pct"))} @30d '
                f'({_fmt_signed_pct(ctx.get("median_7d_pct"))} @7d) &middot; not a forecast'
            )
            rows.append(
                f'<tr><td></td><td></td><td style="padding:0 0 6px 0;color:{_MUTED};font-size:12px">{txt}</td></tr>'
            )
    if not rows:
        return ""
    return (
        _DIVIDER
        + '<div style="font-size:12px;color:' + _MUTED
        + ';text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">'
        + f'Physical Energy System &mdash; <span style="color:{ocolor};font-weight:700">{overall}</span></div>'
        + f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-family:{_FONT}">'
        + "".join(rows)
        + "</table>"
    )


def _build_power_block(situations: list[dict] | None) -> str:
    """Render the per-zone power situation (DE-LU/FR/NL) as the brief's lead block."""
    if not situations:
        return ""
    rows = []
    for s in situations:
        if not s or not s.get("available"):
            continue
        state = s.get("state", "CALM")
        color = _POWER_STATE_COLOR.get(state, _MUTED)
        label = s.get("zone_label") or s.get("zone") or ""
        headline = s.get("headline", "")
        # headline is "<zone> · day-ahead €.. · residual .. GW · spark €.." — drop the zone prefix.
        metrics = headline.split(" · ", 1)[1] if " · " in headline else headline
        extra = ""
        flags = s.get("flags") or []
        if flags:
            extra += " · " + ", ".join(f["label"] for f in flags)
        if s.get("stale"):
            extra += f' <span style="color:{_MUTED}">· as of {s.get("as_of")} ({s.get("age_days")}d)</span>'
        rows.append(
            "<tr>"
            f'<td style="padding:4px 10px 4px 0;white-space:nowrap;font-weight:700;color:{_TEXT}">{label}</td>'
            f'<td style="padding:4px 10px 4px 0;white-space:nowrap;font-weight:700;color:{color}">{state}</td>'
            f'<td style="padding:4px 0;color:{_MUTED};font-size:13px">{metrics}{extra}</td>'
            "</tr>"
        )
    if not rows:
        return ""
    return (
        _DIVIDER
        + f'<div style="font-size:12px;color:{_MUTED};text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">European Power Desk</div>'
        + f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-family:{_FONT}">'
        + "".join(rows)
        + "</table>"
    )


def _build_full_html(briefing: dict, rerouting: dict, crack: dict, data: dict) -> str:
    """Build the daily briefing email HTML."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%A, %d %B %Y")
    market = briefing.get("market_snapshot", {})
    mkt_struct = briefing.get("market_structure") or {}
    anomalies = briefing.get("anomalies", [])

    # ── Price grid (2 rows × 3 columns) ──
    def _pcell(key, label):
        p = market.get(key, {})
        price = p.get("price")
        change = p.get("change_pct")
        cell_style = f'style="padding:0 6px 14px 0;vertical-align:top;width:33%;font-family:{_FONT}"'
        if price is None:
            return (
                f"<td {cell_style}>"
                f'<div style="font-size:12px;color:{_MUTED};text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px">{label}</div>'
                f'<div style="font-size:16px;color:{_MUTED}">---</div>'
                "</td>"
            )
        pfmt = f"${price:,.0f}" if price >= 100 else f"${price:.2f}"
        chg_html = ""
        if change is not None:
            arrow = "&#9650;" if change >= 0 else "&#9660;"
            color = _GREEN if change >= 0 else _RED
            chg_html = f' <span style="font-size:14px;font-weight:700;color:{color}">{arrow}{abs(change):.1f}%</span>'
        return (
            f"<td {cell_style}>"
            f'<div style="font-size:12px;color:{_MUTED};text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px">{label}</div>'
            f'<div><span style="font-size:16px;font-weight:700;color:{_TEXT}">{pfmt}</span>{chg_html}</div>'
            "</td>"
        )

    price_rows = ""
    for row in [
        [("wti", "WTI"), ("brent", "BRENT"), ("ng", "NG")],
        [("ttf", "TTF"), ("gold", "GOLD"), ("copper", "COPPER")],
    ]:
        cells = "".join(_pcell(k, lbl) for k, lbl in row)
        price_rows += f"<tr>{cells}</tr>"

    # ── Futures + Crack (one line) ──
    summary = mkt_struct.get("summary", "unavailable")
    wti_spread = mkt_struct.get("curves", {}).get("WTI", {}).get("spread_pct")
    futures_str = summary.capitalize()
    if wti_spread is not None:
        futures_str += f" ({_pct(wti_spread)})"

    crack_str = ""
    ca = data.get("crack_analysis") or {}
    if crack and not crack.get("error"):
        s321 = crack.get("spread_321")
        if s321 is not None:
            pct_parts: list[str] = []
            pctile = ca.get("percentile") or crack.get("percentile_1y")
            pct_vs_30d = ca.get("pct_vs_30d")
            if pctile is not None:
                pct_parts.append(f"{pctile}th pct")
            if pct_vs_30d is not None:
                color = _GREEN if pct_vs_30d >= 0 else _RED
                pct_parts.append(f'<span style="color:{color}">{_pct(pct_vs_30d)} vs 30d</span>')
            crack_str = (
                f' &middot; 3-2-1 Crack: <span style="color:{_ACCENT};font-weight:600">${_safe(s321)}/bbl</span>'
            )
            if pct_parts:
                crack_str += f" ({', '.join(pct_parts)})"

    futures_line = f"Futures: {futures_str}{crack_str}"

    # ── Physical energy system (the unified front-door summary → leads the body) ──
    physical_html = _build_physical_block(data.get("physical_situation"))

    # ── European Power Desk (per-zone power detail, below the unified summary) ──
    power_html = _build_power_block(data.get("power_situations"))

    # ── Disruption section ──
    disruption_html = ""
    dc = data.get("disruption_context") or {}
    score = dc.get("score")
    catalyst = dc.get("catalyst", "")
    cd = data.get("chokepoint_detail")
    r_current = rerouting.get("current", {}) if rerouting.get("available") else {}
    cape_pct = r_current.get("ratio_pct")

    # Determine which chokepoints are "headline" disruptions (>25% from briefing anomalies)
    headline_names: set[str] = set()
    for a in anomalies:
        headline_names.add(a.get("chokepoint", "").upper())

    has_disruption = bool(anomalies) or (score is not None and score >= 40)

    if has_disruption:
        # Badge + score — use single source (DisruptionScoreHistory)
        score_int = round(score) if score is not None else None
        score_text = f" Score: {score_int}/100" if score_int is not None else ""
        # Strip any embedded score references from catalyst to avoid inconsistency
        if catalyst and score_int is not None:
            catalyst = re.sub(r"\bAt \d+/100[,.]?\s*", "", catalyst).strip()
            catalyst = re.sub(r"\bscore[: ]+\d+/100[,.]?\s*", "", catalyst, flags=re.IGNORECASE).strip()
            # Ensure first character is uppercase after stripping
            if catalyst and catalyst[0].islower():
                catalyst = catalyst[0].upper() + catalyst[1:]
        disruption_html = (
            _DIVIDER + '<div style="margin-bottom:12px">'
            f'<span style="display:inline-block;background:{_RED};color:#fff;font-size:11px;'
            f"font-weight:600;letter-spacing:1px;text-transform:uppercase;"
            f'padding:3px 8px;border-radius:4px;vertical-align:middle">DISRUPTION</span>'
            f'<span style="font-size:15px;color:{_TEXT};margin-left:10px;vertical-align:middle">'
            f"Supply Disruption{score_text}</span>"
            "</div>"
        )

        # Catalyst sentence
        if catalyst:
            disruption_html += (
                f'<div style="font-size:15px;color:{_TEXT};line-height:1.6;margin-bottom:16px">{catalyst}</div>'
            )

    # Chokepoint detail table (>10% deviation, excluding headline chokepoints)
    cp_rows_html = ""
    if cd:
        for d in cd:
            # Skip if already in the disruption headline
            if d["name"].upper().split()[-1] in headline_names or d["name"].upper() in headline_names:
                # Also check short names like HORMUZ matching "STRAIT OF HORMUZ"
                continue
            color = (
                _RED
                if d["drop_pct"] < -25
                else _RED
                if d["drop_pct"] < -10
                else _GREEN
                if d["drop_pct"] > 10
                else _MUTED
            )
            name = d["name"].replace("Strait of ", "").replace(" Strait", "").replace(" Canal", "")
            pct_color = _RED if d["drop_pct"] < 0 else _GREEN
            cp_rows_html += (
                "<tr>"
                f'<td style="padding:3px 0;color:{_ACCENT};font-weight:600;font-size:15px">{name}</td>'
                f'<td style="padding:3px 0;text-align:right;font-size:15px">'
                f'<span style="color:{pct_color};font-weight:700">{_pct(d["drop_pct"])} vs 30d avg</span>'
                "</td></tr>"
            )

    # Main anomaly chokepoints (at the top)
    main_cp_rows = ""
    for a in anomalies:
        cp_name = a.get("chokepoint", "?").capitalize()
        drop = a.get("drop_pct")
        pct_color = _RED if (drop or 0) < 0 else _GREEN
        extra = ""
        if cp_name.lower() == "hormuz" and cape_pct is not None:
            extra = f' <span style="color:{_MUTED};font-size:13px">&middot; Cape rerouting {cape_pct:.1f}%</span>'
        main_cp_rows += (
            "<tr>"
            f'<td style="padding:3px 0;color:{_ACCENT};font-weight:600;font-size:15px">{cp_name}</td>'
            f'<td style="padding:3px 0;text-align:right;font-size:15px">'
            f'<span style="color:{pct_color};font-weight:700">{_pct(drop)} vs 30d avg</span>'
            f"{extra}"
            "</td></tr>"
        )

    all_cp_rows = main_cp_rows + cp_rows_html
    chokepoint_html = ""
    if all_cp_rows:
        chokepoint_html = (
            '<table width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;font-family:{_FONT}">'
            f"{all_cp_rows}</table>"
        )

    # ── Houston Tanker Activity Context (Wednesdays only, ahead of EIA release) ──
    eia_html = ""
    eia = data.get("eia_prediction")
    if eia:
        change_pct = eia.get("tanker_change_pct")
        if change_pct is None:
            change_color = _MUTED
        elif change_pct >= 5:
            change_color = _RED
        elif change_pct <= -5:
            change_color = _GREEN
        else:
            change_color = _TEXT
        details_parts = []
        if eia.get("tanker_count") is not None and eia.get("tanker_count_30d_avg") is not None:
            details_parts.append(f"Houston tankers: {eia['tanker_count']} vs {eia['tanker_count_30d_avg']:.0f} (30d avg)")
        if eia.get("pearson_r") is not None:
            details_parts.append(f"observed Pearson r={eia['pearson_r']:.2f}")
        details = " · ".join(details_parts) if details_parts else "—"
        headline = _pct(change_pct) if change_pct is not None else "no data"

        eia_html = (
            _DIVIDER + '<div style="margin-bottom:4px">'
            f'<span style="display:inline-block;background:{_AMBER};color:#1a1a2e;font-size:11px;'
            f"font-weight:600;letter-spacing:1px;text-transform:uppercase;"
            f'padding:3px 8px;border-radius:4px;vertical-align:middle">EIA RELEASE TODAY</span>'
            f'<span style="font-size:15px;color:{_TEXT};margin-left:10px;vertical-align:middle">'
            f'Houston tanker activity: <span style="color:{change_color};font-weight:700">{headline}</span> vs 30d avg</span>'
            "</div>"
            f'<div style="font-size:13px;color:{_MUTED};line-height:1.5">{details} · informational context, not a forecast</div>'
        )

    # ── Equity Movers (1 line) ──
    equity_html = ""
    em = data.get("equity_movers")
    if em and em.get("top_movers"):
        movers = []
        for m in em["top_movers"]:
            color = _GREEN if m["change_pct"] >= 0 else _RED
            sign = "+" if m["change_pct"] >= 0 else ""
            movers.append(
                f'<span style="font-weight:600">{m["ticker"]}</span> '
                f'<span style="color:{color};font-weight:700">{sign}{m["change_pct"]:.1f}%</span>'
            )
        corr_str = ""
        hc = em.get("highest_corr")
        if hc:
            corr_str = f'<span style="color:{_MUTED}"> | WTI corr: {hc["ticker"]} (r={hc["corr"]:.2f})</span>'
        equity_html = (
            _DIVIDER + f'<div style="font-size:15px;color:{_TEXT}">'
            f"{' &nbsp;&middot;&nbsp; '.join(movers)}{corr_str}</div>"
        )

    # ── Floating Storage (only if > 0) ──
    storage_html = ""
    storage_count, storage_vlcc = _get_floating_storage_count()
    if storage_count > 0:
        vlcc_note = f" ({storage_vlcc} VLCC)" if storage_vlcc > 0 else ""
        storage_html = (
            f'<div style="font-size:15px;color:{_TEXT};margin-top:12px">'
            f"Floating storage: "
            f'<span style="color:{_AMBER};font-weight:600">{storage_count} vessels</span>{vlcc_note}</div>'
        )

    # ── Assemble ──
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{_BG_OUTER};font-family:{_FONT};color:{_TEXT}">
<div style="max-width:580px;margin:0 auto;padding:24px 16px">

<div style="background:{_BG_CARD};border:1px solid {_BORDER};border-radius:8px;padding:28px">

<div style="font-size:28px;font-weight:700;color:{_ACCENT};margin-bottom:2px">OBSYD</div>
<div style="font-size:14px;color:{_MUTED};margin-bottom:20px">{date_str}</div>
<div style="border-top:1px solid {_BORDER};margin-bottom:20px"></div>

<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-family:{_FONT}">
{price_rows}
</table>
<div style="font-size:13px;color:{_MUTED};line-height:1.5">{futures_line}</div>

{physical_html}

{power_html}

{{{{watch_block}}}}

{disruption_html}

{chokepoint_html}

{eia_html}

{equity_html}

{storage_html}

<div style="border-top:1px solid {_BORDER};margin-top:24px;padding-top:16px">
<a href="https://obsyd.dev" style="color:{_ACCENT};text-decoration:none;font-size:14px;font-weight:600">View full dashboard &#8594;</a>
</div>

</div>

<div style="font-size:12px;color:#6b7280;margin-top:16px;line-height:1.6;text-align:center">
You subscribed to OBSYD Daily Briefing.<br>
<a href="https://obsyd.dev/api/email/unsubscribe?token={{{{token}}}}" style="color:{_MUTED};text-decoration:underline">Unsubscribe</a>
</div>

</div>
</body>
</html>"""
