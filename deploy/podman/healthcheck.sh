#!/bin/sh
# Readiness probe for the prototype instance. Logs a failure and exits nonzero;
# it never restarts containers itself. Container restarts are the restart
# policy's job; an unhealthy-but-running app needs an operator decision.
set -eu
URL=${TALKABOUT_READY_URL:-http://127.0.0.1:18081/ready}
code=$(curl -fsS -o /dev/null -w '%{http_code}' "$URL" 2>/dev/null || true)
if [ "$code" != "200" ]; then
  echo "talkabout not ready: HTTP ${code:-none} at $URL"
  exit 1
fi
echo "talkabout ready"
