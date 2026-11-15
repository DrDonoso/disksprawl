# Deployment

This guide installs DiskSprawl with Docker Compose and covers what is specific to analyzing a NAS:
mounting network shares, running in a Proxmox LXC container, permissions, reverse proxies,
updates, backups and troubleshooting. The [README](../README.md#deploy-with-docker) has the short
version.

## Contents

- [Requirements](#requirements)
- [Installing](#installing)
- [What happens at startup](#what-happens-at-startup)
- [Permissions](#permissions)
- [Network shares](#network-shares)
- [Proxmox LXC and a NAS](#proxmox-lxc-and-a-nas)
- [Reverse proxy and HTTPS](#reverse-proxy-and-https)
- [Releases and updating](#releases-and-updating)
- [Backup and restore](#backup-and-restore)
- [Troubleshooting](#troubleshooting)

## Requirements

- A Linux machine, amd64 or arm64, with Docker Engine and the Compose plugin. A small machine is
  enough: the application itself needs little memory, and a scan needs more only for very large
  trees (gdu and multi-threaded ncdu keep the whole tree in memory until they finish).
- The folders to analyze, reachable from that machine: local disks, or network shares mounted on
  the host.
- A few hundred megabytes for the database, depending on how many folders the directories have
  and how many scans keep them.

## Installing

```bash
mkdir disksprawl && cd disksprawl
curl -fsSLO https://raw.githubusercontent.com/DrDonoso/disksprawl/main/docker-compose.yml
curl -fsSL -o .env https://raw.githubusercontent.com/DrDonoso/disksprawl/main/.env.example
```

Edit the `volumes` of `docker-compose.yml` to mount each folder to analyze **read-only** under
`/data`, and adjust `.env` ([configuration](configuration.md)): at least `TZ`, and
`AUTH_PASSWORD` if untrusted devices can reach the port. Then:

```bash
docker compose up -d
docker compose ps     # wait for "healthy"
```

Open `http://<host>:7780`, press **⚙** and **Add directory**. Each directory can be a mounted
folder or any folder inside it: `/data/storage`, but also `/data/storage/media` and
`/data/storage/backups` with different schedules and methods.

The Compose file runs the container hardened: a read-only root file system, `/tmp` in memory, no
Linux capabilities, `no-new-privileges`, and the user 1000. Only the `data` volume is writable.
Keep those settings.

## What happens at startup

1. The database in the `data` volume is created or migrated to the current version.
2. Scans that were running when the container stopped are recorded as **interrupted**; queued
   scans stay in the queue.
3. Each engine is detected with its version; a missing one appears disabled in the settings.
4. The schedules are loaded, the capacity of every volume is measured, and a scheduled scan
   missed by less than an hour (during an update, for example) is queued.

`docker compose logs -f app` shows each step and every scan.

## Permissions

The container runs as **user 1000** (group 1000) and only reads. To measure a folder it must be
able to **list every folder** below it: directories need read and execute permission (`r-x`) for
that user, its group or everyone. Files do not need to be readable: their size comes from their
metadata. Anything the user cannot list is left out of the totals and reported as an error of the
scan ("completed with N errors"), with the paths that failed in the scan's details.

Check it from the host, as user 1000:

```bash
sudo -u '#1000' find /mnt/storage -type d ! -readable -print -o -type d ! -executable -print | head
```

No output means every folder can be listed. Otherwise, choose one of these:

- **Mount options** of a network share, on the host: `uid=` and `gid=` make the files appear owned
  by that user, and `dir_mode=0555` lets everyone list the folders (see
  [network shares](#network-shares)).
- **A group that can read the files**: if the folders belong to a group, such as `media` with id
  1001 and `r-x` for the group, add it to the container in `docker-compose.yml`:

  ```yaml
  services:
    app:
      group_add:
        - "1001"
  ```

- **Another user**: `user: "1001:1001"` runs the container as that user. The `data` volume was
  created for user 1000, so give it to the new user first:
  `docker run --rm -v disksprawl_data:/data alpine chown -R 1001:1001 /data`.

Never run it as root: DiskSprawl does not need it, and the hardening of the container assumes it.

## Network shares

Mount network shares **on the host** (or on the Proxmox host, see below) and pass them to the
container with a read-only bind mount. The container cannot mount shares itself, by design: it
has no capabilities.

**NFS is recommended.** Scanning means listing folders and reading the metadata of every file,
and NFS does that much faster than SMB, which needs several network round trips per folder. A
read-only mount in `/etc/fstab`:

```text
nas.local:/export/storage  /mnt/storage  nfs  ro,_netdev,noatime  0  0
```

With NFS the permissions are the NAS's own: the user 1000 of the container must be able to list
the folders, as a user the NAS knows, through the group or through permissions for everyone. Many
NAS systems can also map every client request to one user (*squash*), which then needs read
access.

**SMB (CIFS)** works too; prefer few threads and the *Balanced* or *Gentle* pace. Keep the
credentials in a file only root can read, and make the files appear as the container's user:

```text
//nas.local/storage  /mnt/storage  cifs  ro,_netdev,credentials=/root/.smb-storage,uid=1000,gid=1000,dir_mode=0555,file_mode=0444,iocharset=utf8  0  0
```

```text
# /root/.smb-storage (chmod 600)
username=disksprawl
password=…
```

A read-only account on the NAS is the safest choice for both: DiskSprawl never writes, and the
account makes sure of it.

**Capacity over the network**: the volume card shows what the mount reports through `statvfs`,
like `df`. On a share it is what the NAS reports for it: usually the size of its volume, but it may
be a quota of the share or of the user. Directories on the same mount are recognized as the same
volume.

## Proxmox LXC and a NAS

A common setup is a small unprivileged LXC container on a Proxmox host, running Docker, next to a
NAS shared over the network. The share is mounted once on the Proxmox host and passed into the LXC
container, and from there into DiskSprawl, read-only all the way.

1. **Mount the share on the Proxmox host** as in [network shares](#network-shares), for example on
   `/mnt/storage`. For SMB, an unprivileged container sees host user 101000 as its user 1000 (the
   container's ids are shifted by 100000), so use `uid=101000,gid=101000` instead of
   `uid=1000,gid=1000`. Check that the share is mounted after a reboot before the container starts.

2. **Create an unprivileged LXC container** with Debian and the `nesting` and `keyctl` features,
   which Docker needs, and install Docker Engine and the Compose plugin in it:

   ```bash
   pct set <CTID> --features nesting=1,keyctl=1
   ```

3. **Pass the share into the container**, read-only:

   ```bash
   pct set <CTID> -mp0 /mnt/storage,mp=/mnt/storage,ro=1
   ```

   Restart the container; `/mnt/storage` then shows the share inside it. More shares take `mp1`,
   `mp2`…

4. **Install DiskSprawl** in the container as in [installing](#installing), with
   `- /mnt/storage:/data/storage:ro` in `docker-compose.yml`, and add `/data/storage` in **⚙**.

Why not mount the share inside the LXC container? An unprivileged container cannot mount NFS or
SMB shares without relaxing its isolation, and the Docker container even less so. Mounting on the
host keeps both containers unprivileged and the credentials on the host. It also lets other
containers use the same mount.

**User ids**: inside the unprivileged container, user 1000 is user 101000 on the Proxmox host.
That is the user the share must allow, as described in [permissions](#permissions): with SMB
through `uid=101000`, with NFS through permissions for everyone, a group, or the NAS's squash
settings. Running `sudo -u '#101000' ls /mnt/storage` on the Proxmox host checks it.

## Reverse proxy and HTTPS

DiskSprawl speaks plain HTTP on port 7780. To open it with a domain name and HTTPS, put a reverse
proxy in front and set in `.env`:

```dotenv
APP_BIND_ADDRESS=127.0.0.1      # only the proxy, on this host, can reach the port
ALLOWED_HOSTS=disks.example.com # the proxy's host name
AUTH_COOKIE_SECURE=true         # the session cookie over HTTPS only
TRUSTED_PROXY_IPS=172.18.0.1    # where the proxy's connections come from, as the container sees it
```

The proxy must pass the original `Host` header (DiskSprawl checks it, and checks that changes come
from the same origin) and set `X-Forwarded-For` and `X-Forwarded-Proto`. Connections published on
the host reach the container from the gateway of its Compose network; this command shows it:

```bash
docker network inspect disksprawl_default --format '{{(index .IPAM.Config 0).Gateway}}'
```

A proxy in another container of the same network connects from that container's address instead.
Never trust `*` or a network that untrusted clients can use: they could then choose the address
the sign-in throttling sees.

**Caddy**, which obtains the certificate itself and sends the headers by default:

```text
disks.example.com {
    reverse_proxy 127.0.0.1:7780
}
```

**nginx**:

```nginx
server {
    listen 443 ssl;
    server_name disks.example.com;
    # ssl_certificate and ssl_certificate_key …

    location / {
        proxy_pass http://127.0.0.1:7780;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Requests are small: bodies over 64 KiB are refused, so the proxy needs no special limits.

## Releases and updating

Each release is published to Docker Hub as `drdonoso/disksprawl:latest` and as a CalVer tag
(`20261115`, or `20261115.02` for a second release that day), for amd64 and arm64, with a GitHub
release listing its changes. To update:

```bash
docker compose pull
docker compose up -d
```

With `IMAGE_TAG=latest` this takes the newest release. To choose when to update, set `IMAGE_TAG`
to a release and change it when you want to. The database is migrated on start; scans queued or
scheduled during the update run afterwards.

**Rolling back**: set `IMAGE_TAG` to the previous release and run `docker compose up -d`. If the
newer release migrated the database, restore the backup taken before updating.

## Backup and restore

Everything DiskSprawl stores is in the `data` volume (`disksprawl_data`): the SQLite database with
the directories, settings and scan history, and the generated session key. The analyzed folders
are never written and are not part of it.

### Backup

Stop the app for a consistent copy, archive the volume and start it again:

```bash
docker compose stop
docker run --rm -v disksprawl_data:/data:ro -v "$PWD":/backup alpine \
    tar -czf /backup/disksprawl-data.tar.gz -C /data .
docker compose start
```

### Restore

```bash
docker compose down
docker volume rm disksprawl_data
docker compose create    # creates an empty data volume, without starting the app
docker run --rm -v disksprawl_data:/data -v "$PWD":/backup alpine \
    sh -c 'tar -xzf /backup/disksprawl-data.tar.gz -C /data && chown -R 1000:1000 /data'
docker compose start
```

Use the image release of the backup, or a newer one.

## Troubleshooting

`docker compose logs --tail=100 app` shows the startup and every scan; `docker compose ps` shows
whether the container is healthy.

| Symptom | Cause and fix |
| --- | --- |
| The container restarts and the log says a setting is invalid | A value in `.env` is wrong, such as an `AUTH_PASSWORD` shorter than 12 characters or an unknown `TZ`; the message names it. |
| "DiskSprawl does not answer to this host name" | The browser uses a host name that is not allowed: add it to `ALLOWED_HOSTS`. |
| Signing in does nothing, or asks again at once | `AUTH_COOKIE_SECURE=true` over plain `http://`: set it to `false`, or use HTTPS. |
| "The folder must be inside /data" | Directories are folders inside the container: mount the folder under `/data` and use that path. |
| "This folder does not exist (it may not be mounted)" | The bind mount is missing, or the share is not mounted on the host (or on the Proxmox host). Check with `docker compose exec app ls /data`. |
| "This folder cannot be read: check its permissions" | User 1000 cannot list it: see [permissions](#permissions). |
| A scan "completed with N errors" | Some folders could not be listed: the scan's details list them. Usually permissions, or files removed while scanning. |
| An engine is disabled in the settings | It was not found or did not answer `--version`: the log says why. The official image includes all four. |
| The volume size differs from the NAS | Over SMB or NFS the NAS reports the share's capacity, which may be a quota. |
| The NAS is slow during scans | Use fewer threads and the *Balanced* or *Gentle* pace, or schedule the scan at night. |
| A scan takes much longer than before | Compare the engine and options in the history; a pace or thread change, or a busy NAS, explain most cases. |
