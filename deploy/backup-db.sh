#!/usr/bin/env bash
# OBSYD SQLite backup — daily via the obsyd user's crontab.
#
#   0 3 * * * /home/obsyd/obsyd/deploy/backup-db.sh >> /home/obsyd/obsyd/logs/backup.log 2>&1
#
# Uses `sqlite3 .backup` (safe with WAL mode / running app, unlike plain cp).
# Keeps RETENTION_DAYS dailies + WEEKLY_RETENTION_DAYS Sunday snapshots.
#
# Every step is checked. A backup that cannot be written, verified or compressed
# is a FAILURE: the script says so, mails ops, cleans up after itself and exits
# non-zero. It never leaves an uncompressed .db behind — on 2026-07-07 that
# behaviour cost ~1 GB per failed run and filled the disk, which killed dockerd
# and took obsyd.dev and valuekick.de offline for two days.
#
# Offsite sync (a VPS disk is a single point of failure):
#   export OBSYD_BACKUP_REMOTE="user@host:/path/to/backups/"

set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/notify.sh
. "$SCRIPT_DIR/notify.sh"

APP_DIR="${OBSYD_APP_DIR:-/home/obsyd/obsyd}"
BACKUP_DIR="${OBSYD_BACKUP_DIR:-/home/obsyd/backups}"
RETENTION_DAYS="${OBSYD_BACKUP_RETENTION_DAYS:-7}"
WEEKLY_RETENTION_DAYS="${OBSYD_BACKUP_WEEKLY_RETENTION_DAYS:-28}"
# Headroom on top of 2.2x the raw database size (backup + gzip working set).
MIN_FREE_MB="${OBSYD_BACKUP_MIN_FREE_MB:-100}"
REMOTE="${OBSYD_BACKUP_REMOTE:-}"

DATE=$(date +%Y-%m-%d)
DOW=$(date +%u)
DBS=("$APP_DIR/obsyd.db" "$APP_DIR/data/portwatch.db")

# `find -delete` implies -depth and restores its initial working directory; if it
# cannot open that directory it refuses to run at all. Starting the script from
# another user's home (`sudo -u obsyd ... ` out of /home/jo) is enough to trip it.
# So don't depend on the caller's cwd — but reject relative paths first, or the
# chdir would silently relocate them.
case "$APP_DIR" in /*) ;; *) echo "[backup] FAIL: OBSYD_APP_DIR must be absolute" >&2; exit 1 ;; esac
case "$BACKUP_DIR" in /*) ;; *) echo "[backup] FAIL: OBSYD_BACKUP_DIR must be absolute" >&2; exit 1 ;; esac
cd /

# Artifacts this run created; removed if we bail out half-way.
partial=()

cleanup_partial() {
    local f
    for f in ${partial[@]+"${partial[@]}"}; do
        [ -e "$f" ] && rm -f "$f"
    done
}

fail() {
    local msg="$1"
    echo "[backup] FAIL: $msg" >&2
    cleanup_partial
    obsyd_alert "OBSYD ALERT: nightly backup FAILED" \
        "The OBSYD backup on $(hostname 2>/dev/null || echo unknown-host) did not complete.

Reason: ${msg}

Disk:
$(df -Ph "$BACKUP_DIR" 2>/dev/null || echo '  (df unavailable)')

No usable snapshot was written for ${DATE}. Investigate before the next run." || true
    exit 1
}

trap 'fail "unexpected error on line $LINENO"' ERR

# `sqlite3 <file> 'PRAGMA ...'` is NOT a read-only operation: if a hot journal
# sits next to the file, opening it runs rollback recovery. For an interrupted
# `.backup` the journal records "originally 0 pages", so the open truncates the
# artifact to nothing. `-readonly` makes SQLite refuse instead (SQLITE_READONLY_
# ROLLBACK) and leaves the file alone. Never drop the flag.
# Callers must guarantee the file has no sidecars (discard_interrupted runs first),
# because opening a WAL database — even read-only — materialises -shm/-wal beside
# it, and we clean those up again on the way out.
is_sqlite_db() {
    [ -s "$1" ] || return 1
    local out rc=0
    out=$(sqlite3 -readonly "$1" 'PRAGMA quick_check(1);' 2>/dev/null) || rc=1
    [ "${out%%$'\n'*}" = "ok" ] || rc=1
    rm -f "$1-shm" "$1-wal"
    return "$rc"
}

has_sidecar() {
    [ -e "$1-journal" ] || [ -e "$1-wal" ] || [ -e "$1-shm" ]
}

# ── 1. an interrupted `.backup` is not a backup — discard it, never open it ───
discard_interrupted() {
    local db side
    for db in "$BACKUP_DIR"/*.db; do
        [ -e "$db" ] || continue
        if has_sidecar "$db"; then
            echo "[backup] discarding interrupted backup (hot journal): $(basename "$db")"
            rm -f "$db" "$db-journal" "$db-wal" "$db-shm"
        fi
    done
    # sidecars whose database is already gone are debris
    for side in "$BACKUP_DIR"/*.db-journal "$BACKUP_DIR"/*.db-wal "$BACKUP_DIR"/*.db-shm; do
        [ -e "$side" ] || continue
        echo "[backup] removing orphaned sqlite sidecar: $(basename "$side")"
        rm -f "$side"
    done
}

# ── 2. discard worthless leftovers (pure filesystem, no sqlite3, no space) ────
discard_junk() {
    local f
    for f in "$BACKUP_DIR"/*.db; do
        [ -e "$f" ] || continue
        if [ ! -s "$f" ]; then
            echo "[backup] removing empty leftover: $(basename "$f")"
            rm -f "$f"
        fi
    done
}

# ── 3. recover good-but-uncompressed leftovers, freeing space before preflight ─
recover_leftovers() {
    local f
    for f in "$BACKUP_DIR"/*.db; do
        [ -e "$f" ] || continue
        if is_sqlite_db "$f"; then
            echo "[backup] recovering uncompressed leftover: $(basename "$f")"
            partial+=("$f.gz")
            gzip -f "$f" || fail "could not compress leftover $(basename "$f")"
            gzip -t "$f.gz" || fail "leftover $(basename "$f").gz failed verification"
            partial=()
        else
            echo "[backup] discarding corrupt leftover: $(basename "$f")"
            rm -f "$f"
        fi
    done
}

# ── 4. never start a backup that cannot fit ──────────────────────────────────
preflight() {
    local db bytes=0 need_kb free_kb
    for db in "${DBS[@]}"; do
        [ -f "$db" ] && bytes=$(( bytes + $(wc -c < "$db") ))
    done
    need_kb=$(( bytes * 22 / 10 / 1024 + MIN_FREE_MB * 1024 ))
    free_kb=$(df -Pk "$BACKUP_DIR" | awk 'NR==2 { print $4 }')
    if [ "$free_kb" -lt "$need_kb" ]; then
        fail "insufficient disk space — ${free_kb} KB free, need ${need_kb} KB"
    fi
    echo "[backup] preflight ok — ${free_kb} KB free, ${need_kb} KB required"
}

# ── 5. back up, verifying every step ─────────────────────────────────────────
backup_one() {
    local db="$1" name out
    name=$(basename "$db" .db)
    if [ ! -f "$db" ]; then
        echo "[backup] skip: $db not found"
        return 0
    fi

    out="$BACKUP_DIR/${name}-${DATE}.db"
    rm -f "$out" "$out.gz" "$out-journal" "$out-wal" "$out-shm"
    partial+=("$out" "$out.gz" "$out-journal" "$out-wal" "$out-shm")

    sqlite3 "$db" ".backup '$out'" || fail "sqlite3 .backup failed for $db"
    [ -s "$out" ] || fail "backup of $db is empty"
    is_sqlite_db "$out" || fail "backup of $db is not a valid SQLite database"

    gzip -f "$out" || fail "gzip failed for $(basename "$out")"
    gzip -t "$out.gz" || fail "gzip verification failed for $(basename "$out").gz"

    if [ "$DOW" = "7" ]; then
        cp "$out.gz" "$BACKUP_DIR/weekly-${name}-${DATE}.db.gz" \
            || fail "could not write weekly snapshot for $name"
    fi

    partial=()
    echo "[backup] ok: $db -> $(basename "$out").gz ($(du -h "$out.gz" | cut -f1))"
}

# ── 6. retention: dailies, weeklies — and any stray .db a crash left behind ───
prune() {
    find "$BACKUP_DIR" -maxdepth 1 -name '*.db.gz' ! -name 'weekly-*' \
        -mtime +"$RETENTION_DAYS" -delete
    find "$BACKUP_DIR" -maxdepth 1 -name 'weekly-*.db.gz' \
        -mtime +"$WEEKLY_RETENTION_DAYS" -delete
}

mkdir -p "$BACKUP_DIR"
discard_interrupted
discard_junk
recover_leftovers
preflight
for db in "${DBS[@]}"; do backup_one "$db"; done
prune

if [ -n "$REMOTE" ]; then
    rsync -az --delete-after "$BACKUP_DIR/" "$REMOTE" || fail "offsite sync to $REMOTE failed"
    echo "[backup] synced to $REMOTE"
fi

trap - ERR
snapshots=$(find "$BACKUP_DIR" -maxdepth 1 -name '*.db.gz' | wc -l | tr -d ' ')
echo "[backup] OK — ${snapshots} snapshots retained, $(du -sh "$BACKUP_DIR" | cut -f1) total"
