"""Scheduler job registration — pins the price-refresh cadence.

The afternoon top-up (power_prices_afternoon, 15:10 UTC) exists because IT (all
seven zones) and NL publish their next-day SDAC results after the 12:00 midday
pass, and the midday raw-cache month blob then satisfies every later read until
the 22:30 nightly overwrite — tomorrow's prices were missing for ~7.5-11.5 h.
These tests pin that all three price passes stay registered with unique ids.
"""
from __future__ import annotations

import backend.collectors.scheduler as sched_mod


class _RecordingScheduler:
    """Stands in for the module-global AsyncIOScheduler: records add_job calls."""

    def __init__(self):
        self.jobs: dict[str, object] = {}

    def add_job(self, func, trigger, *, id, **kwargs):  # noqa: A002 - mirrors APScheduler
        assert id not in self.jobs, f"duplicate scheduler job id: {id}"
        self.jobs[id] = (func, trigger)

    def start(self):
        pass


def _registered_jobs(monkeypatch) -> dict[str, object]:
    rec = _RecordingScheduler()
    monkeypatch.setattr(sched_mod, "scheduler", rec)
    sched_mod.start_scheduler()
    return rec.jobs


def test_all_three_price_passes_registered_with_unique_ids(monkeypatch):
    jobs = _registered_jobs(monkeypatch)
    # _RecordingScheduler.add_job already asserts id uniqueness across ALL jobs.
    for job_id in ("power_spark_daily", "power_prices_midday", "power_prices_afternoon"):
        assert job_id in jobs, f"{job_id} missing from the schedule"


def test_afternoon_price_topup_cron_is_1510_utc(monkeypatch):
    jobs = _registered_jobs(monkeypatch)
    func, trigger = jobs["power_prices_afternoon"]
    assert func is sched_mod._run_power_prices_afternoon
    assert str(trigger) == "cron[hour='15', minute='10']"


def test_existing_price_jobs_keep_their_crons(monkeypatch):
    """The afternoon job is ADDITIVE — midday and nightly stay as they were."""
    jobs = _registered_jobs(monkeypatch)
    assert str(jobs["power_prices_midday"][1]) == "cron[hour='12', minute='0']"
    assert str(jobs["power_spark_daily"][1]) == "cron[hour='22', minute='30']"
