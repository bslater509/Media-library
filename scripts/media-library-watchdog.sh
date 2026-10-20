#!/usr/bin/env bash
# Restart the app when its HTTP loop stops answering.
#
# Why this exists: the process can be alive, listening on its port and holding
# every worker, yet answer nothing — one synchronous stretch on the event loop
# (a mount read, a lock wait) stops the whole server while systemd's
# `Restart=always` sees a healthy MainPID, so it never fires. A hung process is
# only detectable from outside, by asking it for a page.
#
# Two consecutive failures trigger the restart, so a slow shutdown/startup (a
# restart takes ~15 s while the shutdown snapshot is written) is not mistaken
# for a hang and restarted on top of itself.
#
# Installed as a systemd timer; see media-library-watchdog.service.example and
# media-library-watchdog.timer.example. Override with the environment variables
# below (or an Environment= line in the unit).
set -uo pipefail

UNIT="${ML_UNIT:-media-library}"
URL="${ML_HEALTH_URL:-http://127.0.0.1:8888/api/health}"
TIMEOUT="${ML_HEALTH_TIMEOUT:-10}"
STATE_DIR="${ML_WATCHDOG_STATE_DIR:-/run/media-library-watchdog}"
FAILS_BEFORE_RESTART="${ML_WATCHDOG_FAILS:-2}"
LOG_TAG="${ML_WATCHDOG_TAG:-media-library-watchdog}"

log() { logger -t "$LOG_TAG" -- "$*" 2>/dev/null || echo "$LOG_TAG: $*"; }

mkdir -p "$STATE_DIR" 2>/dev/null || true
STATE="$STATE_DIR/failures"
failures=0
if [ -r "$STATE" ]; then
    read -r failures <"$STATE" || failures=0
fi
case "$failures" in
    '' | *[!0-9]*) failures=0 ;;
esac

code="$(curl -fsS -m "$TIMEOUT" -o /dev/null -w '%{http_code}' "$URL" 2>/dev/null || true)"

if [ "$code" = "200" ]; then
    if [ "$failures" -ne 0 ]; then
        echo 0 >"$STATE" 2>/dev/null || true
    fi
    exit 0
fi

failures=$((failures + 1))
echo "$failures" >"$STATE" 2>/dev/null || true

if [ "$failures" -lt "$FAILS_BEFORE_RESTART" ]; then
    log "health check failed (${code:-no response}, attempt $failures); waiting for the next run"
    exit 0
fi

log "health check failed $failures times (${code:-no response}); restarting $UNIT"
echo 0 >"$STATE" 2>/dev/null || true
if ! systemctl restart "$UNIT"; then
    log "systemctl restart $UNIT failed"
    exit 1
fi
log "$UNIT restarted"
