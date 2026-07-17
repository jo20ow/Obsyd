"""
Alert-rule runner — evaluates every active AlertRule on a schedule.

Called by APScheduler every 30 minutes. For each active rule:
  1. Skip if still within cooldown_until.
  2. Run the template evaluator against the live DB.
  3. If it triggers → persist a UserAlertEvent, set cooldown, attempt
     to send a Resend email (best-effort; failure logged, event row
     stays — the inbox still surfaces it).

Side effects beyond DB writes:
  - Resend HTTP POST per trigger.

Hardening:
  - Per-run cap so one buggy rule can't dominate a tick.
  - Each rule evaluation wrapped in try/except; one rule failing
    doesn't abort the run.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Callable

import httpx
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models.alert_rules import AlertRule, UserAlertEvent
from backend.signals.user_alert_rules import EvaluatorResult, evaluator_for

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN = timedelta(hours=6)
MAX_TRIGGERS_PER_RUN = 100


def _resend_api_key() -> str | None:
    # PAUSED (owner decision 2026-07-18): Obsyd sends no product emails. Rules
    # still evaluate and their UserAlertEvent rows land in the ALERTS feed —
    # only the Resend leg is off. Re-enable by restoring the key lookup:
    #   key = settings.resend_api_key
    #   if not key:
    #       return None
    #   return key.get_secret_value() if hasattr(key, "get_secret_value") else key
    return None


def _send_alert_email(api_key: str, email: str, title: str, detail: str) -> bool:
    html = (
        "<!DOCTYPE html><html><body style=\"font-family:'Courier New',monospace;"
        "background:#09090b;color:#d4d4d4;padding:24px\">"
        "<div style=\"max-width:520px;margin:0 auto;border:1px solid #27272a;"
        "padding:24px;background:#0a0a12\">"
        "<div style=\"font-size:14px;color:#22d3ee;font-weight:bold;"
        "letter-spacing:3px;margin-bottom:16px\">OBSYD · ALERT</div>"
        f"<div style=\"font-size:14px;color:#fafafa;margin-bottom:12px\"><b>{title}</b></div>"
        f"<div style=\"font-size:12px;color:#a3a3a3;line-height:1.6;margin-bottom:20px\">{detail}</div>"
        "<a href=\"https://obsyd.dev/app#alerts\" "
        "style=\"display:inline-block;padding:8px 18px;background:#22d3ee;"
        "color:#09090b;text-decoration:none;font-size:11px;letter-spacing:1px\">"
        "OPEN ALERTS INBOX →</a>"
        "<div style=\"margin-top:24px;padding-top:12px;border-top:1px solid #27272a;"
        "font-size:9px;color:#525252\">"
        "Triggered by an alert rule you configured on obsyd.dev. "
        "<a href=\"https://obsyd.dev/app#alerts\" style=\"color:#525252\">Manage rules</a>."
        "</div></div></body></html>"
    )
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "from": "OBSYD <briefing@obsyd.dev>",
                    "to": [email],
                    "subject": f"[OBSYD] {title}",
                    "html": html,
                },
            )
            resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning("Alert email send failed for %s: %s", email, e)
        return False


def _eval_one(db: Session, rule: AlertRule, evaluator: Callable, now: datetime) -> EvaluatorResult | None:
    try:
        params = json.loads(rule.params or "{}")
    except json.JSONDecodeError:
        return None
    try:
        return evaluator(db, params, now=now)
    except Exception as e:
        logger.warning("Rule %d (%s) evaluator raised: %s", rule.id, rule.rule_type, e)
        return None


def process_alert_rules(
    db_factory=SessionLocal,
    *,
    now: datetime | None = None,
    cap: int = MAX_TRIGGERS_PER_RUN,
    send_email: bool = True,
) -> dict:
    """Scheduler entry point. Returns counters for diagnostics + tests.

    Args:
        db_factory: sessionmaker to create the worker session.
        now: override "now" (testing).
        cap: max triggers per run (protects against runaway rules).
        send_email: if False, skip Resend calls (used by tests).
    """
    now = now or datetime.utcnow()
    api_key = _resend_api_key() if send_email else None
    counters = {"evaluated": 0, "triggered": 0, "emailed": 0, "skipped_cooldown": 0}

    db = db_factory()
    try:
        rules = (
            db.query(AlertRule)
            .filter(AlertRule.is_active.is_(True))
            .order_by(AlertRule.id.asc())
            .all()
        )
        triggered_in_run = 0
        for rule in rules:
            if triggered_in_run >= cap:
                break
            if rule.cooldown_until and rule.cooldown_until > now:
                counters["skipped_cooldown"] += 1
                continue
            evaluator = evaluator_for(rule.rule_type)
            if evaluator is None:
                continue

            counters["evaluated"] += 1
            rule.last_evaluated_at = now

            result = _eval_one(db, rule, evaluator, now)
            if result is None:
                continue

            event = UserAlertEvent(
                rule_id=rule.id,
                email=rule.email,
                triggered_at=now,
                title=result.title,
                detail=result.detail,
                payload=json.dumps(result.payload),
            )
            db.add(event)
            rule.last_triggered_at = now
            rule.cooldown_until = now + DEFAULT_COOLDOWN
            counters["triggered"] += 1
            triggered_in_run += 1

            if send_email and api_key:
                ok = _send_alert_email(api_key, rule.email, result.title, result.detail)
                if ok:
                    event.email_sent_at = now
                    counters["emailed"] += 1

        db.commit()
    finally:
        db.close()

    if counters["triggered"]:
        logger.info("alert runner: %s", counters)
    return counters
