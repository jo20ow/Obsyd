#!/usr/bin/env bash
# OBSYD liveness heartbeat — a dead-man ping to healthchecks.io every 10 minutes.
#
# Install (obsyd user's crontab):
#
#   */10 * * * * /home/obsyd/obsyd/deploy/heartbeat.sh
#
# Design: probe the PUBLIC site first, and ping healthchecks ONLY when the probe
# returns HTTP 2xx. That ping-only-on-200 rule is what makes one check catch BOTH
# failure modes:
#   * VPS death / cron death / network partition → this script never runs → no
#     ping → healthchecks fires after its grace period.
#   * Service death with the VPS alive (uvicorn crashed, Caddy/TLS/DNS broken,
#     Docker ate the bridge again) → the probe fails (`curl -f`) → the script
#     exits non-zero WITHOUT pinging → healthchecks fires just the same.
# A design that pinged unconditionally would only ever catch the first mode.
#
# The probe goes through https://obsyd.dev (public URL, not localhost:8000) on
# purpose: it exercises DNS + certificate + Caddy + uvicorn — the whole path a
# real visitor takes. /api/v1/meta is the cheapest real endpoint: it reads only
# the tiny series-catalog dimension table (no power_hourly scan, no
# heavy_query_guard), and 144 hits/day is noise against its 120 req/min limit.
#
# HEALTHCHECKS_LIVE_URL comes from the environment, else from the prod .env.
# If it is set nowhere, the script silently exits 0 — the cron line is safe to
# install BEFORE the healthchecks.io account/check exists. No secrets are echoed.

set -euo pipefail

APP_DIR="${OBSYD_APP_DIR:-/home/obsyd/obsyd}"
ENV_FILE="${OBSYD_ENV_FILE:-$APP_DIR/.env}"
PROBE_URL="${OBSYD_HEARTBEAT_PROBE_URL:-https://obsyd.dev/api/v1/meta}"

if [ -z "${HEALTHCHECKS_LIVE_URL:-}" ] && [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ]; then
    HEALTHCHECKS_LIVE_URL=$(grep -E '^HEALTHCHECKS_LIVE_URL=' "$ENV_FILE" | tail -n 1 \
        | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true)
fi
[ -n "${HEALTHCHECKS_LIVE_URL:-}" ] || exit 0  # no check configured yet — do nothing

# Probe first: any non-2xx / timeout / TLS error makes curl -f fail, set -e ends
# the script here with a non-zero exit, and the ping below never happens.
curl -fsS -m 10 -o /dev/null "$PROBE_URL"

curl -fsS -m 10 --retry 3 -o /dev/null "$HEALTHCHECKS_LIVE_URL"
