#!/bin/sh
# TalkAbout monthly/daily database backup for the prototype instance.
# Runs on the deployment host next to rootless Podman; never stores a password.
set -eu

CONTAINER=${TALKABOUT_DB_CONTAINER:-talkabout-auto-db}
DB_USER=${TALKABOUT_DB_USER:-talkabout_owner}
DB_NAME=${TALKABOUT_DB_NAME:-talkabout}
BACKUP_DIR=${TALKABOUT_BACKUP_DIR:-"$HOME/backups/talkabout"}
KEEP=${TALKABOUT_BACKUP_KEEP:-14}

umask 077
mkdir -p "$BACKUP_DIR"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$BACKUP_DIR/talkabout-$stamp.dump"

podman exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$target"
size=$(wc -c < "$target")
if [ "$size" -lt 1024 ]; then
  echo "backup failed: dump is $size bytes" >&2
  rm -f "$target"
  exit 1
fi
sha256sum "$target" > "$target.sha256"

# Keep the newest N dumps (and their checksums); drop older ones.
ls -1t "$BACKUP_DIR"/talkabout-*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old" "$old.sha256"
done

echo "backup ok: $target ($size bytes)"
