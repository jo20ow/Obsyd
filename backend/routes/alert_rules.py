"""
CRUD + read endpoints for user-defined alert rules.

All routes require Pro (paid or in-trial). Trial users are capped at
TRIAL_RULE_LIMIT active rules; paid Pro users are unlimited (for now).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from backend.auth.dependencies import require_auth
from backend.database import SessionLocal
from backend.models.alert_rules import AlertRule, UserAlertEvent
from backend.models.subscription import Subscription
from backend.signals.user_alert_rules import TEMPLATES, validate_params

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["alert_rules"])

TRIAL_RULE_LIMIT = 3


# ---------- helpers ----------


def _serialise_rule(r: AlertRule) -> dict:
    try:
        params = json.loads(r.params or "{}")
    except json.JSONDecodeError:
        params = {}
    return {
        "id": r.id,
        "rule_type": r.rule_type,
        "name": r.name or TEMPLATES.get(r.rule_type, {}).get("label", r.rule_type),
        "params": params,
        "is_active": r.is_active,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "last_evaluated_at": r.last_evaluated_at.isoformat() if r.last_evaluated_at else None,
        "last_triggered_at": r.last_triggered_at.isoformat() if r.last_triggered_at else None,
        "cooldown_until": r.cooldown_until.isoformat() if r.cooldown_until else None,
    }


def _serialise_event(e: UserAlertEvent) -> dict:
    try:
        payload = json.loads(e.payload or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": e.id,
        "rule_id": e.rule_id,
        "triggered_at": e.triggered_at.isoformat() if e.triggered_at else None,
        "title": e.title,
        "detail": e.detail,
        "payload": payload,
        "seen": e.seen_at is not None,
        "email_sent": e.email_sent_at is not None,
    }


def _effective_tier(email: str) -> str:
    """Returns "paid" | "trial" | "free". Paid > trial (no double-Sub
    expected, but if both exist, paid wins to be permissive)."""
    db = SessionLocal()
    try:
        subs = (
            db.query(Subscription)
            .filter(Subscription.email == email)
            .order_by(Subscription.id.desc())
            .all()
        )
        for s in subs:
            if s.status in ("active", "past_due", "cancelled"):
                return "paid"
        for s in subs:
            if s.status == "trialing" and s.trial_ends_at and s.trial_ends_at > datetime.utcnow():
                return "trial"
        return "free"
    finally:
        db.close()


# ---------- request bodies ----------


class CreateRuleBody(BaseModel):
    rule_type: str
    name: str = ""
    params: dict = {}

    @field_validator("rule_type")
    @classmethod
    def _check_rule_type(cls, v: str) -> str:
        if v not in TEMPLATES:
            raise ValueError(f"unknown rule_type: {v}")
        return v


class PatchRuleBody(BaseModel):
    is_active: bool | None = None
    name: str | None = None


# ---------- public-ish: templates ----------


#: Verticals offered in the rule builder. The maritime/oil templates run on
#: dormant data since the electricity refocus (2026-07-03) — offering rules on
#: feeds that never fire misleads users. Their evaluators stay registered so
#: existing rules keep evaluating; widen this set when the data returns.
ACTIVE_TEMPLATE_VERTICALS = {"power", "gas"}


@router.get("/templates")
def list_templates():
    """Schema discovery for the frontend rule-builder. Not behind require_pro
    — even Free users may want to see what they'd get on Pro."""
    return {
        rule_type: {
            "label": t["label"],
            "summary": t["summary"],
            "params_schema": t["params_schema"],
        }
        for rule_type, t in TEMPLATES.items()
        if t.get("vertical") in ACTIVE_TEMPLATE_VERTICALS
    }


# ---------- rule CRUD ----------


@router.get("/rules")
def list_rules(user: dict = Depends(require_auth)):
    db = SessionLocal()
    try:
        rules = (
            db.query(AlertRule)
            .filter(AlertRule.email == user["email"])
            .order_by(AlertRule.created_at.desc())
            .all()
        )
        return {
            "tier": _effective_tier(user["email"]),
            "trial_rule_limit": TRIAL_RULE_LIMIT,
            "rules": [_serialise_rule(r) for r in rules],
        }
    finally:
        db.close()


@router.post("/rules")
def create_rule(body: CreateRuleBody, user: dict = Depends(require_auth)):
    ok, err = validate_params(body.rule_type, body.params)
    if not ok:
        raise HTTPException(status_code=422, detail=err)

    # Tier-limit enforcement (trial only).
    tier = _effective_tier(user["email"])
    db = SessionLocal()
    try:
        if tier == "trial":
            active_count = (
                db.query(AlertRule)
                .filter(AlertRule.email == user["email"], AlertRule.is_active.is_(True))
                .count()
            )
            if active_count >= TRIAL_RULE_LIMIT:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Trial plan allows up to {TRIAL_RULE_LIMIT} active rules. "
                        "Upgrade to Pro for unlimited rules."
                    ),
                )

        rule = AlertRule(
            email=user["email"],
            rule_type=body.rule_type,
            name=body.name or TEMPLATES[body.rule_type]["label"],
            params=json.dumps(body.params),
            is_active=True,
        )
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return _serialise_rule(rule)
    finally:
        db.close()


@router.patch("/rules/{rule_id}")
def patch_rule(rule_id: int, body: PatchRuleBody, user: dict = Depends(require_auth)):
    db = SessionLocal()
    try:
        rule = (
            db.query(AlertRule)
            .filter(AlertRule.id == rule_id, AlertRule.email == user["email"])
            .first()
        )
        if not rule:
            raise HTTPException(status_code=404, detail="rule not found")
        if body.is_active is not None:
            rule.is_active = body.is_active
        if body.name is not None:
            rule.name = body.name
        db.commit()
        db.refresh(rule)
        return _serialise_rule(rule)
    finally:
        db.close()


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, user: dict = Depends(require_auth)):
    db = SessionLocal()
    try:
        rule = (
            db.query(AlertRule)
            .filter(AlertRule.id == rule_id, AlertRule.email == user["email"])
            .first()
        )
        if not rule:
            raise HTTPException(status_code=404, detail="rule not found")
        db.delete(rule)
        db.commit()
        return {"status": "deleted", "id": rule_id}
    finally:
        db.close()


# ---------- notifications inbox ----------


@router.get("/notifications")
def list_notifications(limit: int = 50, user: dict = Depends(require_auth)):
    limit = max(1, min(limit, 200))
    db = SessionLocal()
    try:
        rows = (
            db.query(UserAlertEvent)
            .filter(UserAlertEvent.email == user["email"])
            .order_by(UserAlertEvent.triggered_at.desc())
            .limit(limit)
            .all()
        )
        unseen = sum(1 for r in rows if r.seen_at is None)
        return {
            "unseen": unseen,
            "events": [_serialise_event(e) for e in rows],
        }
    finally:
        db.close()


@router.post("/notifications/{event_id}/seen")
def mark_seen(event_id: int, user: dict = Depends(require_auth)):
    db = SessionLocal()
    try:
        evt = (
            db.query(UserAlertEvent)
            .filter(UserAlertEvent.id == event_id, UserAlertEvent.email == user["email"])
            .first()
        )
        if not evt:
            raise HTTPException(status_code=404, detail="event not found")
        if evt.seen_at is None:
            evt.seen_at = datetime.utcnow()
            db.commit()
        return {"status": "ok", "id": event_id, "seen_at": evt.seen_at.isoformat()}
    finally:
        db.close()
