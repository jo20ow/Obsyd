"""
Daily Briefing Email — comprehensive morning briefing for Pro subscribers.

Scheduled daily at 07:00 UTC via APScheduler.
Uses Resend API (free tier: 100 emails/day, capped at 95 for safety).
"""

import logging
import secrets
from datetime import datetime, timezone

import httpx

from backend.config import settings
from backend.database import SessionLocal
from backend.models.pro_features import EmailSubscriber
from backend.models.subscription import Subscription
from backend.models.vessels import FloatingStorageEvent
from backend.models.waitlist import Waitlist
from backend.routes.briefing import _build_briefing
from backend.signals.crack_spread import get_crack_spread
from backend.signals.tonnage_proxy import compute_rerouting_index

logger = logging.getLogger(__name__)

DAILY_SEND_LIMIT = 95


async def send_daily_email():
    """Main entry point called by scheduler at 07:00 UTC."""
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

        # Build data
        briefing = await _build_briefing()
        rerouting = compute_rerouting_index(days=365)
        crack = await get_crack_spread()
        subject = _build_subject_line(briefing, rerouting, crack)
        html_template = _build_full_html(briefing, rerouting, crack)

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


def _fmt_mcap(val):
    """Format market cap in human-readable form."""
    if val is None:
        return "---"
    if val >= 1e12:
        return f"${val / 1e12:.1f}T"
    if val >= 1e9:
        return f"${val / 1e9:.0f}B"
    if val >= 1e6:
        return f"${val / 1e6:.0f}M"
    return f"${val:,.0f}"


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
    """Query actual floating storage vessels from FloatingStorageEvent table.

    Returns (total_active, vlcc_count).
    """
    db = SessionLocal()
    try:
        active = db.query(FloatingStorageEvent).filter(FloatingStorageEvent.status == "active").all()
        total = len(active)
        # VLCC: ship_type 80-89 with "VLCC" in name or large vessel indicators
        vlcc = sum(1 for e in active if e.ship_name and "vlcc" in e.ship_name.lower())
        return total, vlcc
    except Exception:
        return 0, 0
    finally:
        db.close()


def _build_full_html(briefing: dict, rerouting: dict, crack: dict) -> str:
    """Build the comprehensive email HTML body."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%d %b %Y").upper()

    market = briefing.get("market_snapshot", {})
    mkt_struct = briefing.get("market_structure") or {}
    anomalies = briefing.get("anomalies", [])
    alerts_summary = briefing.get("alerts_summary", {})
    fleet = briefing.get("fleet_status", {})

    # === MARKET SNAPSHOT ===
    price_rows = []
    for key, label in [("wti", "WTI"), ("brent", "BRENT"), ("ng", "NG"), ("ttf", "TTF"), ("gold", "GOLD")]:
        p = market.get(key, {})
        price = p.get("price")
        change = p.get("change_pct")
        price_str = f"${_safe(price)}" if price is not None else "---"
        change_str = _pct(change) if change is not None else ""
        color = "#34d399" if (change or 0) >= 0 else "#f87171"
        price_rows.append(
            f'<tr><td style="padding:2px 12px 2px 0;color:#737373">{label}</td>'
            f'<td style="padding:2px 12px 2px 0;color:#e5e5e5;font-weight:bold">{price_str}</td>'
            f'<td style="padding:2px 0;color:{color}">{change_str}</td></tr>'
        )

    # Futures curve
    summary = mkt_struct.get("summary", "unavailable")
    wti_spread = mkt_struct.get("curves", {}).get("WTI", {}).get("spread_pct")
    curve_str = summary.upper()
    if wti_spread is not None:
        curve_str += f" ({_pct(wti_spread)})"

    # Crack spread
    crack_str = ""
    if crack and not crack.get("error"):
        s321 = crack.get("spread_321")
        avg30 = crack.get("avg_30d")
        if s321 is not None:
            diff = ""
            if avg30:
                d = s321 - avg30
                diff = f' <span style="color:{"#34d399" if d >= 0 else "#f87171"}">({_pct((d / avg30) * 100)} vs 30d)</span>'
            crack_str = f'3-2-1 Crack: <span style="color:#22d3ee;font-weight:bold">${_safe(s321)}/bbl</span>{diff}'

    # === AIS OVERVIEW ===
    total_vessels = fleet.get("total_vessels_global", 0)
    tankers = fleet.get("tankers_global", 0)

    # Zone counts from anomalies
    zone_parts = []
    for a in anomalies:
        zone_parts.append(f"{a['chokepoint'].capitalize()} {a.get('current_value', '?')}")

    # === CHOKEPOINTS ===
    cp_rows = []
    for a in anomalies:
        cp_name = a.get("chokepoint", "?").upper()
        current = a.get("current_value", "?")
        avg = a.get("average_30d", "?")
        drop = a.get("drop_pct")
        drop_str = _pct(drop) if drop is not None else ""
        severity = a.get("severity", "info")
        color = "#f87171" if severity == "critical" else "#fb923c" if severity == "warning" else "#737373"
        cp_rows.append(
            f'<tr><td style="padding:2px 12px 2px 0;color:{color};font-weight:bold">{cp_name}</td>'
            f'<td style="padding:2px 12px 2px 0;color:#e5e5e5">{current} transits (avg {avg})</td>'
            f'<td style="padding:2px 0;color:{color}">{drop_str}</td></tr>'
        )

    # === SIGNALS ===
    signals_parts = []
    total_24h = alerts_summary.get("total_24h", 0)
    if total_24h > 0:
        by_rule = alerts_summary.get("last_24h", {})
        rule_parts = [f"{rule}: {count}" for rule, count in sorted(by_rule.items(), key=lambda x: -x[1])[:3]]
        signals_parts.append(f"Active Alerts: {total_24h} ({', '.join(rule_parts)})")

    # Rerouting
    r_current = rerouting.get("current", {}) if rerouting.get("available") else {}
    ratio_pct = r_current.get("ratio_pct")
    state = r_current.get("state", "")
    if ratio_pct is not None:
        state_color = "#f87171" if state == "elevated" else "#34d399" if state == "normal" else "#fb923c"
        signals_parts.append(f'Cape Share: {ratio_pct:.1f}% (<span style="color:{state_color}">{state.upper()}</span>)')

    # Sentiment
    sentiment_score = market.get("sentiment_score")
    if sentiment_score is not None:
        score_color = "#f87171" if sentiment_score >= 7 else "#fb923c" if sentiment_score >= 4 else "#34d399"
        signals_parts.append(
            f'Risk Score: <span style="color:{score_color};font-weight:bold">{sentiment_score:.1f}/10</span>'
        )

    signals_html = (
        "<br>".join(f'<span style="color:#a3a3a3">{s}</span>' for s in signals_parts)
        if signals_parts
        else '<span style="color:#404040">No active signals</span>'
    )

    # === FLOATING STORAGE (direct query, not from alerts) ===
    storage_vessels, storage_vlcc = _get_floating_storage_count()

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#09090b;font-family:'Courier New',Courier,monospace;font-size:13px;color:#d4d4d4">
<div style="max-width:560px;margin:0 auto;padding:24px 16px">

<div style="border:1px solid #27272a;padding:20px;background:#0a0a12">

<div style="font-size:11px;color:#22d3ee;font-weight:bold;letter-spacing:2px;margin-bottom:4px">
  OBSYD DAILY BRIEFING
</div>
<div style="font-size:10px;color:#404040;margin-bottom:16px">{date_str}</div>

<!-- MARKET SNAPSHOT -->
<div style="font-size:10px;color:#404040;letter-spacing:1.5px;margin-bottom:8px;border-bottom:1px solid #1a1a2e;padding-bottom:4px">MARKET SNAPSHOT</div>
<table style="border-collapse:collapse;font-family:'Courier New',Courier,monospace;font-size:12px;margin-bottom:8px">
  {"".join(price_rows)}
</table>
<div style="font-size:11px;color:#737373;margin-bottom:4px">Futures Curve: <span style="color:#e5e5e5">{curve_str}</span></div>
{f'<div style="font-size:11px;color:#737373;margin-bottom:12px">{crack_str}</div>' if crack_str else '<div style="margin-bottom:12px"></div>'}

<!-- AIS OVERVIEW -->
<div style="font-size:10px;color:#404040;letter-spacing:1.5px;margin-bottom:8px;border-bottom:1px solid #1a1a2e;padding-bottom:4px">AIS OVERVIEW</div>
<div style="font-size:11px;color:#a3a3a3;margin-bottom:4px">
  Global Fleet: <span style="color:#e5e5e5;font-weight:bold">{total_vessels:,}</span> vessels &middot;
  <span style="color:#e5e5e5;font-weight:bold">{tankers:,}</span> tankers
</div>
{f'<div style="font-size:11px;color:#737373;margin-bottom:4px">Zones: {" | ".join(zone_parts)}</div>' if zone_parts else ""}
{f'<div style="font-size:11px;color:#737373;margin-bottom:12px">Floating Storage: <span style="color:#fb923c;font-weight:bold">{storage_vessels} vessels</span>{f" ({storage_vlcc} VLCCs)" if storage_vlcc > 0 else ""}</div>' if storage_vessels > 0 else '<div style="margin-bottom:12px"></div>'}

<!-- DISRUPTIONS / CHOKEPOINTS -->
{'<div style="font-size:10px;color:#404040;letter-spacing:1.5px;margin-bottom:8px;border-bottom:1px solid #1a1a2e;padding-bottom:4px">ACTIVE DISRUPTIONS</div><table style="border-collapse:collapse;font-family:Courier New,Courier,monospace;font-size:12px;margin-bottom:12px">' + "".join(cp_rows) + "</table>" if cp_rows else '<div style="font-size:11px;color:#34d399;margin-bottom:12px">All chokepoints within normal range</div>'}

<!-- SIGNALS -->
<div style="font-size:10px;color:#404040;letter-spacing:1.5px;margin-bottom:8px;border-bottom:1px solid #1a1a2e;padding-bottom:4px">SIGNALS</div>
<div style="font-size:11px;line-height:1.8;margin-bottom:16px">
  {signals_html}
</div>

<!-- FOOTER -->
<div style="border-top:1px solid #27272a;padding-top:12px;margin-top:8px;display:flex;gap:16px">
  <a href="https://obsyd.dev" style="color:#22d3ee;text-decoration:none;font-size:11px;letter-spacing:1px">
    VIEW FULL DASHBOARD &rarr;
  </a>
</div>

</div>

<div style="font-size:9px;color:#404040;margin-top:16px;line-height:1.6;text-align:center">
  You're receiving this as an OBSYD Pro subscriber<br>
  <a href="https://obsyd.dev/api/email/unsubscribe?token={{{{token}}}}" style="color:#525252">Unsubscribe</a>
  &middot;
  <a href="https://obsyd.dev" style="color:#525252">Manage subscription</a>
</div>

</div>
</body>
</html>"""
