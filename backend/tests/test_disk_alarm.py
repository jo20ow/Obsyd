"""Tests for deploy/disk-alarm.sh.

The 2026-07-07 outage went unnoticed for two days: the disk hit 100 %, journald
could no longer persist anything, and the in-app collector watchdog therefore
never even logged — let alone mailed. So the alarm must hold to three rules:

  1. it must not depend on the app, the database, or journald;
  2. it must not write to the filesystem it is monitoring (that one is full);
  3. a breach must be loud (non-zero exit) even when no mail channel is set up.

`df` and `curl` are stubbed via PATH so the alarm can be driven to any usage
level without a full disk and without sending real mail.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "disk-alarm.sh"


def _stub(stub_dir: Path, name: str, body: str) -> None:
    stub_dir.mkdir(parents=True, exist_ok=True)
    p = stub_dir / name
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(0o755)


def _stub_df(stub_dir: Path, pct: int) -> None:
    used = pct * 500_000
    avail = 50_000_000 - used
    _stub(
        stub_dir,
        "df",
        'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
        f'echo "/dev/sda1 50000000 {used} {avail} {pct}% /"',
    )


@pytest.fixture
def env(tmp_path):
    stubs = tmp_path / "stubs"
    state = tmp_path / "state"
    watched = tmp_path / "watched"
    watched.mkdir()
    curl_log = tmp_path / "curl.log"
    # One line per invocation; never echo "$*" — a JSON payload would break the count.
    _stub(stubs, "curl", f'printf "CALLED\\n" >> "{curl_log}"; exit 0')
    return stubs, state, watched, curl_log


def _run(stubs, state, watched, **extra):
    e = dict(os.environ)
    e["PATH"] = f"{stubs}:{e['PATH']}"
    e["OBSYD_DISK_PATH"] = str(watched)
    e["OBSYD_ALARM_STATE_DIR"] = str(state)
    e["RESEND_API_KEY"] = "test-key"
    e["OBSYD_ALERT_EMAIL"] = "ops@example.com"
    e.update({k: str(v) for k, v in extra.items()})
    return subprocess.run(
        ["bash", str(SCRIPT)], env=e, capture_output=True, text=True, timeout=30
    )


def _alerts(curl_log: Path) -> int:
    return len(curl_log.read_text().splitlines()) if curl_log.exists() else 0


# ── healthy ──────────────────────────────────────────────────────────────────


def test_below_threshold_exits_zero(env):
    stubs, state, watched, _ = env
    _stub_df(stubs, 50)
    r = _run(stubs, state, watched)
    assert r.returncode == 0, r.stderr


def test_below_threshold_sends_no_alert(env):
    stubs, state, watched, curl_log = env
    _stub_df(stubs, 50)
    _run(stubs, state, watched)
    assert _alerts(curl_log) == 0


def test_cron_spike_below_threshold_is_not_an_alarm(env):
    """prod-task.sh transiently takes the disk to ~80 %. That is normal."""
    stubs, state, watched, curl_log = env
    _stub_df(stubs, 80)
    r = _run(stubs, state, watched)
    assert r.returncode == 0
    assert _alerts(curl_log) == 0


# ── breach ───────────────────────────────────────────────────────────────────


def test_above_warn_threshold_exits_nonzero(env):
    stubs, state, watched, _ = env
    _stub_df(stubs, 90)
    r = _run(stubs, state, watched)
    assert r.returncode != 0


def test_above_warn_threshold_sends_one_alert(env):
    stubs, state, watched, curl_log = env
    _stub_df(stubs, 90)
    _run(stubs, state, watched)
    assert _alerts(curl_log) == 1


def test_breach_is_loud_even_without_a_mail_channel(env):
    stubs, state, watched, curl_log = env
    _stub_df(stubs, 90)
    r = _run(stubs, state, watched, RESEND_API_KEY="")
    assert r.returncode != 0
    assert _alerts(curl_log) == 0
    assert "WARN" in (r.stdout + r.stderr) or "CRIT" in (r.stdout + r.stderr)


# ── the alarm must survive the condition it reports ──────────────────────────


def test_never_writes_into_the_monitored_filesystem(env):
    stubs, state, watched, _ = env
    _stub_df(stubs, 97)
    before = set(watched.rglob("*"))
    _run(stubs, state, watched)
    assert set(watched.rglob("*")) == before, "alarm wrote to the full filesystem"


# ── cooldown ─────────────────────────────────────────────────────────────────


def test_repeated_breach_within_cooldown_alerts_once(env):
    stubs, state, watched, curl_log = env
    _stub_df(stubs, 90)
    _run(stubs, state, watched)
    _run(stubs, state, watched)
    assert _alerts(curl_log) == 1


def test_breach_realerts_after_cooldown_expires(env):
    stubs, state, watched, curl_log = env
    _stub_df(stubs, 90)
    _run(stubs, state, watched, OBSYD_ALARM_COOLDOWN_SEC=0)
    _run(stubs, state, watched, OBSYD_ALARM_COOLDOWN_SEC=0)
    assert _alerts(curl_log) == 2


def test_alert_key_can_be_read_from_an_explicit_env_file(env, tmp_path):
    """cron does not source the app's .env, so the alarm must be told where it is."""
    stubs, state, watched, curl_log = env
    _stub_df(stubs, 90)
    env_file = tmp_path / "obsyd.env"
    env_file.write_text('DATABASE_URL=x\nRESEND_API_KEY="re_from_file"\n')
    _run(stubs, state, watched, RESEND_API_KEY="", OBSYD_ENV_FILE=str(env_file))
    assert _alerts(curl_log) == 1


def test_no_credentials_are_read_from_disk_unless_asked(env):
    """Without OBSYD_ENV_FILE the alarm must not go hunting for secrets."""
    stubs, state, watched, curl_log = env
    _stub_df(stubs, 90)
    r = _run(stubs, state, watched, RESEND_API_KEY="")
    assert _alerts(curl_log) == 0
    assert r.returncode != 0


def test_critical_usage_alerts_even_while_warn_is_cooling_down(env):
    stubs, state, watched, curl_log = env
    _stub_df(stubs, 90)
    _run(stubs, state, watched)
    _stub_df(stubs, 97)
    _run(stubs, state, watched)
    assert _alerts(curl_log) == 2, "a critical disk must escalate past the warn cooldown"
