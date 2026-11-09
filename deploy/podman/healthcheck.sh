#!/bin/sh
# Readiness probe for the prototype instance. Logs a failure and exits nonzero;
# it never restarts containers itself. Container restarts are the restart
# policy's job; an unhealthy-but-running app needs an operator decision.
#
# On a failure it also records the /ready response body (which names the
# scheduler state) and /health, so a later reader can tell a failed scheduler
# from a database outage without a shell on the container.
#
# Optional alert hook: set TALKABOUT_ALERT_COMMAND to a command line and it runs
# once per ready<->not_ready TRANSITION (not on every 5-minute failure), with
# TALKABOUT_ALERT_STATE (ready|not_ready) and TALKABOUT_ALERT_MESSAGE in its
# environment. Unset means no alert and no extra behaviour. A failing alert
# command never changes this script's exit code.
set -eu
URL=${TALKABOUT_READY_URL:-http://127.0.0.1:18081/ready}
HEALTH_URL=${TALKABOUT_HEALTH_URL:-${URL%/ready}/health}
STATE_FILE=${TALKABOUT_HEALTH_STATE_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/talkabout/health.state}

# -w appends the status code after the body on its own line; no -f so a 503 body is kept.
response=$(curl -sS --connect-timeout 3 --max-time 10 -w '\n%{http_code}' "$URL" 2>/dev/null || true)
code=$(printf '%s' "$response" | tail -n 1)
body=$(printf '%s' "$response" | sed '$d' | head -c 300)

if [ "$code" = "200" ]; then
  state=ready
else
  state=not_ready
fi

previous=unknown
[ -r "$STATE_FILE" ] && previous=$(head -c 32 "$STATE_FILE" 2>/dev/null | tr -cd 'a-z_') || true

if [ "$state" = "ready" ]; then
  echo "talkabout ready"
else
  health=$(curl -sS --connect-timeout 3 --max-time 5 "$HEALTH_URL" 2>/dev/null | head -c 300 || true)
  echo "talkabout not ready: HTTP ${code:-none} at $URL"
  echo "  ready_body=${body:-none} health_body=${health:-none}"
fi

if [ "$state" != "$previous" ]; then
  mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null || true
  printf '%s\n' "$state" > "$STATE_FILE" 2>/dev/null || true
  # Do not alert on the very first observation being healthy.
  if [ -n "${TALKABOUT_ALERT_COMMAND:-}" ] && { [ "$state" = "not_ready" ] || [ "$previous" != "unknown" ]; }; then
    TALKABOUT_ALERT_STATE=$state \
    TALKABOUT_ALERT_MESSAGE="talkabout $state (HTTP ${code:-none}); previous=$previous; $URL" \
      sh -c "$TALKABOUT_ALERT_COMMAND" >/dev/null 2>&1 || echo "talkabout alert command failed"
  fi
fi

[ "$state" = "ready" ]
