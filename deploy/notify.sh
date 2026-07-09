#!/usr/bin/env bash
# Shared ops-alert channel for the deploy scripts. Source it, don't run it.
#
# The VPS has no MTA, so cron's "mail on non-zero exit" goes nowhere. Resend is
# already configured for the app (magic links, daily brief), so ops alerts ride
# the same channel — a plain curl POST, no Python, no database, no temp files.
# That matters: these alerts have to work on a filesystem that is 100 % full.

OBSYD_ALERT_FROM="${OBSYD_ALERT_FROM:-OBSYD <briefing@obsyd.dev>}"
OBSYD_ALERT_EMAIL="${OBSYD_ALERT_EMAIL:-obsyd.dev@pm.me}"

# cron does not source the app's .env. Point OBSYD_ENV_FILE at it and we lift
# just the one key we need — never a blanket `source`, which would execute
# whatever ends up in that file. Only ever consulted when explicitly configured.
if [ -z "${RESEND_API_KEY:-}" ] && [ -n "${OBSYD_ENV_FILE:-}" ] && [ -r "${OBSYD_ENV_FILE}" ]; then
    RESEND_API_KEY=$(
        sed -n 's/^[[:space:]]*RESEND_API_KEY[[:space:]]*=[[:space:]]*//p' "${OBSYD_ENV_FILE}" \
            | head -1 | tr -d '"'\''' | tr -d '\r'
    )
    export RESEND_API_KEY
fi

# Escape a string for embedding in a JSON string literal, collapsing newlines
# to \n so the payload stays a single line.
_obsyd_json_escape() {
    printf '%s' "$1" \
        | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g' \
        | awk 'BEGIN { ORS = "" } { if (NR > 1) printf "\\n"; print }'
}

# obsyd_alert <subject> <body>
# Returns non-zero when the alert could not be delivered, so callers can decide
# whether to record a cooldown. Never fatal on its own.
obsyd_alert() {
    local subject="$1" body="$2" key payload
    key="${RESEND_API_KEY:-}"

    if [ -z "$key" ]; then
        echo "[notify] RESEND_API_KEY unset — not mailed: $subject" >&2
        return 1
    fi

    payload=$(printf '{"from":"%s","to":["%s"],"subject":"%s","text":"%s"}' \
        "$(_obsyd_json_escape "$OBSYD_ALERT_FROM")" \
        "$(_obsyd_json_escape "$OBSYD_ALERT_EMAIL")" \
        "$(_obsyd_json_escape "$subject")" \
        "$(_obsyd_json_escape "$body")")

    if curl -fsS -X POST "https://api.resend.com/emails" \
        -H "Authorization: Bearer ${key}" \
        -H "Content-Type: application/json" \
        -d "$payload" >/dev/null 2>&1; then
        echo "[notify] alert sent: $subject" >&2
        return 0
    fi

    echo "[notify] alert DELIVERY FAILED: $subject" >&2
    return 1
}
