#!/bin/sh
# Hourly restic snapshots of /data (read-only) into /repo.
set -u
restic cat config >/dev/null 2>&1 || restic init
last_check=0
while true; do
  restic backup /data /config --exclude '/config/index*' --exclude '/config/syncthing.lock' \
    --host syncthing-master --tag hourly || echo "backup failed" >&2
  restic forget --prune --host syncthing-master --group-by host \
    --keep-hourly 24 --keep-daily 14 --keep-weekly 8 --keep-monthly 12 || echo "forget failed" >&2
  now=$(date +%s)
  if [ $((now - last_check)) -ge 604800 ]; then
    restic check || echo "restic check FAILED" >&2
    last_check=$now
  fi
  sleep "${BACKUP_INTERVAL:-3600}"
done
