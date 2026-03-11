"""
Daily Briefing Email — compact morning briefing for all subscribers.

Scheduled daily at 07:00 UTC via APScheduler.
Uses Resend API (free tier: 100 emails/day, capped at 95 for safety).
"""

import logging
import secrets
from datetime import datetime, timezone

import httpx

from backend.config import settings
from backend.database import SessionLocal
from backend.models.pro_features import (
    CrackSpreadHistory,
    EmailSubscriber,
    EquitySnapshot,
    STSEvent,
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
    """Main entry point called by scheduler at 07:00 UTC."""
    global _limit_warning_sent

    api_key = settings.resend_api_key
    if not api_key:
        logger.warning("Daily email: RESEND_API_KEY not configured, skipping")
        return

    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()

    db = SessionLocal()
    try:
        # Sync Pro subscribers to EmailSubscriber table
        _sync_pro_subscribers(db)

        subscribers = (
            db.query(EmailSubscriber)
            .filter(EmailSubscriber.active == True)  # noqa: E712
            .all()
        )

        # Also include legacy waitlist subscribers
        waitlist_subs = (
            db.query(Waitlist)
            .filter(Waitlist.subscribed == True)  # noqa: E712
            .all()
        )
        # Deduplicate by email
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
        pro_data = _gather_pro_data(db, crack)
        subject = _build_subject_line(briefing, rerouting, crack)
        html_template = _build_full_html(briefing, rerouting, crack, pro_data)

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

        # Legacy waitlist subscribers
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


async def _send_via_resend(
    api_key: str,
    to_email: str,
    subject: str,
    html: str,
    unsubscribe_token: str,
):
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


# ─── Formatting helpers ─────────────────────────────────────────────────────


def _safe(val, fmt=".2f", fallback="---"):
    """Format a number safely."""
    if val is None:
        return fallback
    try:
        return f"{val:{fmt}}"
    except (TypeError, ValueError):
        return fallback


def _pct(val, fallback="---"):
    """Format percentage with sign."""
    if val is None:
        return fallback
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def _build_subject_line(briefing: dict, rerouting: dict, crack: dict) -> str:
    """Dynamic subject: date + WTI + top signal."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%d %b")

    parts = [f"OBSYD Daily -- {date_str}"]

    # WTI price
    market = briefing.get("market_snapshot", {})
    wti = market.get("wti", {})
    if wti.get("price") is not None:
        parts.append(f"WTI ${_safe(wti['price'])}")

    # Crack spread
    if crack and not crack.get("error"):
        spread = crack.get("spread_321")
        if spread is not None:
            parts.append(f"3-2-1 ${_safe(spread)}")

    # Top anomaly
    anomalies = briefing.get("anomalies", [])
    if anomalies:
        top = anomalies[0]
        parts.append(f"{top['chokepoint'].capitalize()} {_pct(top.get('drop_pct'))}")

    return " | ".join(parts)


def _get_floating_storage_count() -> tuple[int, int]:
    """Query actual floating storage vessels from FloatingStorageEvent table."""
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


# ─── Data helpers ────────────────────────────────────────────────────────────


def _gather_pro_data(db, crack: dict) -> dict:
    """Gather supplementary data with per-section error handling."""
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
        data["sts_activity"] = _get_sts_activity(db)
    except Exception as e:
        logger.warning("Email STS activity failed: %s", e)

    try:
        data["chokepoint_detail"] = _get_chokepoint_detail()
    except Exception as e:
        logger.warning("Email chokepoint detail failed: %s", e)

    return data


def _get_crack_analysis(db, crack: dict) -> dict | None:
    """Enriched crack spread: 7d/30d avg comparison + percentile."""
    if not crack or crack.get("error"):
        return None
    s321 = crack.get("spread_321")
    if s321 is None:
        return None

    recent = db.query(CrackSpreadHistory).order_by(CrackSpreadHistory.date.desc()).limit(30).all()

    avg_7d = None
    if len(recent) >= 7:
        avg_7d = sum(r.three_two_one_crack for r in recent[:7]) / 7

    avg_30d = crack.get("avg_30d")
    percentile = crack.get("percentile_1y")

    pct_vs_30d = None
    if avg_30d and avg_30d != 0:
        pct_vs_30d = ((s321 - avg_30d) / avg_30d) * 100

    pct_vs_7d = None
    if avg_7d and avg_7d != 0:
        pct_vs_7d = ((s321 - avg_7d) / avg_7d) * 100

    return {
        "spread": s321,
        "avg_7d": round(avg_7d, 2) if avg_7d else None,
        "avg_30d": avg_30d,
        "percentile": percentile,
        "pct_vs_7d": round(pct_vs_7d, 1) if pct_vs_7d is not None else None,
        "pct_vs_30d": round(pct_vs_30d, 1) if pct_vs_30d is not None else None,
    }


def _get_equity_movers(db) -> dict | None:
    """Top 3 movers + highest WTI correlation from latest EquitySnapshot."""
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

    top3 = [{"ticker": s.ticker, "change_pct": s.change_pct, "price": s.price} for s in movers[:3]]

    corr_candidates = [s for s in snapshots if s.wti_corr_30d is not None]
    best_corr = max(corr_candidates, key=lambda s: abs(s.wti_corr_30d)) if corr_candidates else None

    return {
        "top_movers": top3,
        "highest_corr": {"ticker": best_corr.ticker, "corr": best_corr.wti_corr_30d} if best_corr else None,
    }


def _get_sts_activity(db) -> dict | None:
    """Active STS events by zone. Returns None if no events."""
    active = db.query(STSEvent).filter(STSEvent.status == "active").all()
    if not active:
        return None

    zone_counts: dict[str, int] = {}
    for e in active:
        zone = e.zone.replace("sts_", "").replace("_", " ").title()
        zone_counts[zone] = zone_counts.get(zone, 0) + 1

    top_zone = max(zone_counts.items(), key=lambda x: x[1])

    return {
        "total": len(active),
        "candidates": sum(1 for e in active if e.event_type == "candidate"),
        "pairs": sum(1 for e in active if e.event_type == "proximity"),
        "by_zone": zone_counts,
        "top_zone": top_zone[0],
        "top_zone_count": top_zone[1],
    }


def _get_chokepoint_detail() -> list[dict] | None:
    """All chokepoints with >10% deviation from 30d average."""
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
                        "name": result.get("chokepoint", cp).upper(),
                        "current": current.get("n_total", 0),
                        "avg_30d": round(current.get("avg_30d", 0)),
                        "drop_pct": round(drop, 1),
                    }
                )
        except Exception as e:  # noqa: PERF203
            logger.debug("Chokepoint detail for %s failed: %s", cp, e)
            continue

    return details if details else None


# ─── HTML Template ───────────────────────────────────────────────────────────

_DIVIDER = '<div style="border-top:1px solid #333;margin:16px 0"></div>'

# Shared inline style constants (email clients need inline styles everywhere)
_FONT = "-apple-system,'Segoe UI',Arial,sans-serif"
_BG = "#1a1a2e"
_TEXT = "#e0e0e0"
_MUTED = "#888"
_ACCENT = "#00d4aa"
_GREEN = "#00cc66"
_RED = "#ff4444"
_ORANGE = "#ff8800"


def _build_full_html(briefing: dict, rerouting: dict, crack: dict, pro_data: dict) -> str:
    """Build compact Bloomberg-style daily briefing email."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%A, %d %B %Y")

    market = briefing.get("market_snapshot", {})
    mkt_struct = briefing.get("market_structure") or {}
    anomalies = briefing.get("anomalies", [])
    alerts_summary = briefing.get("alerts_summary", {})
    fleet = briefing.get("fleet_status", {})

    # --- Price grid (2 rows x 3 columns) ---
    def _pcell(key, label):
        p = market.get(key, {})
        price = p.get("price")
        change = p.get("change_pct")
        td = f'style="padding:5px 0;font-size:13px;font-family:{_FONT};width:33%"'
        if price is None:
            return f'<td {td}><span style="color:{_MUTED}">{label}</span> ---</td>'
        pfmt = f"${price:,.0f}" if price >= 100 else f"${price:.2f}"
        chg = ""
        if change is not None:
            arrow = "&#9650;" if change >= 0 else "&#9660;"
            c = _GREEN if change >= 0 else _RED
            chg = f' <span style="color:{c};font-size:11px">{arrow}{abs(change):.1f}%</span>'
        return (
            f"<td {td}>"
            f'<span style="color:{_MUTED}">{label}</span> '
            f'<span style="color:#fff;font-weight:600">{pfmt}</span>{chg}</td>'
        )

    price_rows = ""
    for row in [
        [("wti", "WTI"), ("brent", "BRENT"), ("ng", "NG")],
        [("ttf", "TTF"), ("gold", "GOLD"), ("copper", "COPPER")],
    ]:
        cells = "".join(_pcell(k, lbl) for k, lbl in row)
        price_rows += f"<tr>{cells}</tr>"

    # --- Futures + Crack one-liner ---
    summary = mkt_struct.get("summary", "unavailable")
    wti_spread = mkt_struct.get("curves", {}).get("WTI", {}).get("spread_pct")
    futures_str = summary.capitalize()
    if wti_spread is not None:
        futures_str += f" ({_pct(wti_spread)})"

    crack_str = ""
    ca = pro_data.get("crack_analysis") or {}
    if crack and not crack.get("error"):
        s321 = crack.get("spread_321")
        if s321 is not None:
            pct_parts: list[str] = []
            pctile = ca.get("percentile") or crack.get("percentile_1y")
            pct_vs_30d = ca.get("pct_vs_30d")
            if pctile is not None:
                pct_parts.append(f"{pctile}th pct")
            if pct_vs_30d is not None:
                c = _GREEN if pct_vs_30d >= 0 else _RED
                pct_parts.append(f'<span style="color:{c}">{_pct(pct_vs_30d)} vs 30d</span>')
            crack_str = (
                f' &middot; 3-2-1 Crack: <span style="color:{_ACCENT};font-weight:600">${_safe(s321)}/bbl</span>'
            )
            if pct_parts:
                crack_str += f" ({', '.join(pct_parts)})"

    futures_line = f'Futures: <span style="color:{_TEXT}">{futures_str}</span>{crack_str}'

    # --- Disruption section (only when active) ---
    disruption_html = ""
    cd = pro_data.get("chokepoint_detail")
    r_current = rerouting.get("current", {}) if rerouting.get("available") else {}
    cape_pct = r_current.get("ratio_pct")

    dlines: list[str] = []
    shown: set[str] = set()

    for a in anomalies:
        cp = a.get("chokepoint", "?").capitalize()
        shown.add(cp.upper())
        current = a.get("current_value", "?")
        drop = a.get("drop_pct")
        c = _RED if a.get("severity") == "critical" else _ORANGE
        drop_s = f"{_pct(drop)} vs 30d" if drop is not None else ""
        line = f'<span style="color:{c}">{cp}: {current} vessels ({drop_s})</span>'
        if cp.lower() == "hormuz" and cape_pct is not None:
            line += f" &middot; Cape rerouting: {cape_pct:.1f}%"
        dlines.append(line)

    if cd:
        for d in cd:
            if d["name"] in shown:
                continue
            c = (
                _RED
                if d["drop_pct"] < -25
                else _ORANGE
                if d["drop_pct"] < -10
                else _GREEN
                if d["drop_pct"] > 10
                else _MUTED
            )
            name = d["name"].replace("STRAIT OF ", "").replace(" STRAIT", "").title()
            dlines.append(
                f'<span style="color:{c}">{name}: {d["current"]} vessels ({_pct(d["drop_pct"])} vs 30d)</span>'
            )

    if dlines:
        disruption_html = (
            _DIVIDER + '<div style="margin-bottom:6px">'
            f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
            f'background:{_RED};margin-right:6px;vertical-align:middle"></span>'
            f'<span style="color:{_RED};font-size:11px;font-weight:600;letter-spacing:0.5px;'
            f'vertical-align:middle">ACTIVE DISRUPTION</span></div>'
            f'<div style="font-size:13px;color:{_TEXT};line-height:1.8">' + "<br>".join(dlines) + "</div>"
        )

    # --- Fleet & Signals (2 compact lines) ---
    total_vessels = fleet.get("total_vessels_global", 0)
    tankers = fleet.get("tankers_global", 0)
    storage_vessels, _ = _get_floating_storage_count()
    total_24h = alerts_summary.get("total_24h", 0)
    sentiment_score = market.get("sentiment_score")

    fleet_line = (
        f"Fleet: {total_vessels:,} vessels &middot; {tankers:,} tankers &middot; Floating storage: {storage_vessels}"
    )

    sig_parts: list[str] = []
    if total_24h > 0:
        sig_parts.append(f"Alerts: {total_24h} active")
    if sentiment_score is not None:
        sc = _RED if sentiment_score >= 7 else _ORANGE if sentiment_score >= 4 else _GREEN
        sig_parts.append(f'Risk score: <span style="color:{sc};font-weight:600">{sentiment_score:.1f}/10</span>')
    signals_line = " &middot; ".join(sig_parts)

    fleet_html = _DIVIDER + f'<div style="font-size:13px;color:{_TEXT};line-height:1.8">{fleet_line}</div>'
    if signals_line:
        fleet_html += f'<div style="font-size:13px;color:{_TEXT};line-height:1.8">{signals_line}</div>'

    # --- Equity Movers (1 line) ---
    equity_html = ""
    em = pro_data.get("equity_movers")
    if em and em.get("top_movers"):
        movers = []
        for m in em["top_movers"]:
            c = _GREEN if m["change_pct"] >= 0 else _RED
            sign = "+" if m["change_pct"] >= 0 else ""
            movers.append(f'<span style="color:{c}">{m["ticker"]} {sign}{m["change_pct"]:.1f}%</span>')
        corr_str = ""
        hc = em.get("highest_corr")
        if hc:
            corr_str = f" | WTI corr: {hc['ticker']} (r={hc['corr']:.2f})"
        equity_html = (
            _DIVIDER + f'<div style="font-size:13px;color:{_TEXT}">Movers: {" &middot; ".join(movers)}{corr_str}</div>'
        )

    # --- STS Activity (1 line, conditional) ---
    sts_html = ""
    sa = pro_data.get("sts_activity")
    if sa and sa.get("candidates", 0) > 0:
        top = f" &middot; {sa['top_zone']} ({sa['top_zone_count']})" if sa.get("top_zone") else ""
        sts_html = (
            _DIVIDER + f'<div style="font-size:13px;color:{_TEXT}">'
            f"STS: {sa['candidates']} candidates &middot; {sa['pairs']} proximity pairs{top}</div>"
        )

    # --- Assemble final HTML ---
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#111;font-family:{_FONT};color:{_TEXT}">
<div style="max-width:600px;margin:0 auto;padding:24px 16px">
<div style="background:{_BG};border-radius:4px;padding:28px 24px">

<div style="font-size:24px;font-weight:700;color:{_ACCENT};margin-bottom:2px">OBSYD</div>
<div style="font-size:14px;color:{_MUTED};margin-bottom:16px">{date_str}</div>
<div style="border-top:1px solid #333;margin-bottom:16px"></div>

<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-family:{_FONT}">
{price_rows}
</table>
<div style="font-size:12px;color:{_MUTED};margin-top:10px;line-height:1.5">{futures_line}</div>

{disruption_html}

{fleet_html}

{equity_html}

{sts_html}

<div style="border-top:1px solid #333;margin-top:20px;padding-top:16px">
<a href="https://obsyd.dev" style="color:{_ACCENT};text-decoration:none;font-size:14px;font-weight:600">View full dashboard &#8594;</a>
</div>

</div>

<div style="font-size:12px;color:#666;margin-top:16px;line-height:1.6;text-align:center">
You subscribed to OBSYD Daily Briefing.<br>
<a href="https://obsyd.dev/api/email/unsubscribe?token={{{{token}}}}" style="color:{_MUTED};text-decoration:underline">Unsubscribe</a>
&middot;
<a href="https://obsyd.dev" style="color:{_MUTED};text-decoration:underline">Manage subscription</a>
</div>

</div>
</body>
</html>"""
