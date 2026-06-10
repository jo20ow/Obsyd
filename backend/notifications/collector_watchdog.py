"""
Collector watchdog — daily ops alert when a data collector goes stale.

Runs via APScheduler (daily 09:00 UTC). Reuses the staleness thresholds
from the health route, so /api/health/collectors and this alert always
agree. Emails OPS_EMAIL via Resend; logs (and skips) when no key is set.
"""

import logging
from datetime import datetime

import httpx
from sqlalchemy import func

from backend.config import settings
from backend.database import SessionLocal
from backend.models.prices import EIAPrice, FREDSeries
from backend.models.sentiment import GDELTVolume
from backend.models.vessels import VesselPosition
from backend.notifications.daily_email import OPS_EMAIL
from backend.routes.health import STALENESS

logger = logging.getLogger(__name__)


def _collect_staleness(db) -> dict:
    """Return {collector: {'fresh': bool, 'last_seen': datetime|None}}."""
    now = datetime.utcnow()
    last = {
        "eia": db.query(func.max(EIAPrice.fetched_at)).scalar(),
        "fred": db.query(func.max(FREDSeries.fetched_at)).scalar(),
        "ais": db.query(func.max(VesselPosition.timestamp)).scalar(),
        "gdelt": db.query(func.max(GDELTVolume.created_at)).scalar(),
    }
    return {
        key: {
            "fresh": ts is not None and (now - ts) <= STALENESS[key],
            "last_seen": ts,
        }
        for key, ts in last.items()
    }


async def check_collectors():
    """Daily watchdog: email ops when one or more collectors are stale."""
    db = SessionLocal()
    try:
        status = _collect_staleness(db)
    except Exception as e:
        logger.error("Collector watchdog: staleness check failed: %s", e)
        return
    finally:
        db.close()

    stale = {k: v for k, v in status.items() if not v["fresh"]}
    if not stale:
        logger.info("Collector watchdog: all collectors fresh")
        return

    names = ", ".join(stale)
    logger.warning("Collector watchdog: STALE collectors: %s", names)

    api_key = settings.resend_api_key
    if not api_key:
        logger.warning("Collector watchdog: RESEND_API_KEY not set, alert logged only")
        return
    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()

    rows = "".join(
        f"<tr><td><strong>{k}</strong></td>"
        f"<td>{v['last_seen'].isoformat() if v['last_seen'] else 'never'}</td>"
        f"<td>{STALENESS[k]}</td></tr>"
        for k, v in stale.items()
    )
    html = (
        f"<p>The following OBSYD collectors have not written data within their freshness window:</p>"
        f"<table border='1' cellpadding='6' cellspacing='0'>"
        f"<tr><th>Collector</th><th>Last seen (UTC)</th><th>Threshold</th></tr>{rows}</table>"
        f"<p>Check: <code>journalctl -u obsyd --since '-24h' | grep -i error</code></p>"
    )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "from": "OBSYD <briefing@obsyd.dev>",
                    "to": [OPS_EMAIL],
                    "subject": f"OBSYD ALERT: stale collectors — {names}",
                    "html": html,
                },
            )
            resp.raise_for_status()
        logger.info("Collector watchdog: alert email sent (%s)", names)
    except Exception as e:
        logger.error("Collector watchdog: alert email failed: %s", e)
