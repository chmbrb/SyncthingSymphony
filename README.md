# Syncthing Synchronizer

Always-on Syncthing master ("Copy A") with hourly restic backups.

## Setup
1. Create the directories yourself before the first `up`, owned by the account Syncthing will run as
   (otherwise Docker creates them as root and Syncthing fails with `syncthing.lock: permission denied`):
   `mkdir -p data config backups && sudo chown -R "$(id -u):$(id -g)" data config && chmod 700 config`
   (`backups/` may stay root-owned.) Set `PUID`/`PGID` in `.env` to match `id -u` / `id -g`.
2. `cp .env.example .env` (set `GUI_BIND` if needed) and create the restic secret (see "Restic password" below). `GUI_BIND=0.0.0.0`
   exposes the GUI to the whole LAN: set the GUI password immediately, or bind to a specific LAN IP.
3. `docker compose up -d`
4. Open http://127.0.0.1:8384, set a GUI username/password immediately.
5. In the GUI: Actions > Settings > Connections: disable Global Discovery and Relaying (LAN only).
6. Edit the default folder: path `/var/syncthing/data`, type **Send & Receive**,
   File Versioning > **Staggered** (max age 365 days).
7. Add employee devices by device ID; accept each pairing manually.

## Fedora / SELinux hosts
The base `docker-compose.yml` works as-is on Windows and on Debian/Ubuntu (AppArmor). On
SELinux hosts, add the overlay, which relabels the bind mounts with `:z`:

    docker compose -f docker-compose.yml -f docker-compose.selinux.yml up -d

(Optional: `echo COMPOSE_FILE=docker-compose.yml:docker-compose.selinux.yml >> .env` so plain
`docker compose` commands pick it up.) Do not use this on Windows; `:z` is not needed there.
Because `:z` relabels the host directories as shared container content, don't point it at
system paths such as your home directory.

## Firewall (Fedora / firewalld)
Other devices need the sync port (and, optionally, discovery) open on the machine running the container:

    # allow; omit --permanent to apply to the running session only
    sudo firewall-cmd --permanent --add-port=22000/tcp --add-port=22000/udp --add-port=21027/udp && sudo firewall-cmd --reload

    # remove
    sudo firewall-cmd --permanent --remove-port=22000/tcp --remove-port=22000/udp --remove-port=21027/udp && sudo firewall-cmd --reload

    # check
    sudo firewall-cmd --list-ports

On Debian/Ubuntu with ufw: `sudo ufw allow 22000 && sudo ufw allow 21027/udp`.

## Running Syncthing on the host too (e.g. development)
A host instance acts like any other employee device, with two cautions.

**1. Port conflicts.** The container publishes these host ports, so a host install clashes on them:

| Port | Used by container for | Host instance default |
|---|---|---|
| 8384/tcp | GUI | 8384 (conflict) |
| 22000/tcp+udp | sync | 22000 (conflict) |
| 21027/udp | local discovery | 21027 (conflict) |

Pick one approach:
- **Change the host instance (recommended).** In the host GUI under Settings > Connections, set the
  sync listen address to `tcp://0.0.0.0:22001, quic://0.0.0.0:22001`; under Settings > GUI, set the
  listen address to `127.0.0.1:8385`. Disable Local Discovery on the host instance, or accept bind
  errors on 21027.
- **Change the container's published ports (alternative, handy for development).** Edit the left side
  of the mappings in `docker-compose.yml` (e.g. `"22001:22000/tcp"`, `"22001:22000/udp"`,
  `"21028:21027/udp"`, GUI `8385:8384`) and leave the host instance on its defaults. Other devices
  must then connect to the new sync port (`tcp://<host-ip>:22001`) and the firewall rules must match.
  Don't commit this change for production, where peers expect 22000.

**2. Don't point both at `./data`.** Two Syncthing instances writing to the same directory fight
over it and create conflicts. Give the host instance its own folder, e.g. `~/Sync/keepass`, and sync
it with the container like any other peer. Editing straight in `./data` edits Copy A directly and
skips the pairing, so avoid that too.

**Pairing them:**
1. On the host instance, add the container as a remote device with the manual address
   `tcp://127.0.0.1:22000` (use the remapped port if you changed it).
2. Accept the prompt in the container GUI, then share the specific folder (e.g. `data/B`, see
   Segregated folders) with the host device.
3. On the host, accept the folder and pick a local path such as `~/Sync/keepass`.

The container sees the host connect in, so no address is needed in the other direction.

**Fedora/SELinux:** a host install from the package manager (`sudo dnf install syncthing`) runs as
your user, so it needs none of the `:z` relabelling. A GUI listening only on `127.0.0.1` needs no
firewall change.

**Run the host instance in the background:** `systemctl --user enable --now syncthing.service`
(and `sudo loginctl enable-linger $USER` to start at boot without logging in). Stop any copy running
in a terminal first.

## Networking and GUI access
Docker already puts the containers on a bridge network, so no extra bridge or adapter is needed
for LAN access. What matters is which host ports are published:

- `22000` (sync) and `21027/udp` (discovery) are published on all host interfaces. Employee
  devices connect to `<host-ip>:22000`, regardless of `GUI_BIND`. Discovery broadcasts may not
  cross the Docker bridge, so if a device doesn't find Copy A, add it by address
  (`tcp://<host-ip>:22000`).
- `GUI_BIND` only controls the web GUI (port 8384). `127.0.0.1` (recommended) keeps it local to
  the host; `0.0.0.0` or a LAN IP exposes the login page to the network.

To use a local-only GUI from another machine, tunnel over SSH instead of exposing it:

    ssh -L 8384:127.0.0.1:8384 user@<host-ip>

then browse to http://127.0.0.1:8384. After changing `GUI_BIND` in `.env`, run `docker compose up -d`
again; `data/` and `config/` are untouched.

## Segregated folders (per-employee access)
Syncthing can't track a folder inside an already-tracked folder, so don't share `data/` itself.
Track sibling subdirectories instead and share each only with the devices that need it:

| Syncthing folder | Container path | Shared with |
|---|---|---|
| Directory B | `/var/syncthing/data/B` | Employee B only |
| Directory C | `/var/syncthing/data/C` | Employee C only |
| Shared (optional) | `/var/syncthing/data/shared` | B and C |

Restic backs up the whole of `data/`, so every subfolder is covered with no extra setup.

Setup / migration on Copy A:
1. In the GUI, remove the existing `data` folder (Edit > Remove). This does not delete files.
2. Delete `data/.stfolder` (the old root marker).
3. `mkdir -p data/B data/C && sudo chown PUID:PGID data/B data/C` (use the values from `.env`);
   move any existing files into the right subfolder.
4. Add one folder per subdirectory: path as in the table, type Send & Receive,
   Staggered versioning, shared only with the right device.
5. On each employee device, remove the old `data` share and accept the new folder.

Notes:
- A device only receives folders explicitly shared with it, so B never sees C's files or names.
- Never enable **Introducer** on any device; it would expose device lists to other employees.
- Devices sharing the same folder can see each other's device IDs, so keep unrelated folders separate.
- This is a sharing policy, not a boundary against someone with GUI or `config/` access to Copy A.
- Use Send Only (Copy A) / Receive Only (employee) for folders employees should never alter.

## Storage layout
| Directory | Holds | Backed up by restic |
|---|---|---|
| `data/` | the synced files, plus each folder's `.stversions` | yes (read-only mount) |
| `config/` | device key/cert, `config.xml` (folders, devices, GUI password hash) | yes, excluding the rebuildable index DB (`index*`) and the lock file |
| `backups/` | the restic repository | no (it is the backup) |

- Put `backups/` on a different physical disk than `data/` and `config/`; if they share a disk, one
  failure loses the data and its backups together. Plain bind mounts on the separate drive are enough;
  a Docker named volume adds no redundancy (it lives on the Docker data root unless bind-backed).
- Losing `config/` means a new device ID, and every peer must be re-paired. It is backed up for that reason.
- The restic repo is encrypted, so including the device key is safe, but keep the restic passphrase
  stored separately from the host (see "Restic password").
- Off-host copy (SFTP/S3/B2 or `restic copy`) is not set up yet; it protects against theft, fire and
  ransomware on the host.
- Restore `config/`: stop the stack, `restic restore latest --target /tmp/r --include /config`,
  copy the files back into `./config`, `chown -R PUID:PGID config`, `chmod 700 config`, start the stack.
- Test a restore occasionally; an untested backup is a guess.
- After upgrading from an earlier version, run `docker compose up -d` to recreate the restic container.
  `--group-by host` in the prune step keeps old `/data`-only snapshots under the same retention policy.

## Restic password (Docker secret)
The restic passphrase is read from a file, not an environment variable, so it does not appear in
`docker inspect`, `/proc/<pid>/environ`, `.env` or shell history. It is mounted into the backup
container at `/run/secrets/restic_password` (`RESTIC_PASSWORD_FILE`).

Create it once, readable only by root (the backup container runs as root):

    sudo install -d -m 700 secrets
    # a generated passphrase, written without a trailing echo to the terminal:
    openssl rand -base64 32 | sudo tee secrets/restic_password >/dev/null
    sudo chmod 600 secrets/restic_password

Migrating from an older setup with `RESTIC_PASSWORD` in `.env`: keep the same value so the existing
repo still opens. Write it with `printf '%s' 'the-password' | sudo tee secrets/restic_password >/dev/null`
(no quotes or trailing text in the file), then delete the line from `.env` and run `docker compose up -d`.

Recovery copy: store the passphrase off the host, e.g. `pass insert syncthing/restic`, and keep it
with whoever restores. Without it the backups cannot be decrypted. Recreate the file on a new host with
`pass show syncthing/restic | sudo tee secrets/restic_password >/dev/null`.

Limits: root on the host can still read the file, and so can anyone who can start containers with
the directory mounted, so this reduces exposure but is not a boundary against a compromised host.
It matters most once copies of `backups/` leave the host (off-host copy, stolen disk).
`secrets/` is gitignored.

On SELinux hosts (Fedora) the file may need a container label or the backup container gets
"permission denied": `sudo chcon -t container_file_t secrets/restic_password`.

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
