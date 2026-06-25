"""
Per-user watchlist CRUD (Pro-tier) — the keystone of "Personal Supply-Watch".

The /catalog endpoint is ungated (Free users see what they'd get on Pro);
reading and writing a user's own items requires Pro. Legal (kind, key) pairs
are derived from the single sources of truth: the criticality material list,
the geofence chokepoint zones, and the power bidding zones — so the watchlist
can never drift from what the app actually tracks.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from backend.auth.dependencies import require_auth
from backend.database import SessionLocal
from backend.geofences.zones import ZONES
from backend.models.watchlist import WatchlistItem
from backend.power.zones import POWER_ZONES
from backend.routes.atlas import CRITICAL_MATERIALS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


def _build_catalog() -> dict[str, dict[str, str]]:
    """Legal {kind: {key: label}}. Materials from the criticality list; zones
    from the chokepoint geofences + the power bidding zones (no key overlap)."""
    materials = {key: label for key, label, _kind, _spec in CRITICAL_MATERIALS}
    zones = {z["name"]: z["display_name"] for z in ZONES}
    zones.update({k: v["label"] for k, v in POWER_ZONES.items()})
    return {"material": materials, "zone": zones}


VALID_KEYS = _build_catalog()


def _serialise(it: WatchlistItem) -> dict:
    return {
        "id": it.id,
        "kind": it.kind,
        "key": it.key,
        "label": it.label,
        "created_at": it.created_at.isoformat() if it.created_at else None,
    }


class CreateItemBody(BaseModel):
    kind: str
    key: str


@router.get("/catalog")
async def watchlist_catalog():
    """The materials/zones a user can watch. Ungated — upsell discovery."""
    return {
        "material": [{"key": k, "label": v} for k, v in VALID_KEYS["material"].items()],
        "zone": [{"key": k, "label": v} for k, v in VALID_KEYS["zone"].items()],
    }


@router.get("")
async def list_watchlist(user: dict = Depends(require_auth)):
    db = SessionLocal()
    try:
        items = (
            db.query(WatchlistItem)
            .filter(WatchlistItem.email == user["email"])
            .order_by(WatchlistItem.created_at.desc())
            .all()
        )
        return {"items": [_serialise(i) for i in items]}
    finally:
        db.close()


@router.post("")
async def add_watchlist(body: CreateItemBody, user: dict = Depends(require_auth)):
    if body.kind not in VALID_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown kind: {body.kind}")
    if body.key not in VALID_KEYS[body.kind]:
        raise HTTPException(status_code=422, detail=f"unknown {body.kind} key: {body.key}")
    label = VALID_KEYS[body.kind][body.key]

    db = SessionLocal()
    try:

        def _find():
            return (
                db.query(WatchlistItem)
                .filter(
                    WatchlistItem.email == user["email"],
                    WatchlistItem.kind == body.kind,
                    WatchlistItem.key == body.key,
                )
                .first()
            )

        existing = _find()
        if existing:
            return _serialise(existing)  # idempotent — saving twice is a no-op

        item = WatchlistItem(email=user["email"], kind=body.kind, key=body.key, label=label)
        db.add(item)
        try:
            db.commit()
        except IntegrityError:
            # Lost a race on the unique constraint — return the winner.
            db.rollback()
            return _serialise(_find())
        db.refresh(item)
        return _serialise(item)
    finally:
        db.close()


@router.delete("/{item_id}")
async def delete_watchlist(item_id: int, user: dict = Depends(require_auth)):
    db = SessionLocal()
    try:
        item = (
            db.query(WatchlistItem)
            .filter(WatchlistItem.id == item_id, WatchlistItem.email == user["email"])
            .first()
        )
        if not item:
            raise HTTPException(status_code=404, detail="item not found")
        db.delete(item)
        db.commit()
        return {"status": "deleted", "id": item_id}
    finally:
        db.close()
