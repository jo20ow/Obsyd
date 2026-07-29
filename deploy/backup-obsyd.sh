#!/usr/bin/env bash
# OBSYD daily DB snapshot + secrets copy, with a healthchecks.io dead-man ping.
#
# Install (obsyd user's crontab; 04:10 UTC — after the nightly jobs, before the
# 09:00 UTC watchdog):
#
#   10 4 * * * /home/obsyd/obsyd/deploy/backup-obsyd.sh >> /home/obsyd/obsyd/logs/backup-obsyd.log 2>&1
#
# What it does, in order — ANY failure exits non-zero WITHOUT pinging, because the
# missed ping IS the alarm (healthchecks.io fires when the daily ping goes missing):
#   1. Online-consistent snapshot of obsyd.db via the venv Python's sqlite3.backup()
#      API (safe mid-write, no `sqlite3` CLI required — unlike deploy/backup-db.sh,
#      which this script supersedes for the power-only desk; do NOT cron both, they
#      would double the disk footprint) + PRAGMA quick_check on the copy.
#   2. gzip + gzip -t verification.
#   3. Copy .env alongside as env-YYYYMMDD (chmod 600) — the API tokens are as
#      unrecoverable as the data in a disaster.
#   4. Rotation: keep the last 7 dailies (+ their env copies) and the last 4
#      Sunday snapshots; delete older. Only files matching THIS script's naming
#      (obsyd-YYYYMMDD.db.gz / sunday-obsyd-YYYYMMDD.db.gz / env-YYYYMMDD) are
#      touched — backup-db.sh leftovers (obsyd-YYYY-MM-DD.db.gz) are left alone.
#   5. Only after ALL of the above: ping HEALTHCHECKS_BACKUP_URL (from the
#      environment, else read from the prod .env). Unset URL = no ping, still
#      exit 0 — the cron is safe to install before the healthchecks account exists.
#
# RESTORE (offsite copy → workstation → VPS; see deploy/OPS.md for the full runbook):
#   scp <admin>@<vps>:/home/obsyd/backups/obsyd-YYYYMMDD.db.gz .   # pick a date
#   gunzip obsyd-YYYYMMDD.db.gz                                    # sanity-check locally
#   # on the VPS:
#   sudo systemctl stop obsyd
#   sudo -u obsyd gunzip -kc /home/obsyd/backups/obsyd-YYYYMMDD.db.gz \
#       > /home/obsyd/obsyd/obsyd.db.restored
#   sudo -u obsyd mv /home/obsyd/obsyd/obsyd.db.restored /home/obsyd/obsyd/obsyd.db
#   sudo -u obsyd rm -f /home/obsyd/obsyd/obsyd.db-wal /home/obsyd/obsyd/obsyd.db-shm
#   sudo systemctl start obsyd
#   # then check https://obsyd.dev/api/v1/status
#
# Paths/retention are overridable via environment (defaults below). No secrets are
# ever echoed — the ping URL and the .env contents stay out of stdout/stderr.

set -euo pipefail
umask 077  # backups carry the .env — nothing this script writes may be group/world-readable

APP_DIR="${OBSYD_APP_DIR:-/home/obsyd/obsyd}"
DB_PATH="${OBSYD_DB_PATH:-$APP_DIR/obsyd.db}"
ENV_FILE="${OBSYD_ENV_FILE:-$APP_DIR/.env}"
BACKUP_DIR="${OBSYD_BACKUP_DIR:-/home/obsyd/backups}"
VENV_PY="${OBSYD_VENV_PY:-$APP_DIR/.venv/bin/python}"
KEEP_DAILY="${OBSYD_BACKUP_KEEP_DAILY:-7}"
KEEP_SUNDAY="${OBSYD_BACKUP_KEEP_SUNDAY:-4}"

STAMP=$(date -u +%Y%m%d)
DOW=$(date -u +%u)  # 7 = Sunday
OUT="$BACKUP_DIR/obsyd-$STAMP.db"

fail() {
    echo "[backup-obsyd] FAIL: $1" >&2
    # never leave today's partial artifacts behind (an uncompressed .db once
    # filled the disk and took the site down — see backup-db.sh's war story)
    rm -f "$OUT" "$OUT-journal" "$OUT-wal" "$OUT-shm" "$OUT.gz" \
        "$BACKUP_DIR/sunday-obsyd-$STAMP.db.gz" "$BACKUP_DIR/env-$STAMP"
    exit 1
}
trap 'fail "unexpected error on line $LINENO"' ERR

[ -f "$DB_PATH" ] || fail "database not found: $DB_PATH"
[ -x "$VENV_PY" ] || fail "venv python not found: $VENV_PY"
mkdir -p "$BACKUP_DIR"

# ── 1. online-consistent snapshot + integrity check (sqlite3.backup API) ──────
rm -f "$OUT" "$OUT.gz"
"$VENV_PY" - "$DB_PATH" "$OUT" <<'PY' || fail "sqlite3.backup() snapshot failed"
import sqlite3, sys

src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
try:
    with dst:
        src.backup(dst)  # page-consistent even while the app is writing
    verdict = dst.execute("PRAGMA quick_check(1)").fetchone()[0]
    if verdict != "ok":
        raise SystemExit(f"quick_check on the snapshot failed: {verdict}")
finally:
    dst.close()
    src.close()
PY
rm -f "$OUT-journal" "$OUT-wal" "$OUT-shm"  # sidecars materialised by the check
[ -s "$OUT" ] || fail "snapshot is empty"

# ── 2. compress + verify ──────────────────────────────────────────────────────
gzip -f "$OUT" || fail "gzip failed"
gzip -t "$OUT.gz" || fail "gzip verification failed"

# ── 3. Sunday copy (kept on a longer leash by the rotation below) ─────────────
if [ "$DOW" = "7" ]; then
    cp "$OUT.gz" "$BACKUP_DIR/sunday-obsyd-$STAMP.db.gz" || fail "sunday copy failed"
fi

# ── 4. secrets copy — set OBSYD_ENV_FILE="" to opt out explicitly ─────────────
if [ -n "$ENV_FILE" ]; then
    [ -f "$ENV_FILE" ] || fail ".env not found: $ENV_FILE (set OBSYD_ENV_FILE=\"\" to skip)"
    install -m 600 "$ENV_FILE" "$BACKUP_DIR/env-$STAMP" || fail "env copy failed"
fi

# ── 5. rotation (count-based on the datestamped names, newest kept) ───────────
prune() {  # $1 = anchored filename regex, $2 = how many newest to keep
    ls -1 "$BACKUP_DIR" | { grep -E "$1" || true; } | sort -r | tail -n +"$(($2 + 1))" \
        | while IFS= read -r f; do
            echo "[backup-obsyd] pruning $f"
            rm -f -- "$BACKUP_DIR/$f"
        done
}
prune '^obsyd-[0-9]{8}\.db\.gz$' "$KEEP_DAILY"
prune '^sunday-obsyd-[0-9]{8}\.db\.gz$' "$KEEP_SUNDAY"
prune '^env-[0-9]{8}$' "$KEEP_DAILY"

echo "[backup-obsyd] OK: $(basename "$OUT").gz ($(du -h "$OUT.gz" | cut -f1)), $(ls -1 "$BACKUP_DIR" | wc -l | tr -d ' ') files retained"

# ── 6. dead-man ping — ONLY on full success, never on failure ─────────────────
# The backup is complete and verified from here on: a ping failure must still exit
# non-zero (so the cron log shows it), but must NOT delete the good snapshot.
trap - ERR
if [ -z "${HEALTHCHECKS_BACKUP_URL:-}" ] && [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ]; then
    HEALTHCHECKS_BACKUP_URL=$(grep -E '^HEALTHCHECKS_BACKUP_URL=' "$ENV_FILE" | tail -n 1 \
        | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true)
fi
if [ -n "${HEALTHCHECKS_BACKUP_URL:-}" ]; then
    if ! curl -fsS -m 10 --retry 3 -o /dev/null "$HEALTHCHECKS_BACKUP_URL"; then
        echo "[backup-obsyd] FAIL: healthchecks ping failed (snapshot itself is on disk and intact)" >&2
        exit 1
    fi
    echo "[backup-obsyd] healthchecks pinged"
fi
