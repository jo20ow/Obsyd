"""OBSYD_ROLE gating: only ingest/all run the scheduler; api serves requests only."""
from __future__ import annotations

from backend.collectors.scheduler import scheduler_role_enabled
from backend.config import settings


def test_default_role_is_all():
    # The shipped default keeps single-process behavior (scheduler on).
    assert settings.obsyd_role == "all"


def test_ingest_and_all_run_the_scheduler():
    assert scheduler_role_enabled("ingest") is True
    assert scheduler_role_enabled("all") is True


def test_api_role_disables_the_scheduler():
    assert scheduler_role_enabled("api") is False
    assert scheduler_role_enabled("API") is False
    assert scheduler_role_enabled(" api ") is False


def test_unknown_or_empty_role_fails_safe_to_enabled():
    # A typo'd env var must never silently stop ingestion.
    assert scheduler_role_enabled("") is True
    assert scheduler_role_enabled("worker") is True
    assert scheduler_role_enabled(None) is True  # type: ignore[arg-type]
