"""Tests for deploy/docker-prune.sh.

Docker's build cache had grown to 5.1 GB on the VPS and was a large part of what
filled the disk on 2026-07-07. Nothing ever pruned it.

Pruning docker on this host is dangerous in two specific ways, and both are
pinned here rather than left to a reviewer's memory:

  * `docker volume prune` would destroy valuekick's postgres data;
  * `docker image prune -a` would remove node:20-bookworm-slim, which
    valuekick's prod-task.sh pulls for every cron run, every 30 minutes.

`docker` is stubbed via PATH: the script is never allowed near a real daemon in
a test, and every subcommand it issues is recorded and asserted on.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "docker-prune.sh"
#: Absolute, so a test may strip PATH down to the stub dir without also hiding
#: the interpreter from subprocess itself.
BASH = shutil.which("bash") or "/bin/bash"


@pytest.fixture
def env(tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    calls = tmp_path / "docker-calls"
    return stubs, calls


def _stub_docker(stubs: Path, calls: Path, *, info_rc: int = 0, prune_rc: int = 0) -> None:
    script = f"""#!/bin/sh
echo "$@" >> "{calls}"
case "$1" in
  info) exit {info_rc} ;;
  builder|image) exit {prune_rc} ;;
esac
exit 0
"""
    p = stubs / "docker"
    p.write_text(script)
    p.chmod(0o755)


def _run(stubs, **env_extra):
    e = dict(os.environ)
    e["PATH"] = f"{stubs}:{e['PATH']}"
    e["RESEND_API_KEY"] = ""  # never mail from the test suite
    e.update({k: str(v) for k, v in env_extra.items()})
    return subprocess.run(
        [BASH, str(SCRIPT)], env=e, capture_output=True, text=True, timeout=30
    )


def _calls(calls: Path) -> list[str]:
    return calls.read_text().splitlines() if calls.exists() else []


# ── what it must do ──────────────────────────────────────────────────────────


def test_prunes_the_build_cache(env):
    stubs, calls = env
    _stub_docker(stubs, calls)
    r = _run(stubs)
    assert r.returncode == 0, r.stderr
    assert any(c.startswith("builder prune") for c in _calls(calls)), _calls(calls)


def test_prunes_dangling_images(env):
    stubs, calls = env
    _stub_docker(stubs, calls)
    _run(stubs)
    assert any(c.startswith("image prune") for c in _calls(calls)), _calls(calls)


# ── what it must never do ────────────────────────────────────────────────────


def test_never_prunes_volumes(env):
    """valuekick's postgres lives in a docker volume."""
    stubs, calls = env
    _stub_docker(stubs, calls)
    _run(stubs)
    assert not any("volume" in c for c in _calls(calls)), _calls(calls)


def test_never_runs_system_prune(env):
    stubs, calls = env
    _stub_docker(stubs, calls)
    _run(stubs)
    assert not any(c.startswith("system prune") for c in _calls(calls)), _calls(calls)


def test_never_removes_images_that_have_no_container(env):
    """`image prune -a` would drop node:20-bookworm-slim, which prod-task.sh
    needs every 30 minutes — it only ever runs it with --rm."""
    stubs, calls = env
    _stub_docker(stubs, calls)
    _run(stubs)
    for call in _calls(calls):
        if call.startswith("image prune"):
            assert " -a" not in f" {call}", f"dangling-only, got: {call}"
            assert "--all" not in call, f"dangling-only, got: {call}"


# ── it must not fight the daemon it cannot reach ─────────────────────────────


def test_does_nothing_when_the_daemon_is_down(env):
    """A dead dockerd is how the outage started. Do not add noise to it."""
    stubs, calls = env
    _stub_docker(stubs, calls, info_rc=1)
    r = _run(stubs)
    assert r.returncode == 0, r.stderr
    assert not any("prune" in c for c in _calls(calls)), _calls(calls)


def test_a_failing_prune_is_loud(env):
    stubs, calls = env
    _stub_docker(stubs, calls, prune_rc=1)
    r = _run(stubs)
    assert r.returncode != 0
    assert "FAIL" in (r.stdout + r.stderr)


def test_missing_docker_binary_is_not_a_crash(env):
    stubs, _ = env  # no docker stub written at all
    r = _run(stubs, PATH=str(stubs))
    assert r.returncode == 0, r.stderr
