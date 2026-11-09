#!/bin/sh
# Sends the health-transition alert (TALKABOUT_ALERT_STATE / TALKABOUT_ALERT_MESSAGE) to
# Telegram and/or Slack. Use it as the alert command:
#   TALKABOUT_ALERT_COMMAND=sh %h/talkabout/deploy/podman/alert-notify.sh
# Channels turn on only when their secrets exist in ~/.config/talkabout/alert.env:
#   TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID      (Telegram Bot API sendMessage)
#   SLACK_WEBHOOK_URL                          (Slack incoming webhook)
# A channel that is not configured is skipped. Exit 0 if at least one configured channel
# accepted the message, or if none is configured; exit 1 only when every configured
# channel failed. Secrets are passed to curl on stdin, never on its command line.
set -u
state=${TALKABOUT_ALERT_STATE:-unknown}
# Keep the text to one plain line: it goes into a JSON string and a form field.
message=$(printf '%s' "${TALKABOUT_ALERT_MESSAGE:-talkabout $state}" | tr -cd 'A-Za-z0-9 .,:;=()/_@%+-' | head -c 400)
case $state in not_ready) icon=ALERT ;; ready) icon=RECOVERED ;; *) icon=INFO ;; esac
text="[$icon] $message"
configured=0
delivered=0

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  configured=$((configured + 1))
  api=${TALKABOUT_TELEGRAM_API:-https://api.telegram.org}
  if printf 'url = "%s/bot%s/sendMessage"\n' "$api" "$TELEGRAM_BOT_TOKEN" |
     curl -sS -f --connect-timeout 5 --max-time 15 -K - \
       --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" --data-urlencode "text=$text" -o /dev/null 2>/dev/null; then
    delivered=$((delivered + 1))
  else
    echo "talkabout alert: telegram delivery failed" >&2
  fi
fi

if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
  configured=$((configured + 1))
  if printf 'url = "%s"\n' "$SLACK_WEBHOOK_URL" |
     curl -sS -f --connect-timeout 5 --max-time 15 -K - \
       -H 'Content-Type: application/json' --data "{\"text\":\"$text\"}" -o /dev/null 2>/dev/null; then
    delivered=$((delivered + 1))
  else
    echo "talkabout alert: slack delivery failed" >&2
  fi
fi

[ "$configured" -eq 0 ] || [ "$delivered" -gt 0 ]
