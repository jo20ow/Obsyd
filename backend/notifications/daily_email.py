"""
Daily Briefing Email — comprehensive morning briefing for Pro subscribers.

Scheduled daily at 07:00 UTC via APScheduler.
Uses Resend API (free tier: 100 emails/day, capped at 95 for safety).

Pro-exclusive sections: Crack Spread Analysis, Equity Movers,
STS Activity, Chokepoint Detail, Weekly Context (Mondays).
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

import httpx

from backend.config import settings
from backend.database import SessionLocal
from backend.models.prices import FREDSeries
from backend.models.pro_features import (
    CrackSpreadHistory,
    EmailSubscriber,
    EquitySnapshot,
    STSEvent,
)
from backend.models.subscription import Subscription
from backend.models.vessels import FloatingStorageEvent, GeofenceEvent
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


# ─── Pro-exclusive data helpers ──────────────────────────────────────────────


def _gather_pro_data(db, crack: dict) -> dict:
    """Gather all Pro-exclusive data with per-section error handling."""
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

    if datetime.now(timezone.utc).weekday() == 0:
        try:
            data["weekly_context"] = _get_weekly_context(db)
        except Exception as e:
            logger.warning("Email weekly context failed: %s", e)

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
    highlight = False
    if avg_30d and avg_30d != 0:
        pct_vs_30d = ((s321 - avg_30d) / avg_30d) * 100
        highlight = abs(pct_vs_30d) > 10

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
        "highlight": highlight,
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


def _get_weekly_context(db) -> dict | None:
    """Monday-only: zone WoW, Cushing stocks, crack spread trend."""
    now = datetime.now(timezone.utc)
    context: dict = {}

    # Zone tanker counts: this week vs last week
    this_week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    last_week_start = (now - timedelta(days=14)).strftime("%Y-%m-%d")

    this_week = db.query(GeofenceEvent).filter(GeofenceEvent.date >= this_week_start).all()
    last_week = (
        db.query(GeofenceEvent)
        .filter(
            GeofenceEvent.date >= last_week_start,
            GeofenceEvent.date < this_week_start,
        )
        .all()
    )

    zone_this: dict[str, list] = {}
    for e in this_week:
        zone_this.setdefault(e.zone, []).append(e.tanker_count)
    zone_last: dict[str, list] = {}
    for e in last_week:
        zone_last.setdefault(e.zone, []).append(e.tanker_count)

    zone_changes = []
    for zone in set(list(zone_this.keys()) + list(zone_last.keys())):
        vals_this = zone_this.get(zone, [0])
        vals_last = zone_last.get(zone, [0])
        avg_this = sum(vals_this) / max(len(vals_this), 1)
        avg_last = sum(vals_last) / max(len(vals_last), 1)
        if avg_last > 0:
            change_pct = ((avg_this - avg_last) / avg_last) * 100
            if abs(change_pct) > 5:
                zone_changes.append(
                    {
                        "zone": zone.replace("_", " ").title(),
                        "change_pct": round(change_pct, 1),
                    }
                )

    if zone_changes:
        zone_changes.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
        context["zone_changes"] = zone_changes[:5]

    # EIA Cushing stocks (FRED: WCUSTUS1, thousands of barrels)
    cushing = (
        db.query(FREDSeries).filter(FREDSeries.series_id == "WCUSTUS1").order_by(FREDSeries.date.desc()).limit(2).all()
    )
    if len(cushing) >= 2:
        delta = cushing[0].value - cushing[1].value
        context["cushing"] = {
            "current": cushing[0].value,
            "delta": delta,
        }

    # Crack spread week-over-week
    crack_hist = db.query(CrackSpreadHistory).order_by(CrackSpreadHistory.date.desc()).limit(14).all()
    if len(crack_hist) >= 14:
        this_avg = sum(r.three_two_one_crack for r in crack_hist[:7]) / 7
        last_avg = sum(r.three_two_one_crack for r in crack_hist[7:14]) / 7
        context["crack_trend"] = {
            "this_week": round(this_avg, 2),
            "last_week": round(last_avg, 2),
            "change": round(this_avg - last_avg, 2),
        }

    return context if context else None


# ─── HTML Template ───────────────────────────────────────────────────────────

_SECTION_HEADER = (
    '<div style="font-size:10px;color:#404040;letter-spacing:1.5px;'
    "margin-bottom:8px;border-bottom:1px solid #1a1a2e;"
    'padding-bottom:4px">{title}</div>'
)
_SECTION_END = '<div style="margin-bottom:12px"></div>'


def _build_full_html(briefing: dict, rerouting: dict, crack: dict, pro_data: dict) -> str:
    """Build the comprehensive email HTML body with Pro-exclusive sections."""
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

    # Crack spread summary line
    crack_str = ""
    if crack and not crack.get("error"):
        s321 = crack.get("spread_321")
        avg30 = crack.get("avg_30d")
        if s321 is not None:
            diff = ""
            if avg30:
                d = s321 - avg30
                diff = (
                    f' <span style="color:{"#34d399" if d >= 0 else "#f87171"}">'
                    f"({_pct((d / avg30) * 100)} vs 30d)</span>"
                )
            crack_str = f'3-2-1 Crack: <span style="color:#22d3ee;font-weight:bold">${_safe(s321)}/bbl</span>{diff}'

    # === CRACK SPREAD ANALYSIS (Pro) ===
    crack_analysis_html = ""
    ca = pro_data.get("crack_analysis")
    if ca:
        ca_parts = []
        ca_parts.append(
            f'<div style="font-size:12px;color:#e5e5e5;margin-bottom:4px">'
            f'3-2-1: <span style="color:#22d3ee;font-weight:bold">${_safe(ca["spread"])}/bbl</span>'
            f"</div>"
        )

        avgs = []
        if ca.get("avg_7d") is not None:
            c7 = "#34d399" if (ca.get("pct_vs_7d") or 0) >= 0 else "#f87171"
            avgs.append(f'vs 7d: ${_safe(ca["avg_7d"])} (<span style="color:{c7}">{_pct(ca["pct_vs_7d"])}</span>)')
        if ca.get("avg_30d") is not None:
            c30 = "#34d399" if (ca.get("pct_vs_30d") or 0) >= 0 else "#f87171"
            avgs.append(f'vs 30d: ${_safe(ca["avg_30d"])} (<span style="color:{c30}">{_pct(ca["pct_vs_30d"])}</span>)')
        if avgs:
            ca_parts.append(
                f'<div style="font-size:11px;color:#a3a3a3;margin-bottom:4px">{"&middot;".join(avgs)}</div>'
            )

        if ca.get("percentile") is not None:
            ca_parts.append(
                f'<div style="font-size:11px;color:#737373;margin-bottom:4px">'
                f"{ca['percentile']}th percentile vs 1 year</div>"
            )

        if ca.get("highlight"):
            direction = "up" if (ca.get("pct_vs_30d") or 0) > 0 else "down"
            ca_parts.append(
                f'<div style="font-size:11px;color:#f87171;font-weight:bold;margin-bottom:4px">'
                f"Significant move {direction}: {_pct(ca['pct_vs_30d'])} vs 30d average</div>"
            )

        crack_analysis_html = _SECTION_HEADER.format(title="CRACK SPREAD ANALYSIS") + "".join(ca_parts) + _SECTION_END

    # === AIS OVERVIEW ===
    total_vessels = fleet.get("total_vessels_global", 0)
    tankers = fleet.get("tankers_global", 0)
    zone_parts = []
    for a in anomalies:
        zone_parts.append(f"{a['chokepoint'].capitalize()} {a.get('current_value', '?')}")

    storage_vessels, storage_vlcc = _get_floating_storage_count()

    # === EQUITY MOVERS (Pro) ===
    equity_html = ""
    em = pro_data.get("equity_movers")
    if em and em.get("top_movers"):
        movers_spans = []
        for m in em["top_movers"]:
            color = "#34d399" if m["change_pct"] >= 0 else "#f87171"
            sign = "+" if m["change_pct"] >= 0 else ""
            movers_spans.append(f'<span style="color:{color}">{m["ticker"]} {sign}{m["change_pct"]:.1f}%</span>')

        corr_line = ""
        hc = em.get("highest_corr")
        if hc:
            corr_line = (
                f'<div style="font-size:11px;color:#737373;margin-bottom:4px">'
                f"Highest WTI correlation: {hc['ticker']} (r={hc['corr']:.2f})</div>"
            )

        equity_html = (
            _SECTION_HEADER.format(title="EQUITY MOVERS")
            + '<div style="font-size:12px;color:#e5e5e5;margin-bottom:4px">'
            + " &nbsp;|&nbsp; ".join(movers_spans)
            + "</div>"
            + corr_line
            + _SECTION_END
        )

    # === CHOKEPOINTS (existing — critical/warning anomalies) ===
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

    # === CHOKEPOINT DETAIL (Pro — expanded, >10% threshold) ===
    cp_detail_html = ""
    cd = pro_data.get("chokepoint_detail")
    if cd:
        cd_rows = []
        for d in cd:
            color = (
                "#f87171"
                if d["drop_pct"] < -25
                else "#fb923c"
                if d["drop_pct"] < -10
                else "#34d399"
                if d["drop_pct"] > 10
                else "#737373"
            )
            cd_rows.append(
                f'<tr><td style="padding:2px 12px 2px 0;color:{color};font-weight:bold">{d["name"]}</td>'
                f'<td style="padding:2px 12px 2px 0;color:#e5e5e5">{d["current"]} vessels</td>'
                f'<td style="padding:2px 0;color:{color}">{_pct(d["drop_pct"])} vs 30d</td></tr>'
            )

        # Rerouting state
        rerouting_line = ""
        r_current = rerouting.get("current", {}) if rerouting.get("available") else {}
        ratio_pct = r_current.get("ratio_pct")
        r_state = r_current.get("state", "")
        if ratio_pct is not None:
            sc = "#f87171" if r_state == "elevated" else "#34d399" if r_state == "normal" else "#fb923c"
            rerouting_line = (
                f'<div style="font-size:11px;color:#737373;margin-bottom:4px">'
                f"Rerouting: Cape Share {ratio_pct:.1f}% "
                f'(<span style="color:{sc}">{r_state.upper()}</span>)</div>'
            )

        cp_detail_html = (
            _SECTION_HEADER.format(title="CHOKEPOINT DETAIL")
            + '<table style="border-collapse:collapse;font-family:Courier New,Courier,monospace;'
            + 'font-size:11px;margin-bottom:4px">'
            + "".join(cd_rows)
            + "</table>"
            + rerouting_line
            + _SECTION_END
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

    # === STS ACTIVITY (Pro, conditional) ===
    sts_html = ""
    sa = pro_data.get("sts_activity")
    if sa:
        zone_list = " | ".join(f"{z} ({c})" for z, c in sorted(sa["by_zone"].items(), key=lambda x: -x[1]))
        sts_html = (
            _SECTION_HEADER.format(title="STS ACTIVITY")
            + f'<div style="font-size:11px;color:#a3a3a3;margin-bottom:4px">'
            f'Active candidates: <span style="color:#fb923c;font-weight:bold">{sa["candidates"]}</span>'
            f' &middot; Proximity pairs: <span style="color:#fb923c;font-weight:bold">{sa["pairs"]}</span>'
            f"</div>"
            f'<div style="font-size:11px;color:#737373;margin-bottom:12px">{zone_list}</div>'
        )

    # === WEEKLY CONTEXT (Pro, Monday only) ===
    weekly_html = ""
    wc = pro_data.get("weekly_context")
    if wc:
        wc_parts = []

        zc = wc.get("zone_changes")
        if zc:
            zone_strs = []
            for z in zc:
                color = "#34d399" if z["change_pct"] > 0 else "#f87171"
                zone_strs.append(f'{z["zone"]} <span style="color:{color}">{_pct(z["change_pct"])}</span>')
            wc_parts.append(
                f'<div style="font-size:11px;color:#a3a3a3;margin-bottom:4px">'
                f"Zone traffic WoW: {' | '.join(zone_strs)}</div>"
            )

        cu = wc.get("cushing")
        if cu:
            delta_color = "#34d399" if cu["delta"] < 0 else "#f87171"
            delta_sign = "+" if cu["delta"] >= 0 else ""
            wc_parts.append(
                f'<div style="font-size:11px;color:#a3a3a3;margin-bottom:4px">'
                f"Cushing Stocks: {cu['current'] / 1000:.1f}M bbl "
                f'(<span style="color:{delta_color}">{delta_sign}{cu["delta"] / 1000:.1f}M</span>)'
                f"</div>"
            )

        ct = wc.get("crack_trend")
        if ct:
            change_color = "#34d399" if ct["change"] >= 0 else "#f87171"
            wc_parts.append(
                f'<div style="font-size:11px;color:#a3a3a3;margin-bottom:4px">'
                f"Crack Spread: ${_safe(ct['this_week'])}/bbl "
                f"(prior week ${_safe(ct['last_week'])}, "
                f'<span style="color:{change_color}">{"+" if ct["change"] >= 0 else ""}'
                f"{_safe(ct['change'])}</span>)</div>"
            )

        if wc_parts:
            weekly_html = _SECTION_HEADER.format(title="WEEKLY CONTEXT") + "".join(wc_parts) + _SECTION_END

    # === ASSEMBLE FINAL HTML ===
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
{f'<div style="font-size:11px;color:#737373;margin-bottom:12px">{crack_str}</div>' if crack_str else _SECTION_END}

{crack_analysis_html}

<!-- AIS OVERVIEW -->
<div style="font-size:10px;color:#404040;letter-spacing:1.5px;margin-bottom:8px;border-bottom:1px solid #1a1a2e;padding-bottom:4px">AIS OVERVIEW</div>
<div style="font-size:11px;color:#a3a3a3;margin-bottom:4px">
  Global Fleet: <span style="color:#e5e5e5;font-weight:bold">{total_vessels:,}</span> vessels &middot;
  <span style="color:#e5e5e5;font-weight:bold">{tankers:,}</span> tankers
</div>
{f'<div style="font-size:11px;color:#737373;margin-bottom:4px">Zones: {" | ".join(zone_parts)}</div>' if zone_parts else ""}
{f'<div style="font-size:11px;color:#737373;margin-bottom:12px">Floating Storage: <span style="color:#fb923c;font-weight:bold">{storage_vessels} vessels</span>{f" ({storage_vlcc} VLCCs)" if storage_vlcc > 0 else ""}</div>' if storage_vessels > 0 else _SECTION_END}

{equity_html}

<!-- DISRUPTIONS / CHOKEPOINTS -->
{'<div style="font-size:10px;color:#404040;letter-spacing:1.5px;margin-bottom:8px;border-bottom:1px solid #1a1a2e;padding-bottom:4px">ACTIVE DISRUPTIONS</div><table style="border-collapse:collapse;font-family:Courier New,Courier,monospace;font-size:12px;margin-bottom:12px">' + "".join(cp_rows) + "</table>" if cp_rows else '<div style="font-size:11px;color:#34d399;margin-bottom:12px">All chokepoints within normal range</div>'}

{cp_detail_html}

<!-- SIGNALS -->
<div style="font-size:10px;color:#404040;letter-spacing:1.5px;margin-bottom:8px;border-bottom:1px solid #1a1a2e;padding-bottom:4px">SIGNALS</div>
<div style="font-size:11px;line-height:1.8;margin-bottom:16px">
  {signals_html}
</div>

{sts_html}

{weekly_html}

<!-- FOOTER -->
<div style="border-top:1px solid #27272a;padding-top:12px;margin-top:8px">
  <div style="font-size:10px;color:#525252;margin-bottom:8px;line-height:1.6">
    This briefing includes Pro-exclusive data: Crack Spreads, Equity Correlations, STS Detection.
  </div>
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
