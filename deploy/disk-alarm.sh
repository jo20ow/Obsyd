#!/usr/bin/env bash
# OBSYD disk alarm — every 15 min via the obsyd user's crontab.
#
#   */15 * * * * /home/obsyd/obsyd/deploy/disk-alarm.sh >> /home/obsyd/obsyd/logs/disk-alarm.log 2>&1
#
# On 2026-07-07 the root filesystem hit 100 %. dockerd died, Caddy went with it,
# and obsyd.dev + valuekick.de were offline for two days before anyone noticed.
# journald could no longer persist, so the in-app collector watchdog never even
# logged, let alone mailed.
#
# Hence the constraints this script is built around:
#   * it depends on nothing but `df`, `curl` and the shell — not on the app,
#     the database, journald, or an MTA;
#   * its state lives on tmpfs (/dev/shm), so it writes nothing to the very
#     filesystem it is reporting as full;
#   * a breach exits non-zero even when no mail channel is configured.
#
# Thresholds: the valuekick cron tasks (prod-task.sh) transiently add ~5 GB for
# ~3 minutes every 30 minutes. WARN therefore sits above that spike, so a normal
# spike is silent but a genuinely eroded headroom is not.

set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/notify.sh
. "$SCRIPT_DIR/notify.sh"

DISK_PATH="${OBSYD_DISK_PATH:-/}"
WARN_PCT="${OBSYD_DISK_WARN_PCT:-85}"
CRIT_PCT="${OBSYD_DISK_CRIT_PCT:-93}"
STATE_DIR="${OBSYD_ALARM_STATE_DIR:-/dev/shm/obsyd}"
COOLDOWN_SEC="${OBSYD_ALARM_COOLDOWN_SEC:-21600}"  # 6 h

pct=$(df -Pk "$DISK_PATH" | awk 'NR==2 { gsub(/%/, "", $5); print $5 }')
free_h=$(df -Ph "$DISK_PATH" | awk 'NR==2 { print $4 }')

if [ "$pct" -ge "$CRIT_PCT" ]; then
    level=CRIT
elif [ "$pct" -ge "$WARN_PCT" ]; then
    level=WARN
else
    echo "[disk-alarm] OK — ${pct}% used on ${DISK_PATH}, ${free_h} free"
    exit 0
fi

echo "[disk-alarm] ${level} — ${pct}% used on ${DISK_PATH}, only ${free_h} free" >&2

# Cooldown is tracked per level, so a disk crossing from WARN into CRIT escalates
# immediately instead of being swallowed by the WARN cooldown.
mkdir -p "$STATE_DIR"
state_file="${STATE_DIR}/disk-alarm.${level}"
now=$(date +%s)
last=0
[ -f "$state_file" ] && last=$(cat "$state_file" 2>/dev/null || echo 0)

if [ $(( now - last )) -ge "$COOLDOWN_SEC" ]; then
    body="Disk ${level} on $(hostname 2>/dev/null || echo unknown-host): ${pct}% of ${DISK_PATH} used, ${free_h} free.

$(df -Ph "$DISK_PATH" 2>/dev/null || echo '  (df unavailable)')

At 100 % dockerd dies and Caddy stops serving obsyd.dev and valuekick.de.

Quick wins, in order:
  docker builder prune -af          # build cache, regenerates on next deploy
  npm cache clean --force           # /root/.npm
  journalctl --vacuum-size=100M
Never run 'docker volume prune' — that is the valuekick postgres.
Keep >=5 GB free: the valuekick cron tasks need it transiently every 30 min."

    # Only start the cooldown once the alert is actually out the door.
    if obsyd_alert "OBSYD ${level}: disk ${pct}% full" "$body"; then
        printf '%s' "$now" > "$state_file"
    fi
else
    echo "[disk-alarm] alert suppressed — last ${level} sent $(( (now - last) / 60 )) min ago" >&2
fi

exit 1
