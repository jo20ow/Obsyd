import hashlib
import hmac
import logging
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models.waitlist import Waitlist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/waitlist", tags=["waitlist"])

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _make_unsubscribe_token(email: str) -> str:
    secret = getattr(settings, "secret_key", "obsyd-waitlist-default")
    if hasattr(secret, "get_secret_value"):
        secret = secret.get_secret_value()
    return hmac.new(secret.encode(), email.lower().encode(), hashlib.sha256).hexdigest()[:32]


class WaitlistSignup(BaseModel):
    email: str
    tier: str = "pro"

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_RE.match(v):
            raise ValueError("Invalid email format")
        if len(v) > 254:
            raise ValueError("Email too long")
        return v

    @field_validator("tier")
    @classmethod
    def validate_tier(cls, v: str) -> str:
        if v not in ("free", "pro"):
            raise ValueError("Tier must be 'free' or 'pro'")
        return v


@router.post("")
async def signup(body: WaitlistSignup, db: Session = Depends(get_db)):
    existing = db.query(Waitlist).filter(Waitlist.email == body.email).first()
    if existing:
        return {"status": "ok", "message": "already registered"}

    entry = Waitlist(
        email=body.email,
        tier=body.tier,
        unsubscribe_token=_make_unsubscribe_token(body.email),
    )
    db.add(entry)
    db.commit()
    logger.info("Waitlist signup: %s (tier=%s)", body.email, body.tier)
    return {"status": "ok"}


@router.get("/count")
async def count(db: Session = Depends(get_db)):
    total = db.query(Waitlist).count()
    return {"count": total}


@router.get("/unsubscribe")
async def unsubscribe(email: str, token: str, db: Session = Depends(get_db)):
    expected = _make_unsubscribe_token(email)
    if not hmac.compare_digest(token, expected):
        return {"status": "error", "message": "Invalid token"}

    entry = db.query(Waitlist).filter(Waitlist.email == email.lower()).first()
    if not entry:
        return {"status": "error", "message": "Not found"}

    entry.subscribed = False
    db.commit()
    logger.info("Waitlist unsubscribe: %s", email)
    return {"status": "ok", "message": "You have been unsubscribed."}
