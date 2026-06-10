#!/usr/bin/env bash
# OBSYD SQLite backup script
#
# Uses `sqlite3 .backup` (safe with WAL mode / running app, unlike plain cp).
# Keeps RETENTION_DAYS daily snapshots locally; optionally syncs offsite.
#
# Install (as obsyd user):
#   crontab -e
#   30 3 * * * /home/obsyd/obsyd/deploy/backup-db.sh >> /home/obsyd/backups/backup.log 2>&1
#
# Offsite sync (recommended — a VPS disk is a single point of failure):
#   export OBSYD_BACKUP_REMOTE="user@host:/path/to/backups/"  (in crontab line or ~/.profile)

set -euo pipefail

APP_DIR="${OBSYD_APP_DIR:-/home/obsyd/obsyd}"
BACKUP_DIR="${OBSYD_BACKUP_DIR:-/home/obsyd/backups}"
RETENTION_DAYS="${OBSYD_BACKUP_RETENTION_DAYS:-14}"
REMOTE="${OBSYD_BACKUP_REMOTE:-}"

STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR"

backup_one() {
    local db_path="$1"
    local name
    name=$(basename "$db_path" .db)
    if [ ! -f "$db_path" ]; then
        echo "[$STAMP] skip: $db_path not found"
        return 0
    fi
    local out="$BACKUP_DIR/${name}-${STAMP}.db"
    sqlite3 "$db_path" ".backup '$out'"
    gzip "$out"
    echo "[$STAMP] backed up $db_path -> ${out}.gz ($(du -h "${out}.gz" | cut -f1))"
}

backup_one "$APP_DIR/obsyd.db"
backup_one "$APP_DIR/data/portwatch.db"

# Rotate: delete local snapshots older than RETENTION_DAYS
find "$BACKUP_DIR" -name '*.db.gz' -mtime +"$RETENTION_DAYS" -delete

# Optional offsite sync
if [ -n "$REMOTE" ]; then
    rsync -az --delete-after "$BACKUP_DIR/" "$REMOTE"
    echo "[$STAMP] synced to $REMOTE"
fi

echo "[$STAMP] backup complete ($(find "$BACKUP_DIR" -name '*.db.gz' | wc -l | tr -d ' ') snapshots retained)"
