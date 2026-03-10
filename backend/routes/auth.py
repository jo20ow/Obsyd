"""
Authentication routes — Magic Link login + user info.

Flow:
  POST /api/auth/magic-link  → sends email with login link
  GET  /api/auth/verify       → validates token, sets cookie
  GET  /api/auth/me           → returns current user info
  POST /api/auth/logout       → clears cookie
"""

import logging

import httpx
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, field_validator

from backend.auth.dependencies import get_current_user
from backend.auth.jwt import create_magic_token, create_token, verify_token
from backend.config import settings
from backend.database import SessionLocal
from backend.models.subscription import Subscription
from backend.models.waitlist import Waitlist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class MagicLinkRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def normalize(cls, v: str) -> str:
        return v.strip().lower()


@router.post("/magic-link")
async def request_magic_link(body: MagicLinkRequest):
    """Send a magic link email for passwordless login."""
    api_key = settings.resend_api_key
    if not api_key:
        return {"status": "error", "message": "Email not configured"}
    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()

    token = create_magic_token(body.email)
    login_url = f"https://obsyd.dev/api/auth/verify?token={token}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "from": "OBSYD <briefing@obsyd.dev>",
                    "to": [body.email],
                    "subject": "OBSYD — Your login link",
                    "html": _magic_link_html(login_url),
                },
            )
            resp.raise_for_status()
    except Exception as e:
        logger.error("Magic link email failed for %s: %s", body.email, e)
        return {"status": "error", "message": "Failed to send email"}

    # Also add to waitlist if not already there
    db = SessionLocal()
    try:
        existing = db.query(Waitlist).filter(Waitlist.email == body.email).first()
        if not existing:
            from backend.routes.waitlist import _make_unsubscribe_token

            db.add(
                Waitlist(
                    email=body.email,
                    tier="pro",
                    unsubscribe_token=_make_unsubscribe_token(body.email),
                )
            )
            db.commit()
    except Exception:
        logger.debug("Waitlist auto-add failed for %s", body.email)
    finally:
        db.close()

    logger.info("Magic link sent to %s", body.email)
    return {"status": "ok"}


@router.get("/verify")
async def verify_magic_link(token: str, response: Response):
    """Verify magic link token and set session cookie."""
    payload = verify_token(token)
    if not payload:
        return Response(
            content=_redirect_html("https://obsyd.dev?auth=expired"),
            media_type="text/html",
        )

    if payload.get("purpose") != "magic_link":
        return Response(
            content=_redirect_html("https://obsyd.dev?auth=invalid"),
            media_type="text/html",
        )

    email = payload["email"]

    # Check subscription status
    db = SessionLocal()
    sub_status = "free"
    try:
        sub = db.query(Subscription).filter(Subscription.email == email, Subscription.status == "active").first()
        if sub:
            sub_status = "pro"
    finally:
        db.close()

    # Create session token (30 days)
    session_token = create_token(email, subscription_status=sub_status)

    resp = Response(
        content=_redirect_html("https://obsyd.dev?auth=success"),
        media_type="text/html",
    )
    resp.set_cookie(
        key="obsyd_token",
        value=session_token,
        max_age=settings.jwt_expiry_days * 86400,
        httponly=True,
        secure=True,
        samesite="lax",
        domain="obsyd.dev",
        path="/",
    )
    return resp


@router.get("/me")
async def get_me(user: dict | None = Depends(get_current_user)):
    """Get current user info and subscription status."""
    if not user:
        return {"authenticated": False, "tier": "free"}

    # Refresh subscription status from DB
    db = SessionLocal()
    try:
        sub = (
            db.query(Subscription).filter(Subscription.email == user["email"], Subscription.status == "active").first()
        )
        tier = "pro" if sub else "free"
    finally:
        db.close()

    return {
        "authenticated": True,
        "email": user["email"],
        "tier": tier,
        "checkout_url": settings.lemonsqueezy_checkout_url if tier == "free" else None,
    }


@router.post("/logout")
async def logout(response: Response):
    """Clear auth cookie."""
    response.delete_cookie(
        key="obsyd_token",
        domain="obsyd.dev",
        path="/",
    )
    return {"status": "ok"}


def _magic_link_html(url: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#09090b;font-family:'Courier New',monospace;color:#d4d4d4">
<div style="max-width:480px;margin:0 auto;padding:40px 20px">
<div style="border:1px solid #27272a;padding:30px;background:#0a0a12">
<div style="font-size:14px;color:#22d3ee;font-weight:bold;letter-spacing:3px;margin-bottom:20px">OBSYD</div>
<div style="font-size:13px;color:#a3a3a3;margin-bottom:24px;line-height:1.6">
Click the link below to log in to your OBSYD account. This link expires in 15 minutes.
</div>
<a href="{url}" style="display:inline-block;padding:10px 24px;background:#22d3ee;color:#09090b;text-decoration:none;font-family:'Courier New',monospace;font-size:12px;font-weight:bold;letter-spacing:1px">
LOG IN TO OBSYD
</a>
<div style="font-size:10px;color:#525252;margin-top:24px;line-height:1.5">
If you didn't request this, you can safely ignore this email.
</div>
</div>
</div>
</body></html>"""


def _redirect_html(url: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={url}">
<script>window.location.href="{url}"</script></head>
<body style="background:#09090b;color:#d4d4d4;font-family:monospace;padding:40px;text-align:center">
Redirecting to OBSYD...</body></html>"""
