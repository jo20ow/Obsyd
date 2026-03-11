"""
Email management endpoints — unsubscribe + test briefing.
"""

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from backend.auth.dependencies import require_auth, require_pro
from backend.database import SessionLocal
from backend.models.pro_features import EmailSubscriber
from backend.models.waitlist import Waitlist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/email", tags=["email"])


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(token: str = Query(...)):
    """Public unsubscribe endpoint. Sets subscriber to inactive."""
    db = SessionLocal()
    try:
        sub = (
            db.query(EmailSubscriber)
            .filter(
                EmailSubscriber.unsubscribe_token == token,
                EmailSubscriber.active == True,  # noqa: E712
            )
            .first()
        )
        if sub:
            sub.active = False
            sub.unsubscribed_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("Email unsubscribe: %s", sub.email)
            return HTMLResponse(
                content="""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Unsubscribed — OBSYD</title></head>
<body style="margin:0;padding:0;background:#09090b;font-family:'Courier New',monospace;color:#d4d4d4;display:flex;align-items:center;justify-content:center;min-height:100vh">
<div style="text-align:center;padding:40px">
<div style="color:#22d3ee;font-size:18px;font-weight:bold;letter-spacing:3px;margin-bottom:20px">OBSYD</div>
<div style="font-size:14px;color:#a3a3a3;margin-bottom:20px">You have been unsubscribed from daily briefings.</div>
<a href="https://obsyd.dev" style="color:#22d3ee;font-size:12px">Back to Dashboard</a>
</div>
</body>
</html>""",
                status_code=200,
            )
        return HTMLResponse(
            content="""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>OBSYD</title></head>
<body style="margin:0;padding:0;background:#09090b;font-family:'Courier New',monospace;color:#d4d4d4;display:flex;align-items:center;justify-content:center;min-height:100vh">
<div style="text-align:center;padding:40px">
<div style="font-size:14px;color:#a3a3a3">Invalid or expired unsubscribe link.</div>
<a href="https://obsyd.dev" style="color:#22d3ee;font-size:12px">Back to Dashboard</a>
</div>
</body>
</html>""",
            status_code=200,
        )
    finally:
        db.close()


@router.get("/stats")
async def email_stats(_user=Depends(require_auth)):
    """Subscriber counts for monitoring (auth required)."""
    db = SessionLocal()
    try:
        active_subs = db.query(EmailSubscriber).filter(EmailSubscriber.active == True).count()  # noqa: E712
        inactive_subs = db.query(EmailSubscriber).filter(EmailSubscriber.active == False).count()  # noqa: E712
        waitlist_total = db.query(Waitlist).filter(Waitlist.subscribed == True).count()  # noqa: E712
        sub_emails = {s.email for s in db.query(EmailSubscriber.email).filter(EmailSubscriber.active == True).all()}  # noqa: E712
        waitlist_only = (
            db.query(Waitlist).filter(Waitlist.subscribed == True, Waitlist.email.notin_(sub_emails)).count()  # noqa: E712
        )
        total_daily = active_subs + waitlist_only
        return {
            "email_subscribers": active_subs,
            "email_unsubscribed": inactive_subs,
            "waitlist_subscribed": waitlist_total,
            "waitlist_only": waitlist_only,
            "total_daily_recipients": total_daily,
            "daily_send_limit": 95,
            "headroom": max(0, 95 - total_daily),
        }
    finally:
        db.close()


@router.post("/test-briefing")
async def test_briefing(user=Depends(require_pro)):
    """Send a test briefing email to the authenticated Pro user."""
    from backend.config import settings
    from backend.notifications.daily_email import _build_full_html, _build_subject_line, _send_via_resend

    api_key = settings.resend_api_key
    if not api_key:
        return {"error": "RESEND_API_KEY not configured"}
    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()

    email = user["email"]

    # Ensure subscriber exists
    db = SessionLocal()
    try:
        sub = db.query(EmailSubscriber).filter(EmailSubscriber.email == email).first()
        if not sub:
            sub = EmailSubscriber(
                email=email,
                tier="pro",
                unsubscribe_token=secrets.token_urlsafe(32),
                active=True,
            )
            db.add(sub)
            db.commit()

        from backend.routes.briefing import _build_briefing
        from backend.signals.crack_spread import get_crack_spread
        from backend.signals.tonnage_proxy import compute_rerouting_index

        briefing = await _build_briefing()
        rerouting = compute_rerouting_index(days=365)
        crack = await get_crack_spread()

        subject = _build_subject_line(briefing, rerouting, crack)
        html = _build_full_html(briefing, rerouting, crack)
        html = html.replace("{{email}}", email).replace("{{token}}", sub.unsubscribe_token)

        await _send_via_resend(
            api_key=api_key,
            to_email=email,
            subject=subject,
            html=html,
            unsubscribe_token=sub.unsubscribe_token,
        )
        return {"status": "ok", "message": f"Test briefing sent to {email}"}
    except Exception as e:
        logger.error("Test briefing failed: %s", e)
        return {"error": "Failed to send test briefing"}
    finally:
        db.close()
