#!/bin/sh
# Create a custom-format backup after checking its table of contents and hash.
# These checks do not prove that every archive data block can be restored.
# Requires flock, mktemp, sha256sum and a running PostgreSQL container.
set -eu
CONTAINER=${TALKABOUT_DB_CONTAINER:-talkabout-auto-db}
DB_USER=${TALKABOUT_DB_USER:-talkabout_owner}
DB_NAME=${TALKABOUT_DB_NAME:-talkabout}
BACKUP_DIR=${TALKABOUT_BACKUP_DIR:-"$HOME/backups/talkabout"}
KEEP=${TALKABOUT_BACKUP_KEEP:-14}
# Require canonical positive decimal notation; leading zeros are ambiguous in
# shell arithmetic (00 is zero, while 08/09 can be invalid octal).
case "$KEEP" in ''|0*|*[!0-9]*) echo 'backup failed: invalid retention count' >&2; exit 1;; esac
[ "$KEEP" -le 10000 ] || { echo 'backup failed: invalid retention count' >&2; exit 1; }
umask 077
mkdir -p "$BACKUP_DIR"
cd "$BACKUP_DIR"
# flock is tied to the open descriptor: no stale lock directory after a crash.
exec 9>.talkabout-backup.lock
if ! flock -n 9; then echo 'backup failed: another backup is running' >&2; exit 1; fi
work=$(mktemp -d .talkabout-backup.XXXXXXXX)
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="talkabout-$stamp.dump"
# Never overwrite a published backup, including one made within the same second.
if [ -e "$target" ] || [ -e "$target.sha256" ]; then echo 'backup failed: target already exists' >&2; exit 1; fi
partial="$work/payload"
if ! podman exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$partial"; then
  echo 'backup failed: pg_dump failed' >&2; exit 1
fi
size=$(wc -c < "$partial")
if [ "$size" -lt 1024 ]; then echo "backup failed: dump is $size bytes" >&2; exit 1; fi
# Check the archive table of contents before publishing it; this does not restore data.
if ! podman exec -i "$CONTAINER" pg_restore -l < "$partial" > "$work/toc"; then
  echo 'backup failed: archive is unreadable' >&2; exit 1
fi
[ -s "$work/toc" ] || { echo 'backup failed: empty archive table of contents' >&2; exit 1; }
hash=$(sha256sum "$partial")
hash=${hash%% *}
# Verify the staged bytes before exposing a final dump name.
printf '%s  %s\n' "$hash" payload > "$work/staged.sha256"
if ! (cd "$work" && sha256sum -c staged.sha256 >/dev/null); then
  echo 'backup failed: staged checksum mismatch' >&2; exit 1
fi
printf '%s  %s\n' "$hash" "$target" > "$work/checksum"
# Hard links give no-clobber publication on the same filesystem. Publish the
# checksum first; the final dump name appears only after both artifacts exist.
if ! ln "$work/checksum" "$target.sha256"; then echo 'backup failed: checksum target exists' >&2; exit 1; fi
if ! ln "$partial" "$target"; then
  rm -f -- "$target.sha256"
  echo 'backup failed: dump target exists' >&2; exit 1
fi
if ! sha256sum -c "$target.sha256" >/dev/null; then
  echo 'backup failed: published checksum mismatch; files retained for investigation' >&2; exit 1
fi
# Retention runs only after successful publication. Protect the just-published
# target even when a previously copied file has a future mtime. Sorting and
# deletion failures are not hidden by a pipeline's last-command exit status.
if ! ls -1t talkabout-*.dump > "$work/list"; then
  echo 'backup published but retention listing failed' >&2; exit 1
fi
retained=1
while IFS= read -r old; do
  [ "$old" = "$target" ] && continue
  if [ "$retained" -lt "$KEEP" ]; then
    retained=$((retained + 1))
  else
    rm -f -- "$old" "$old.sha256" || { echo 'backup published but retention failed' >&2; exit 1; }
  fi
done < "$work/list"
echo "backup ok: $BACKUP_DIR/$target ($size bytes)"
