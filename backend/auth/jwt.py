"""
JWT utilities for OBSYD Pro authentication.

Magic Link flow:
1. User enters email -> POST /api/auth/magic-link
2. Backend sends email with JWT token link
3. User clicks link -> GET /api/auth/verify?token=...
4. Backend sets httpOnly cookie with session JWT (30 days)
5. Frontend reads /api/auth/me to check auth state
"""

import logging

import jwt

from backend.config import settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"


def _get_secret() -> str:
    return settings.jwt_secret.get_secret_value()


def create_token(email: str, subscription_status: str = "free", expiry_days: int | None = None) -> str:
    """Create a session JWT token."""
    import time

    exp_days = expiry_days or settings.jwt_expiry_days
    now = int(time.time())
    payload = {
        "email": email.lower(),
        "sub_status": subscription_status,
        "iat": now,
        "exp": now + (exp_days * 86400),
    }
    return jwt.encode(payload, _get_secret(), algorithm=ALGORITHM)


def create_magic_token(email: str) -> str:
    """Short-lived token for magic link (15 minutes)."""
    import time

    now = int(time.time())
    payload = {
        "email": email.lower(),
        "purpose": "magic_link",
        "iat": now,
        "exp": now + 900,
    }
    return jwt.encode(payload, _get_secret(), algorithm=ALGORITHM)


def verify_token(token: str) -> dict | None:
    """Verify and decode a token. Returns payload dict or None."""
    try:
        return jwt.decode(token, _get_secret(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        logger.debug("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("Token verification failed: %s", e)
        return None
