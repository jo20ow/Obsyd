#!/usr/bin/env bash
# Docker housekeeping — weekly via root's crontab.
#
#   0 5 * * 0 /home/obsyd/obsyd/deploy/docker-prune.sh >> /home/obsyd/obsyd/logs/docker-prune.log 2>&1
#
# Nothing ever pruned docker on this host. By 2026-07-07 the build cache alone
# held 5.1 GB — one `--build` deploy's worth of layers per deploy, kept forever —
# and helped fill the root filesystem, which killed dockerd, which took Caddy and
# therefore obsyd.dev and valuekick.de offline for two days.
#
# What this deliberately does NOT do, and must never do:
#
#   docker volume prune   -> valuekick's postgres lives in a volume.
#   docker system prune   -> same, plus it takes everything else with it.
#   docker image prune -a -> would remove node:20-bookworm-slim. valuekick's
#                            prod-task.sh runs it with --rm every 30 minutes, so
#                            docker sees no container using it, so -a reaps it —
#                            and every cron run would then re-pull 200 MB.
#
# Dangling images and the build cache are regenerable by definition. The first
# deploy after a prune rebuilds without cache; that is the whole price.

set -Eeuo pipefail

# No `dirname`: this script must survive a PATH that has nothing on it, so it can
# report "docker is not installed" rather than dying on a missing coreutil.
SCRIPT_DIR=$(cd "${BASH_SOURCE[0]%/*}" && pwd)
# shellcheck source=deploy/notify.sh
. "$SCRIPT_DIR/notify.sh"

fail() {
    local msg="$1"
    echo "[docker-prune] FAIL: $msg" >&2
    obsyd_alert "OBSYD ALERT: docker prune FAILED" \
        "Weekly docker housekeeping on $(hostname 2>/dev/null || echo unknown-host) failed.

Reason: ${msg}

Disk:
$(df -Ph / 2>/dev/null || echo '  (df unavailable)')" || true
    exit 1
}

if ! command -v docker >/dev/null 2>&1; then
    echo "[docker-prune] docker not installed — nothing to do"
    exit 0
fi

# A dead daemon is how the outage started. Say so and stay out of the way.
if ! docker info >/dev/null 2>&1; then
    echo "[docker-prune] docker daemon not reachable — skipping"
    exit 0
fi

free_before=$(df -Pk / | awk 'NR==2 { print $4 }')

docker builder prune -af >/dev/null || fail "builder prune failed"
docker image prune -f    >/dev/null || fail "image prune failed"   # dangling only

free_after=$(df -Pk / | awk 'NR==2 { print $4 }')
reclaimed_mb=$(( (free_after - free_before) / 1024 ))
[ "$reclaimed_mb" -lt 0 ] && reclaimed_mb=0

echo "[docker-prune] OK — reclaimed ${reclaimed_mb} MB, $(df -Ph / | awk 'NR==2 { print $4 }') free"
