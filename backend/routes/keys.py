"""API-key management — Phase 2. Session-cookie authenticated (the magic-link
login): a key is minted UNDER an email identity, and its tier is always the
owner's newest subscription at request time (backend/auth/api_keys.py).

No UI yet, deliberately — the premium preview is tested hidden. The owner
(or any logged-in user) manages keys with curl and the session cookie:

    curl -b "obsyd_token=…" -X POST https://obsyd.dev/api/v1/keys -d '{"label":"desk"}' \
         -H 'content-type: application/json'
    curl -b "obsyd_token=…" https://obsyd.dev/api/v1/keys
    curl -b "obsyd_token=…" -X DELETE https://obsyd.dev/api/v1/keys/3
    curl -b "obsyd_token=…" https://obsyd.dev/api/v1/keys/usage
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import metering
from backend.auth.api_keys import MAX_ACTIVE_KEYS, create_key, revoke_key
from backend.auth.dependencies import require_auth
from backend.database import get_db
from backend.models.api_key import ApiKey, ApiUsageDaily

router = APIRouter(prefix="/api/v1/keys", tags=["v1-keys"])


class KeyCreate(BaseModel):
    label: str | None = Field(None, max_length=80)


def _row(k: ApiKey) -> dict:
    return {
        "id": k.id,
        "prefix": k.prefix,
        "label": k.label,
        "created_at": k.created_at.isoformat() if k.created_at else None,
        "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
    }


@router.post("")
def create(body: KeyCreate, user: dict = Depends(require_auth),
           db: Session = Depends(get_db)):
    """Mint a key. The RAW key appears in this response ONCE and is never
    retrievable again — only its hash is stored."""
    try:
        row, raw = create_key(db, user["email"], body.label)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**_row(row), "key": raw,
            "note": "Store this key now — it is shown only once."}


@router.get("")
def list_keys(user: dict = Depends(require_auth), db: Session = Depends(get_db)):
    rows = (
        db.query(ApiKey).filter(ApiKey.email == user["email"])
        .order_by(ApiKey.id.desc()).all()
    )
    return {
        "keys": [_row(k) for k in rows],
        "max_active": MAX_ACTIVE_KEYS,
    }


@router.delete("/{key_id}")
def revoke(key_id: int, user: dict = Depends(require_auth),
           db: Session = Depends(get_db)):
    if not revoke_key(db, user["email"], key_id):
        raise HTTPException(status_code=404, detail="No such active key of yours.")
    return {"revoked": key_id}


@router.get("/usage")
def usage(days: int = 30, user: dict = Depends(require_auth),
          db: Session = Depends(get_db)):
    """Per-key daily usage over the trailing window: the flushed ledger plus
    today's in-memory tail (the flush runs every few minutes, and a usage
    check right after a request must not read zero)."""
    days = max(1, min(days, 90))
    keys = db.query(ApiKey).filter(ApiKey.email == user["email"]).all()
    ids = {k.id: k.prefix for k in keys}
    if not ids:
        return {"days": days, "usage": []}
    from datetime import datetime, timedelta, timezone

    floor = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    rows = (
        db.query(ApiUsageDaily)
        .filter(ApiUsageDaily.key_id.in_(ids), ApiUsageDaily.day >= floor)
        .order_by(ApiUsageDaily.day.desc()).all()
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    merged: dict[tuple[str, int], dict] = {
        (r.day, r.key_id): {"day": r.day, "key_id": r.key_id, "prefix": ids[r.key_id],
                            "requests": r.requests, "points": r.points}
        for r in rows
    }
    for kid in ids:
        req, pts = metering.usage_today(kid)
        if req or pts:
            slot = merged.setdefault(
                (today, kid),
                {"day": today, "key_id": kid, "prefix": ids[kid], "requests": 0, "points": 0},
            )
            slot["requests"] += req
            slot["points"] += pts
    return {
        "days": days,
        "usage": sorted(merged.values(), key=lambda r: (r["day"], r["key_id"]), reverse=True),
        "note": "Today includes the un-flushed in-memory tail; history is the flushed ledger.",
    }
