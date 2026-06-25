"""
Authentication routes — Magic Link login + user info.

Flow:
  POST /api/auth/magic-link  → sends email with login link
  GET  /api/auth/verify       → validates token, sets cookie
  GET  /api/auth/me           → returns current user info
  POST /api/auth/logout       → clears cookie
"""

import logging
import re
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, field_validator

from backend.auth.dependencies import get_current_user, require_auth
from backend.auth.jwt import create_magic_token, create_token, verify_token
from backend.auth.subscription_check import is_pro
from backend.config import settings
from backend.database import SessionLocal
from backend.models.subscription import Subscription
from backend.models.waitlist import Waitlist

TRIAL_DAYS = 14

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _checkout_url_for(email: str | None) -> str:
    """Lemon Squeezy checkout URL, prefilled with the signed-in user's email.

    Prefilling `checkout[email]` makes the LS purchase email default to the
    account email, so the subscription_created webhook (which matches on
    `user_email`) attaches Pro to the right account. Anonymous visitors get
    the bare URL (they pick an email at checkout, then log in with it).
    """
    base = settings.lemonsqueezy_checkout_url
    if not email:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}checkout[email]={quote(email)}"


_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class MagicLinkRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def normalize(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email format")
        return v


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

    # Check subscription status (paid OR in-trial both count as pro)
    db = SessionLocal()
    sub_status = "free"
    try:
        sub = (
            db.query(Subscription)
            .filter(Subscription.email == email)
            .order_by(Subscription.id.desc())
            .first()
        )
        if is_pro(sub):
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
        return {
            "authenticated": False,
            "tier": "free",
            "checkout_url": _checkout_url_for(None),
        }

    # Refresh subscription status from DB (paid OR in-trial both count as pro)
    db = SessionLocal()
    try:
        sub = (
            db.query(Subscription)
            .filter(Subscription.email == user["email"])
            .order_by(Subscription.id.desc())
            .first()
        )
        tier = "pro" if is_pro(sub) else "free"
        trial_ends_at = sub.trial_ends_at.isoformat() if (sub and sub.status == "trialing" and sub.trial_ends_at) else None
        trial_used = bool(sub)  # any past subscription record disables fresh trial signup
    finally:
        db.close()

    return {
        "authenticated": True,
        "email": user["email"],
        "tier": tier,
        "trial_ends_at": trial_ends_at,
        "trial_eligible": tier == "free" and not trial_used,
        "checkout_url": _checkout_url_for(user["email"]) if tier == "free" else None,
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


@router.post("/start-trial")
async def start_trial(response: Response, user: dict = Depends(require_auth)):
    """Start a 14-day in-app Pro trial. No card required.

    One trial per email — any prior Subscription record (active, expired,
    cancelled, or previous trial) disables a fresh trial. This keeps the
    flow simple and prevents trial-cycling.
    """
    from datetime import datetime, timedelta

    db = SessionLocal()
    try:
        existing = (
            db.query(Subscription)
            .filter(Subscription.email == user["email"])
            .order_by(Subscription.id.desc())
            .first()
        )
        if existing is not None:
            if is_pro(existing):
                # Already Pro — nothing to do, surface that to the client.
                return {
                    "status": "already_pro",
                    "tier": "pro",
                    "trial_ends_at": existing.trial_ends_at.isoformat() if existing.trial_ends_at else None,
                }
            # Past trial / expired / cancelled — trial not re-grantable.
            raise HTTPException(
                status_code=409,
                detail="Trial already used. Subscribe via the Pro checkout to reactivate.",
            )

        now = datetime.utcnow()
        trial = Subscription(
            email=user["email"],
            status="trialing",
            plan="pro",
            trial_ends_at=now + timedelta(days=TRIAL_DAYS),
            drip_stage=0,  # welcome email sent right below
        )
        db.add(trial)
        db.commit()
        db.refresh(trial)
    finally:
        db.close()

    # Fire the welcome email synchronously. We tolerate failure (e.g. Resend
    # outage) and don't roll back the trial — the daily drip processor will
    # not re-send day 0 because drip_stage is already 0.
    try:
        from backend.notifications.trial_drip import send_welcome_now

        send_welcome_now(user["email"])
    except Exception:
        # Logged inside send_welcome_now; never block trial activation.
        pass

    # Re-issue session token with sub_status=pro so the frontend sees Pro
    # immediately without waiting for a /me refresh.
    new_session_token = create_token(user["email"], subscription_status="pro")
    response.set_cookie(
        key="obsyd_token",
        value=new_session_token,
        max_age=settings.jwt_expiry_days * 86400,
        httponly=True,
        secure=True,
        samesite="lax",
        domain="obsyd.dev",
        path="/",
    )
    return {
        "status": "trial_started",
        "tier": "pro",
        "trial_ends_at": trial.trial_ends_at.isoformat(),
        "days_remaining": TRIAL_DAYS,
    }


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
