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
from backend.models.analytics import (
    DisruptionScoreHistory,
    EIAPredictionHistory,
    MarketReport,
)
from backend.models.pro_features import (
    EmailSubscriber,
    EquitySnapshot,
)
from backend.models.subscription import Subscription
from backend.models.vessels import FloatingStorageEvent
from backend.models.waitlist import Waitlist
from backend.routes.briefing import _build_briefing
from backend.signals.crack_spread import get_crack_spread
from backend.signals.tonnage_proxy import compute_rerouting_index

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

        subscribers = (
            db.query(EmailSubscriber)
            .filter(EmailSubscriber.active == True)  # noqa: E712
            .all()
        )
        waitlist_subs = (
            db.query(Waitlist)
            .filter(Waitlist.subscribed == True)  # noqa: E712
            .all()
        )
        sub_emails = {s.email for s in subscribers}
        legacy_only = [w for w in waitlist_subs if w.email not in sub_emails]

        if not subscribers and not legacy_only:
            logger.info("Daily email: no subscribers, skipping")
            return

        # --- Resend free tier safety check ---
        total_recipients = len(subscribers) + len(legacy_only)

        if total_recipients > RESEND_FREE_TIER_LIMIT:
            logger.error(
                "Resend free tier limit exceeded (%d/%d subscribers), skipping daily briefing",
                total_recipients,
                RESEND_FREE_TIER_LIMIT,
            )
            return

        if total_recipients > LIMIT_WARN_THRESHOLD:
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
        subject = _build_subject_line(briefing, rerouting, crack)
        html_template = _build_full_html(briefing, rerouting, crack, email_data)

        sent = 0
        for sub in subscribers:
            if sent >= DAILY_SEND_LIMIT:
                break
            html = html_template.replace("{{email}}", sub.email).replace("{{token}}", sub.unsubscribe_token or "")
            try:
                await _send_via_resend(api_key, sub.email, subject, html, sub.unsubscribe_token or "")
                sent += 1
            except Exception as e:
                logger.error("Daily email failed for %s: %s", sub.email, e)

        for w in legacy_only:
            if sent >= DAILY_SEND_LIMIT:
                break
            html = html_template.replace("{{email}}", w.email).replace("{{token}}", w.unsubscribe_token or "")
            try:
                await _send_via_resend(api_key, w.email, subject, html, w.unsubscribe_token or "")
                sent += 1
            except Exception as e:
                logger.error("Daily email failed for %s: %s", w.email, e)

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


def _build_subject_line(briefing: dict, rerouting: dict, crack: dict) -> str:
    now = datetime.now(timezone.utc)
    parts = [f"OBSYD Daily -- {now.strftime('%d %b')}"]

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

    # ── EIA Prediction (Wednesdays only) ──
    eia_html = ""
    eia = data.get("eia_prediction")
    if eia:
        prediction = eia["prediction"]
        pred_color = _RED if prediction == "BUILD" else _GREEN if prediction == "DRAW" else _MUTED
        details_parts = []
        if eia.get("tanker_count") is not None and eia.get("tanker_count_30d_avg") is not None:
            details_parts.append(f"Houston tankers: {eia['tanker_count']} vs {eia['tanker_count_30d_avg']:.0f} avg")
            if eia.get("tanker_change_pct") is not None:
                details_parts.append(f"({_pct(eia['tanker_change_pct'])})")
        if eia.get("pearson_r") is not None:
            details_parts.append(f"r={eia['pearson_r']:.2f}")
        details = " ".join(details_parts)

        eia_html = (
            _DIVIDER + '<div style="margin-bottom:4px">'
            f'<span style="display:inline-block;background:{_AMBER};color:#1a1a2e;font-size:11px;'
            f"font-weight:600;letter-spacing:1px;text-transform:uppercase;"
            f'padding:3px 8px;border-radius:4px;vertical-align:middle">EIA TODAY</span>'
            f'<span style="font-size:15px;color:{_TEXT};margin-left:10px;vertical-align:middle">'
            f'AIS prediction: <span style="color:{pred_color};font-weight:700">{prediction}</span></span>'
            "</div>"
            f'<div style="font-size:13px;color:{_MUTED};line-height:1.5">{details}</div>'
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
