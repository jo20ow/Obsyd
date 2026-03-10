"""
Lemon Squeezy webhook handler.

Receives subscription lifecycle events:
  - subscription_created → activate Pro
  - subscription_updated → update status
  - subscription_cancelled → deactivate Pro
  - subscription_expired → deactivate Pro

Webhook signature verification: HMAC-SHA256 of raw body.
"""

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Request

from backend.config import settings
from backend.database import SessionLocal
from backend.models.subscription import Subscription

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/lemonsqueezy")
async def lemonsqueezy_webhook(request: Request):
    """Handle Lemon Squeezy subscription webhooks."""
    webhook_secret = settings.lemonsqueezy_webhook_secret
    if not webhook_secret:
        logger.warning("Lemon Squeezy webhook received but no secret configured")
        raise HTTPException(status_code=500, detail="Webhook not configured")

    if hasattr(webhook_secret, "get_secret_value"):
        webhook_secret = webhook_secret.get_secret_value()

    # Verify signature
    body = await request.body()
    sig_header = request.headers.get("X-Signature", "")

    expected = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(sig_header, expected):
        logger.warning("Lemon Squeezy webhook: invalid signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = json.loads(body)
    event_name = payload.get("meta", {}).get("event_name", "")
    data = payload.get("data", {})
    attrs = data.get("attributes", {})

    email = attrs.get("user_email", "").lower()
    subscription_id = str(data.get("id", ""))
    status = attrs.get("status", "")
    customer_id = str(attrs.get("customer_id", ""))
    variant_id = str(attrs.get("variant_id", ""))
    update_url = attrs.get("urls", {}).get("update_payment_method", "")
    cancel_url = attrs.get("urls", {}).get("customer_portal", "")

    if not email:
        logger.warning("Lemon Squeezy webhook: no email in payload")
        return {"status": "ok"}

    logger.info("Lemon Squeezy webhook: %s for %s (status=%s)", event_name, email, status)

    db = SessionLocal()
    try:
        if event_name == "subscription_created":
            existing = db.query(Subscription).filter(Subscription.lemon_squeezy_id == subscription_id).first()

            if existing:
                existing.status = "active"
                existing.email = email
            else:
                db.add(
                    Subscription(
                        email=email,
                        lemon_squeezy_id=subscription_id,
                        status="active",
                        plan="pro",
                        customer_id=customer_id,
                        variant_id=variant_id,
                        update_url=update_url,
                        cancel_url=cancel_url,
                    )
                )
            db.commit()

        elif event_name in ("subscription_updated", "subscription_resumed"):
            sub = db.query(Subscription).filter(Subscription.lemon_squeezy_id == subscription_id).first()
            if sub:
                sub.status = "active" if status == "active" else status
                db.commit()

        elif event_name in ("subscription_cancelled", "subscription_expired"):
            sub = db.query(Subscription).filter(Subscription.lemon_squeezy_id == subscription_id).first()
            if sub:
                sub.status = "cancelled" if event_name == "subscription_cancelled" else "expired"
                db.commit()

        elif event_name == "subscription_payment_success":
            # Extend access — subscription is active
            sub = db.query(Subscription).filter(Subscription.lemon_squeezy_id == subscription_id).first()
            if sub:
                sub.status = "active"
                db.commit()

    except Exception as e:
        logger.error("Lemon Squeezy webhook processing failed: %s", e)
        db.rollback()
    finally:
        db.close()

    return {"status": "ok"}
