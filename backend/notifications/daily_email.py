"""
Daily Email Snapshot — sends morning briefing to all waitlist subscribers.

Scheduled daily at 06:45 UTC via APScheduler.
Uses Resend API (free tier: 100 emails/day, capped at 95 for safety).
"""

import logging
from datetime import datetime, timezone

import httpx

from backend.config import settings
from backend.database import SessionLocal
from backend.models.waitlist import Waitlist
from backend.routes.briefing import _build_briefing
from backend.signals.tonnage_proxy import compute_rerouting_index

logger = logging.getLogger(__name__)

DAILY_SEND_LIMIT = 95


async def send_daily_email():
    """Main entry point called by scheduler at 06:45 UTC."""
    api_key = settings.resend_api_key
    if not api_key:
        logger.warning("Daily email: RESEND_API_KEY not configured, skipping")
        return

    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()

    db = SessionLocal()
    try:
        subscribers = (
            db.query(Waitlist)
            .filter(Waitlist.subscribed == True)  # noqa: E712
            .all()
        )
        if not subscribers:
            logger.info("Daily email: no subscribers, skipping")
            return

        # Build data
        briefing = await _build_briefing()
        rerouting = compute_rerouting_index(days=365)
        subject = _build_subject(briefing, rerouting)
        html_template = _build_html(briefing, rerouting)

        sent = 0
        skipped = 0
        for sub in subscribers:
            if sent >= DAILY_SEND_LIMIT:
                skipped = len(subscribers) - sent
                logger.warning(
                    "Daily email limit reached, %d subscribers skipped",
                    skipped,
                )
                break

            # Personalize unsubscribe link per subscriber
            html = html_template.replace("{{email}}", sub.email).replace("{{token}}", sub.unsubscribe_token or "")

            try:
                await _send_via_resend(
                    api_key=api_key,
                    to_email=sub.email,
                    subject=subject,
                    html=html,
                    unsubscribe_token=sub.unsubscribe_token or "",
                )
                sent += 1
            except Exception as e:
                logger.error("Daily email failed for %s: %s", sub.email, e)

        logger.info("Daily email: sent %d, skipped %d", sent, skipped)
    except Exception as e:
        logger.error("Daily email build failed: %s", e)
    finally:
        db.close()


async def _send_via_resend(
    api_key: str,
    to_email: str,
    subject: str,
    html: str,
    unsubscribe_token: str,
):
    """Send a single email via Resend API."""
    unsubscribe_url = f"https://obsyd.dev/api/waitlist/unsubscribe?email={to_email}&token={unsubscribe_token}"
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
                },
            },
        )
        resp.raise_for_status()


def _safe(val, fmt=".2f", fallback="—"):
    """Format a number safely, return fallback if None."""
    if val is None:
        return fallback
    try:
        return f"{val:{fmt}}"
    except (TypeError, ValueError):
        return fallback


def _pct(val, fallback="—"):
    """Format percentage with sign."""
    if val is None:
        return fallback
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def _build_subject(briefing: dict, rerouting: dict) -> str:
    """Dynamic subject line: biggest anomaly + top price."""
    parts = []

    # Biggest anomaly
    anomalies = briefing.get("anomalies", [])
    if anomalies:
        top = anomalies[0]
        parts.append(f"{top['chokepoint'].capitalize()} {_pct(top.get('drop_pct'))}")

    # WTI price
    market = briefing.get("market_snapshot", {})
    wti = market.get("wti", {})
    if wti.get("price") is not None:
        parts.append(f"WTI ${_safe(wti['price'])}")

    # Rerouting
    current = rerouting.get("current", {}) if rerouting.get("available") else {}
    ratio_pct = current.get("ratio_pct")
    if ratio_pct is not None:
        parts.append(f"Rerouting {ratio_pct:.0f}%")

    return "OBSYD Daily: " + " | ".join(parts) if parts else "OBSYD Daily Snapshot"


def _build_html(briefing: dict, rerouting: dict) -> str:
    """Build the email HTML body. Monospace, inline CSS, email-safe."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%d %b %Y").upper()

    market = briefing.get("market_snapshot", {})
    mkt_struct = briefing.get("market_structure") or {}
    anomalies = briefing.get("anomalies", [])
    alerts_summary = briefing.get("alerts_summary", {})

    # Prices section
    price_rows = []
    for key, label in [("wti", "WTI"), ("brent", "BRENT"), ("ng", "NG"), ("gold", "GOLD")]:
        p = market.get(key, {})
        price = p.get("price")
        change = p.get("change_pct")
        price_str = f"${_safe(price)}" if price is not None else "—"
        change_str = _pct(change) if change is not None else ""
        color = "#34d399" if (change or 0) >= 0 else "#f87171"
        price_rows.append(
            f'<tr><td style="padding:2px 12px 2px 0;color:#a3a3a3">{label}</td>'
            f'<td style="padding:2px 12px 2px 0;color:#e5e5e5;font-weight:bold">{price_str}</td>'
            f'<td style="padding:2px 0;color:{color}">{change_str}</td></tr>'
        )

    # Chokepoint section
    cp_rows = []
    for a in anomalies:
        cp_name = a.get("chokepoint", "?").upper()
        current = a.get("current_value", "?")
        avg = a.get("average_30d", "?")
        drop = a.get("drop_pct")
        drop_str = _pct(drop) if drop is not None else ""
        severity = a.get("severity", "info")
        color = "#f87171" if severity == "critical" else "#fb923c" if severity == "warning" else "#a3a3a3"
        dot = "&#9679; " if severity == "critical" else ""
        cp_rows.append(
            f'<tr><td style="padding:2px 12px 2px 0;color:{color}">{dot}{cp_name}</td>'
            f'<td style="padding:2px 12px 2px 0;color:#e5e5e5">{current} transits (avg {avg})</td>'
            f'<td style="padding:2px 0;color:{color}">{drop_str}</td></tr>'
        )

    # Signals section
    signals_parts = []

    # Market structure
    summary = mkt_struct.get("summary", "unavailable")
    if summary != "unavailable":
        wti_spread = mkt_struct.get("curves", {}).get("WTI", {}).get("spread_pct")
        struct_str = summary.upper()
        if wti_spread is not None:
            struct_str += f" ({_pct(wti_spread)} WTI)"
        signals_parts.append(f"Market: {struct_str}")

    # Rerouting
    r_current = rerouting.get("current", {}) if rerouting.get("available") else {}
    ratio_pct = r_current.get("ratio_pct")
    state = r_current.get("state", "")
    if ratio_pct is not None:
        signals_parts.append(f"Rerouting: {ratio_pct:.0f}% Cape share ({state.upper()})")

    # Alerts count
    total_24h = alerts_summary.get("total_24h", 0)
    if total_24h > 0:
        signals_parts.append(f"Active Alerts: {total_24h}")

    signals_html = ""
    if signals_parts:
        signals_html = "<br>".join(f'<span style="color:#a3a3a3">{s}</span>' for s in signals_parts)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#09090b;font-family:'Courier New',Courier,monospace;font-size:13px;color:#d4d4d4">
<div style="max-width:560px;margin:0 auto;padding:24px 16px">

<div style="border:1px solid #27272a;padding:20px;background:#0a0a12">

<div style="font-size:11px;color:#22d3ee;font-weight:bold;letter-spacing:2px;margin-bottom:4px">
  OBSYD DAILY SNAPSHOT
</div>
<div style="font-size:10px;color:#525252;margin-bottom:16px">{date_str}</div>

<div style="font-size:10px;color:#525252;letter-spacing:1.5px;margin-bottom:8px">PRICES</div>
<table style="border-collapse:collapse;font-family:'Courier New',Courier,monospace;font-size:12px;margin-bottom:16px">
  {"".join(price_rows)}
</table>

{'<div style="font-size:10px;color:#525252;letter-spacing:1.5px;margin-bottom:8px">CHOKEPOINTS</div><table style="border-collapse:collapse;font-family:Courier New,Courier,monospace;font-size:12px;margin-bottom:16px">' + "".join(cp_rows) + "</table>" if cp_rows else '<div style="font-size:12px;color:#34d399;margin-bottom:16px">All chokepoints within normal range</div>'}

<div style="font-size:10px;color:#525252;letter-spacing:1.5px;margin-bottom:8px">SIGNALS</div>
<div style="font-size:12px;line-height:1.8;margin-bottom:16px">
  {signals_html or '<span style="color:#525252">No active signals</span>'}
</div>

<div style="border-top:1px solid #27272a;padding-top:12px;margin-top:8px">
  <a href="https://obsyd.dev" style="color:#22d3ee;text-decoration:none;font-size:11px;letter-spacing:1px">
    OPEN DASHBOARD &rarr;
  </a>
</div>

</div>

<div style="font-size:9px;color:#404040;margin-top:16px;line-height:1.6;text-align:center">
  You're receiving this because you signed up at obsyd.dev<br>
  <a href="https://obsyd.dev/api/waitlist/unsubscribe?email={{{{email}}}}&token={{{{token}}}}" style="color:#525252">Unsubscribe</a>
</div>

</div>
</body>
</html>"""
