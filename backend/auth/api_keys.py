"""API-key issuance and per-request resolution — Phase 2.

Key format: ``obsyd_`` + 32 url-safe random chars (~192 bits). Stored as a
SHA-256 hash (high-entropy token → fast hash is correct; bcrypt is for
passwords). Sent as ``Authorization: Bearer obsyd_…`` or ``X-Api-Key``.

Resolution runs AT MOST ONCE per request (cached on request.state) and
meters the request the moment a key resolves — so every keyed request is
counted exactly once, whichever dependency (rate limit, optional_pro,
require_pro) touched it first.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy.orm import Session

from backend import metering
from backend.models.api_key import ApiKey

KEY_PREFIX = "obsyd_"
#: Active (unrevoked) keys per email — a lost-key rotation buffer, not a fleet.
MAX_ACTIVE_KEYS = 5

_STATE_ATTR = "obsyd_api_key"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """(raw, key_hash, prefix) — raw is shown once and never stored."""
    raw = KEY_PREFIX + secrets.token_urlsafe(24)
    return raw, hash_key(raw), raw[: len(KEY_PREFIX) + 8]


def create_key(db: Session, email: str, label: str | None = None) -> tuple[ApiKey, str]:
    """Mint a key for `email`. Raises ValueError at the active-key cap."""
    active = (
        db.query(ApiKey)
        .filter(ApiKey.email == email, ApiKey.revoked_at.is_(None))
        .count()
    )
    if active >= MAX_ACTIVE_KEYS:
        raise ValueError(
            f"Key limit reached ({MAX_ACTIVE_KEYS} active). Revoke one first."
        )
    raw, key_hash, prefix = generate_key()
    row = ApiKey(key_hash=key_hash, prefix=prefix, email=email, label=label)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, raw


def raw_key_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and auth[7:].startswith(KEY_PREFIX):
        return auth[7:].strip()
    xk = request.headers.get("x-api-key", "")
    return xk.strip() if xk.startswith(KEY_PREFIX) else None


def resolve_request_key(request: Request, db: Session) -> ApiKey | None:
    """The valid (unrevoked) ApiKey this request carries, or None.

    Cached on request.state so the DB lookup and the metering record happen
    exactly once however many dependencies ask.
    """
    cached = getattr(request.state, _STATE_ATTR, "unset")
    if cached != "unset":
        return cached
    raw = raw_key_from_request(request)
    row = None
    if raw:
        row = db.query(ApiKey).filter(ApiKey.key_hash == hash_key(raw)).first()
        if row is not None and row.revoked_at is not None:
            row = None
        if row is not None:
            metering.record(row.id)
    setattr(request.state, _STATE_ATTR, row)
    return row


def revoke_key(db: Session, email: str, key_id: int) -> bool:
    """Revoke one of `email`'s own keys. True if a live key was revoked."""
    row = (
        db.query(ApiKey)
        .filter(ApiKey.id == key_id, ApiKey.email == email, ApiKey.revoked_at.is_(None))
        .first()
    )
    if row is None:
        return False
    row.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return True
