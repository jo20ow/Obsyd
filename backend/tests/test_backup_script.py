"""Regression tests for deploy/backup-db.sh.

Written after the 2026-07-07 outage, where the deployed backup script reported
"Backup complete" although `gzip` had died with "No space left on device". It
then left the uncompressed ~1 GB .db behind, which its own pruner never matched
(`find -name '*.db.gz'`) — so every failure permanently ate another gigabyte.

The script is exercised as a real subprocess. `sqlite3`, `gzip` and `df` are
stubbed via PATH only where a failure has to be *provoked*; the happy path runs
the real binaries so the test cannot drift away from actual behaviour.
"""

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "backup-db.sh"


def _make_db(path: Path, rows: int = 5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    con.close()


def _make_wal_db(path: Path, rows: int = 5) -> None:
    """Production's obsyd.db runs in WAL mode, so `.backup` yields a WAL database
    too — and opening one, even read-only, materialises -shm/-wal beside it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (x INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()


def _stub(stub_dir: Path, name: str, body: str) -> None:
    stub_dir.mkdir(parents=True, exist_ok=True)
    p = stub_dir / name
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(0o755)


@pytest.fixture
def dirs(tmp_path):
    app = tmp_path / "app"
    backups = tmp_path / "backups"
    stubs = tmp_path / "stubs"
    _make_db(app / "obsyd.db")
    _make_db(app / "data" / "portwatch.db")
    backups.mkdir()
    return app, backups, stubs


def _run(app, backups, stubs=None, cwd=None, **env):
    e = dict(os.environ)
    e["OBSYD_APP_DIR"] = str(app)
    e["OBSYD_BACKUP_DIR"] = str(backups)
    e["RESEND_API_KEY"] = ""  # never send mail from the test suite
    e.update({k: str(v) for k, v in env.items()})
    if stubs is not None and stubs.exists():
        e["PATH"] = f"{stubs}:{e['PATH']}"
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=e,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _strays(backups: Path) -> list[str]:
    """Anything the run left behind that is not a finished archive."""
    return sorted(p.name for p in backups.iterdir() if not p.name.endswith(".gz"))


# ── happy path ───────────────────────────────────────────────────────────────


def test_happy_path_writes_gzipped_backups_and_exits_zero(dirs):
    app, backups, _ = dirs
    r = _run(app, backups)
    assert r.returncode == 0, r.stderr
    assert list(backups.glob("obsyd-*.db.gz")), r.stdout
    assert list(backups.glob("portwatch-*.db.gz")), r.stdout


def test_happy_path_leaves_no_uncompressed_backup(dirs):
    app, backups, _ = dirs
    r = _run(app, backups)
    assert r.returncode == 0, r.stderr
    assert _strays(backups) == []


# ── the actual bug: failures must be loud, and must not leak files ───────────


def test_gzip_failure_exits_nonzero(dirs):
    app, backups, stubs = dirs
    _stub(stubs, "gzip", "exit 1")
    r = _run(app, backups, stubs)
    assert r.returncode != 0, f"gzip failed but script reported success:\n{r.stdout}"


def test_gzip_failure_never_claims_success(dirs):
    app, backups, stubs = dirs
    _stub(stubs, "gzip", "exit 1")
    r = _run(app, backups, stubs)
    assert "complete" not in (r.stdout + r.stderr).lower()
    assert "FAIL" in (r.stdout + r.stderr)


def test_gzip_failure_leaves_no_uncompressed_backup_behind(dirs):
    """The 959 MB `obsyd-2026-07-06.db` that nothing ever pruned."""
    app, backups, stubs = dirs
    _stub(stubs, "gzip", "exit 1")
    _run(app, backups, stubs)
    assert _strays(backups) == []


def test_sqlite_backup_failure_exits_nonzero(dirs):
    app, backups, stubs = dirs
    _stub(stubs, "sqlite3", "exit 1")
    r = _run(app, backups, stubs)
    assert r.returncode != 0, f"sqlite3 failed but script reported success:\n{r.stdout}"


def test_truncated_backup_is_detected(dirs):
    """A .backup that exits 0 but writes garbage must not be accepted.

    The stub only fakes the `.backup` call — any other sqlite3 invocation (i.e.
    the script's own validation) is delegated to the real binary, so the check
    cannot be satisfied by the stub itself.
    """
    app, backups, stubs = dirs
    real = shutil.which("sqlite3")
    assert real, "sqlite3 CLI required for this test"
    _stub(
        stubs,
        "sqlite3",
        f'''case "$2" in
  .backup*)
    out=$(printf '%s' "$2" | sed -e "s/^\\.backup '//" -e "s/'$//")
    printf 'not-a-sqlite-db' > "$out"
    exit 0 ;;
  *) exec "{real}" "$@" ;;
esac''',
    )
    r = _run(app, backups, stubs)
    assert r.returncode != 0, f"garbage backup accepted:\n{r.stdout}"


# ── preflight: never start a backup that cannot fit ──────────────────────────


def test_preflight_refuses_when_free_space_insufficient(dirs):
    app, backups, stubs = dirs
    _stub(
        stubs,
        "df",
        'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
        'echo "/dev/sda1 50000000 49999900 100 100% /"',
    )
    r = _run(app, backups, stubs)
    assert r.returncode != 0
    assert "FAIL" in (r.stdout + r.stderr)


def test_preflight_runs_before_any_backup_is_attempted(dirs):
    app, backups, stubs = dirs
    marker = backups.parent / "sqlite-was-called"
    _stub(
        stubs,
        "df",
        'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
        'echo "/dev/sda1 50000000 49999900 100 100% /"',
    )
    _stub(stubs, "sqlite3", f'touch "{marker}"; exit 0')
    _run(app, backups, stubs)
    assert not marker.exists(), "preflight must abort before touching the database"


# ── self-healing: clean up what earlier broken runs left behind ──────────────


def test_orphaned_valid_backup_is_compressed_not_deleted(dirs):
    """A previous run made a good .db but died before gzip — recover it."""
    app, backups, _ = dirs
    orphan = backups / "obsyd-2026-07-06.db"
    _make_db(orphan, rows=3)
    _run(app, backups)
    assert not orphan.exists(), "orphan should no longer sit there uncompressed"
    assert (backups / "obsyd-2026-07-06.db.gz").exists(), "valid orphan must be preserved"


def test_orphaned_zero_byte_backup_is_removed(dirs):
    """`obsyd-2026-07-08.db` and friends: 0 bytes, worthless, never pruned."""
    app, backups, _ = dirs
    junk = backups / "obsyd-2026-07-08.db"
    junk.write_bytes(b"")
    _run(app, backups)
    assert not junk.exists()
    assert not (backups / "obsyd-2026-07-08.db.gz").exists()


# ── never destroy what you are inspecting ────────────────────────────────────
#
# `sqlite3 file 'PRAGMA ...'` is NOT a read-only operation. If a hot journal sits
# next to the database, opening it runs rollback recovery. For a leftover from an
# interrupted `.backup` the journal records "originally 0 pages", so the open
# truncates the file to nothing. That is how the 2026-07-06 leftover was lost —
# by a diagnostic command that looked like a read.


def _recording_sqlite3(stubs: Path, calls: Path) -> None:
    real = shutil.which("sqlite3")
    assert real, "sqlite3 CLI required for this test"
    _stub(stubs, "sqlite3", f'echo "$@" >> "{calls}"\nexec "{real}" "$@"')


def test_leftover_with_a_hot_journal_is_discarded_never_opened(dirs, tmp_path):
    app, backups, stubs = dirs
    calls = tmp_path / "sqlite-calls"
    _recording_sqlite3(stubs, calls)

    orphan = backups / "obsyd-2026-07-06.db"
    _make_db(orphan, rows=3)
    (backups / "obsyd-2026-07-06.db-journal").write_bytes(b"\x00" * 1024)

    _run(app, backups, stubs)

    assert not orphan.exists(), "interrupted backup must be discarded"
    assert not (backups / "obsyd-2026-07-06.db-journal").exists()
    assert not (backups / "obsyd-2026-07-06.db.gz").exists(), (
        "a torn backup must never be preserved as if it were good"
    )
    opened = calls.read_text() if calls.exists() else ""
    assert orphan.name not in opened, "the script opened a database with a hot journal"


def test_backup_artifacts_are_only_ever_opened_readonly(dirs, tmp_path):
    app, backups, stubs = dirs
    calls = tmp_path / "sqlite-calls"
    _recording_sqlite3(stubs, calls)
    orphan = backups / "obsyd-2026-07-06.db"
    _make_db(orphan, rows=3)

    r = _run(app, backups, stubs)
    assert r.returncode == 0, r.stdout + r.stderr

    for line in calls.read_text().splitlines():
        if str(backups) in line and ".backup" not in line:
            assert "-readonly" in line, f"backup artifact opened writably: {line}"


def test_orphaned_sidecar_without_a_database_is_removed(dirs):
    app, backups, _ = dirs
    debris = backups / "obsyd-2026-07-07.db-journal"
    debris.write_bytes(b"\x00" * 512)
    _run(app, backups)
    assert not debris.exists()


# ── the script must clean up after its own inspection ────────────────────────


def test_wal_database_backup_leaves_no_sidecars_behind(tmp_path):
    """Validating the fresh backup opens it; for a WAL database that creates
    -shm/-wal next to the archive. They must not survive the run."""
    app = tmp_path / "app"
    backups = tmp_path / "backups"
    backups.mkdir()
    _make_wal_db(app / "obsyd.db")
    _make_wal_db(app / "data" / "portwatch.db")

    r = _run(app, backups)

    assert r.returncode == 0, r.stdout + r.stderr
    assert list(backups.glob("obsyd-*.db.gz")), r.stdout
    assert _strays(backups) == []


# ── the script must not care where it was started ────────────────────────────


def test_prune_does_not_depend_on_the_callers_working_directory(dirs, tmp_path):
    """GNU `find -delete` implies -depth and restores its initial cwd. Started
    from a directory the running user cannot read — say another user's home —
    it aborts. Production hit exactly this.
    """
    app, backups, _ = dirs
    old = backups / "obsyd-2020-01-01.db.gz"
    old.write_bytes(b"x")
    os.utime(old, (0, 0))

    cage = tmp_path / "cage"
    cage.mkdir()
    cage.chmod(0o111)  # enterable, not readable
    try:
        r = _run(app, backups, cwd=cage)
    finally:
        cage.chmod(0o755)

    assert r.returncode == 0, r.stdout + r.stderr
    assert not old.exists(), "retention did not run"


def test_relative_paths_are_rejected(dirs):
    """cd'ing to a safe directory would silently relocate a relative target."""
    app, backups, _ = dirs
    r = _run(app, "relative/backups")
    assert r.returncode != 0
    assert "absolute" in (r.stdout + r.stderr).lower()


# ── retention ────────────────────────────────────────────────────────────────


def test_old_daily_backups_are_pruned(dirs):
    app, backups, _ = dirs
    old = backups / "obsyd-2020-01-01.db.gz"
    old.write_bytes(b"x")
    os.utime(old, (0, 0))  # epoch → far beyond any retention window
    _run(app, backups, OBSYD_BACKUP_RETENTION_DAYS=7)
    assert not old.exists()


def test_weekly_backups_survive_daily_retention(dirs):
    app, backups, _ = dirs
    weekly = backups / "weekly-obsyd-2026-07-05.db.gz"
    weekly.write_bytes(b"x")
    _run(app, backups, OBSYD_BACKUP_RETENTION_DAYS=7)
    assert weekly.exists()
