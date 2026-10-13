# SyncthingSynchronizer

Always-on Syncthing master ("Copy A") with hourly restic backups.

## Setup
1. `cp .env.example .env` and set `RESTIC_PASSWORD` (and `GUI_BIND` if needed).
2. `docker compose up -d`
3. Open http://127.0.0.1:8384, set a GUI username/password immediately.
4. In the GUI: Actions > Settings > Connections: disable Global Discovery and Relaying (LAN only).
5. Edit the default folder: path `/var/syncthing/data`, type **Send & Receive**,
   File Versioning > **Staggered** (max age 365 days).
6. Add employee devices by device ID; accept each pairing manually.

## Fedora / SELinux hosts
The base `docker-compose.yml` works as-is on Windows and on Debian/Ubuntu (AppArmor). On
SELinux hosts, add the overlay, which relabels the bind mounts with `:z`:

    docker compose -f docker-compose.yml -f docker-compose.selinux.yml up -d

(Optional: `echo COMPOSE_FILE=docker-compose.yml:docker-compose.selinux.yml >> .env` so plain
`docker compose` commands pick it up.) Do not use this on Windows; `:z` is not needed there.
Because `:z` relabels the host directories as shared container content, don't point it at
system paths such as your home directory.

## Recovery
- Recent overwrite/delete: look in `data/.stversions/`.
- Anything else: `docker compose exec restic restic snapshots`, then
  `docker compose exec restic restic restore <id> --target /tmp/restore --include /data/<file>`
  (copy out with `docker compose cp`), and place the file back in `data/`.
  If restoring over a corrupted file, pause peers first so the bad version doesn't win.

## Threat model
An authorized device (Eve) can still write bad data; it will propagate. Versioning and
snapshots make it recoverable. The restic repo (`backups/`) is only written by the backup
container; put it on a separate disk and restrict host access.

## KeePass
Concurrent edits produce `*.sync-conflict-*` files; merge with KeePass "Synchronize with file".
