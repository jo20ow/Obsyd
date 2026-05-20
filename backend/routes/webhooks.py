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
            # Idempotency by LS subscription_id (replayed webhooks must not duplicate).
            existing = db.query(Subscription).filter(Subscription.lemon_squeezy_id == subscription_id).first()

            if existing:
                existing.status = "active"
                existing.email = email
                existing.trial_ends_at = None  # paid now, clear any leftover trial marker
            else:
                # Email-match upgrade: if the user previously started an in-app
                # trial (no LS id), upgrade that row to a paid LS sub instead
                # of creating a duplicate Subscription per email.
                trial_sub = (
                    db.query(Subscription)
                    .filter(
                        Subscription.email == email,
                        Subscription.lemon_squeezy_id.is_(None),
                    )
                    .order_by(Subscription.id.desc())
                    .first()
                )
                if trial_sub is not None:
                    trial_sub.lemon_squeezy_id = subscription_id
                    trial_sub.status = "active"
                    trial_sub.customer_id = customer_id
                    trial_sub.variant_id = variant_id
                    trial_sub.update_url = update_url
                    trial_sub.cancel_url = cancel_url
                    trial_sub.trial_ends_at = None
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

        elif event_name == "subscription_payment_failed":
            # LS retry policy will attempt the charge again; we mark the
            # sub as past_due so we can show a banner in the UI later.
            # `is_pro()` still treats past_due as Pro (grace period); a
            # follow-up subscription_expired event will eventually downgrade.
            sub = db.query(Subscription).filter(Subscription.lemon_squeezy_id == subscription_id).first()
            if sub:
                sub.status = "past_due"
                db.commit()

    except Exception as e:
        logger.error("Lemon Squeezy webhook processing failed: %s", e)
        db.rollback()
    finally:
        db.close()

    return {"status": "ok"}
