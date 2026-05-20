"""
Tests for the trial onboarding drip processor.

We never hit real Resend — the test patches `_send_html` to record calls
and return success. Stage-advancement logic and age-threshold gating are
the actual code under test.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import backend.database as _db_module
from backend.models.subscription import Subscription
from backend.notifications import trial_drip


@pytest.fixture
def session_factory(db_session):
    """Return the test-patched SessionLocal so process_trial_drip can open
    and close its own sessions without invalidating the test-fixture session."""
    return _db_module.SessionLocal


@pytest.fixture
def fake_resend(monkeypatch):
    """Capture every drip send instead of hitting Resend."""
    calls: list[dict] = []

    def fake_key():
        return "test-api-key"

    def fake_send(client, api_key, email, subject, html):
        calls.append({"email": email, "subject": subject, "html_len": len(html)})
        return True

    monkeypatch.setattr(trial_drip, "_resend_api_key", fake_key)
    monkeypatch.setattr(trial_drip, "_send_html", fake_send)
    return calls


def _seed_trial(db_session, *, email: str, days_old: float, drip_stage: int) -> Subscription:
    sub = Subscription(
        email=email,
        status="trialing",
        plan="pro",
        created_at=datetime.utcnow() - timedelta(days=days_old),
        trial_ends_at=datetime.utcnow() + timedelta(days=14 - days_old),
        drip_stage=drip_stage,
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)
    return sub


def test_no_drip_for_subs_outside_trial_state(db_session, session_factory, fake_resend):
    # active LS subs should never be touched
    paid = Subscription(email="paid@example.com", status="active", plan="pro", drip_stage=0)
    db_session.add(paid)
    db_session.commit()

    result = trial_drip.process_trial_drip(db_factory=session_factory)
    assert result["total"] == 0
    assert fake_resend == []


def test_day2_fires_only_after_48h(db_session, session_factory, fake_resend):
    fresh = _seed_trial(db_session, email="fresh@example.com", days_old=1.0, drip_stage=0)
    overdue = _seed_trial(db_session, email="overdue@example.com", days_old=2.5, drip_stage=0)

    result = trial_drip.process_trial_drip(db_factory=session_factory)
    assert result["total"] == 1
    assert len(fake_resend) == 1
    assert fake_resend[0]["email"] == "overdue@example.com"

    db_session.refresh(fresh)
    db_session.refresh(overdue)
    assert fresh.drip_stage == 0  # not advanced
    assert overdue.drip_stage == 1  # advanced to day-2


def test_day5_fires_only_after_stage1(db_session, session_factory, fake_resend):
    # User crossed day-5 age but is still at stage 0 — must NOT skip stage 1.
    sub = _seed_trial(db_session, email="skip@example.com", days_old=6.0, drip_stage=0)

    result = trial_drip.process_trial_drip(db_factory=session_factory)
    assert result["total"] == 1
    db_session.refresh(sub)
    assert sub.drip_stage == 1  # day-2 sent (one advance per run)


def test_full_progression_over_multiple_runs(db_session, session_factory, fake_resend):
    sub = _seed_trial(db_session, email="alice@example.com", days_old=7.0, drip_stage=0)

    # run 1: stage 0 -> 1 (day-2 email)
    trial_drip.process_trial_drip(db_factory=session_factory)
    db_session.refresh(sub)
    assert sub.drip_stage == 1

    # run 2: stage 1 -> 2 (day-5 email)
    trial_drip.process_trial_drip(db_factory=session_factory)
    db_session.refresh(sub)
    assert sub.drip_stage == 2

    # run 3: nothing to advance (stage 2 is the last drip; mark done)
    trial_drip.process_trial_drip(db_factory=session_factory)
    db_session.refresh(sub)
    # stage stays at 2 because there's no stage-3 template; future runs
    # would auto-mark to 3 via the safety branch.
    assert sub.drip_stage in (2, 3)
    assert len(fake_resend) == 2


def test_run_respects_budget(db_session, session_factory, fake_resend):
    for i in range(5):
        _seed_trial(db_session, email=f"u{i}@example.com", days_old=3.0, drip_stage=0)

    result = trial_drip.process_trial_drip(db_factory=session_factory, budget=2)
    assert result["total"] == 2
    assert len(fake_resend) == 2


def test_failed_send_does_not_advance_stage(db_session, session_factory, monkeypatch):
    # _send_html returns False -> stage must NOT advance, so we retry next run.
    monkeypatch.setattr(trial_drip, "_resend_api_key", lambda: "test-api-key")
    monkeypatch.setattr(trial_drip, "_send_html", lambda *a, **k: False)
    sub = _seed_trial(db_session, email="flaky@example.com", days_old=3.0, drip_stage=0)

    result = trial_drip.process_trial_drip(db_factory=session_factory)
    assert result["total"] == 0
    db_session.refresh(sub)
    assert sub.drip_stage == 0


def test_no_api_key_skips_processor(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(trial_drip, "_resend_api_key", lambda: None)
    _seed_trial(db_session, email="x@example.com", days_old=3.0, drip_stage=0)

    result = trial_drip.process_trial_drip(db_factory=session_factory)
    assert result == {"skipped": "no_api_key"}
