"""
FastAPI dependencies for authentication and Pro-tier gating.

Usage in routes:
    from backend.auth.dependencies import require_pro, get_current_user

    @router.get("/pro-endpoint")
    async def pro_only(user = Depends(require_pro)):
        return {"email": user["email"]}

    @router.get("/optional-auth")
    async def optional(user = Depends(get_current_user)):
        if user:
            return {"tier": user["sub_status"]}
        return {"tier": "free"}
"""

from fastapi import Cookie, HTTPException, Request

from backend.auth.api_keys import raw_key_from_request, resolve_request_key
from backend.auth.jwt import verify_token
from backend.auth.subscription_check import is_pro
from backend.database import SessionLocal
from backend.models.subscription import Subscription


def _sub_is_pro(db, email: str) -> bool:
    """Newest subscription for `email` passes is_pro. The JWT's (or a key's)
    tier is deliberately never trusted or stored — every pro decision reads
    the DB (require_pro's original doctrine, now shared with the key path)."""
    sub = (
        db.query(Subscription)
        .filter(Subscription.email == email)
        .order_by(Subscription.id.desc())
        .first()
    )
    return bool(is_pro(sub))


def get_current_user(request: Request, obsyd_token: str | None = Cookie(None)) -> dict | None:
    """Extract current user from cookie. Returns None if not authenticated."""
    if not obsyd_token:
        return None
    payload = verify_token(obsyd_token)
    if not payload:
        return None
    return payload


def require_auth(request: Request, obsyd_token: str | None = Cookie(None)) -> dict:
    """Require authentication. Raises 401 if not logged in."""
    user = get_current_user(request, obsyd_token)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def optional_pro(request: Request, obsyd_token: str | None = Cookie(None)) -> bool:
    """True when the request carries a valid session OR API key whose newest
    subscription passes is_pro — NEVER raises (anonymous callers get False,
    not a 401).

    The premium preview's soft check (backend/premium.py): free endpoints that
    merely omit premium fields, and the catalog that merely hides premium
    series, need to know the tier without turning the whole endpoint into a
    login wall. Credential-less requests (most public traffic) short-circuit
    before any DB read.
    """
    user = get_current_user(request, obsyd_token)
    if not user and raw_key_from_request(request) is None:
        return False
    db = SessionLocal()
    try:
        if user:
            return _sub_is_pro(db, user["email"])
        key = resolve_request_key(request, db)
        return _sub_is_pro(db, key.email) if key else False
    finally:
        db.close()


def require_pro(request: Request, obsyd_token: str | None = Cookie(None)) -> dict:
    """Require Pro subscription (paid or in-trial), via session cookie OR API
    key. Raises 401 without a credential, 403 with one that isn't Pro.

    The JWT's sub_status claim is deliberately NOT trusted: a token lives up to
    jwt_expiry_days, so a refunded/downgraded user would otherwise keep Pro
    access until expiry. Every Pro request verifies against the DB (cheap on
    SQLite, single indexed lookup) — the API-key path shares that doctrine:
    a key stores no tier, its owner's newest subscription decides.
    """
    user = get_current_user(request, obsyd_token)
    db = SessionLocal()
    try:
        if user is None:
            key = resolve_request_key(request, db)
            if key is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if _sub_is_pro(db, key.email):
                return {"email": key.email, "via": "api_key"}
        elif _sub_is_pro(db, user["email"]):
            return user
    finally:
        db.close()

    raise HTTPException(status_code=403, detail="Pro subscription required")
