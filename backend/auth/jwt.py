"""
JWT utilities for OBSYD Pro authentication.

Magic Link flow:
1. User enters email → POST /api/auth/magic-link
2. Backend sends email with JWT token link
3. User clicks link → GET /api/auth/verify?token=...
4. Backend sets httpOnly cookie with session JWT (30 days)
5. Frontend reads /api/auth/me to check auth state
"""

import hashlib
import hmac
import json
import logging
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode

from backend.config import settings

logger = logging.getLogger(__name__)


def _b64encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return urlsafe_b64decode(s)


def create_token(email: str, subscription_status: str = "free", expiry_days: int | None = None) -> str:
    """Create a JWT-like token (header.payload.signature)."""
    exp_days = expiry_days or settings.jwt_expiry_days
    now = int(time.time())
    payload = {
        "email": email.lower(),
        "sub_status": subscription_status,
        "iat": now,
        "exp": now + (exp_days * 86400),
    }
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64encode(json.dumps(payload).encode())
    sig = hmac.new(settings.jwt_secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"{header}.{body}.{sig}"


def create_magic_token(email: str) -> str:
    """Short-lived token for magic link (15 minutes)."""
    now = int(time.time())
    payload = {
        "email": email.lower(),
        "purpose": "magic_link",
        "iat": now,
        "exp": now + 900,  # 15 min
    }
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64encode(json.dumps(payload).encode())
    sig = hmac.new(settings.jwt_secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"{header}.{body}.{sig}"


def verify_token(token: str) -> dict | None:
    """Verify and decode a token. Returns payload dict or None."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, body, sig = parts
        expected_sig = hmac.new(settings.jwt_secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload = json.loads(_b64decode(body))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload
    except Exception as e:
        logger.debug("Token verification failed: %s", e)
        return None
