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

from backend.auth.jwt import verify_token
from backend.database import SessionLocal
from backend.models.subscription import Subscription


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


def require_pro(request: Request, obsyd_token: str | None = Cookie(None)) -> dict:
    """Require Pro subscription. Raises 401/403 if not authorized."""
    user = require_auth(request, obsyd_token)

    # Check if token says pro
    if user.get("sub_status") == "pro":
        return user

    # Double-check against DB in case subscription was updated after token was issued
    db = SessionLocal()
    try:
        sub = (
            db.query(Subscription)
            .filter(
                Subscription.email == user["email"],
                Subscription.status == "active",
            )
            .first()
        )
        if sub:
            return user
    finally:
        db.close()

    raise HTTPException(status_code=403, detail="Pro subscription required")
