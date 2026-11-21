# Configuration

DiskSprawl has two kinds of settings: a few **environment variables** for the container, set in
the `.env` file next to `docker-compose.yml`, and everything about **what to scan and how**, set in
the web interface.

## Contents

- [How settings are read](#how-settings-are-read)
- [Quick reference](#quick-reference)
- [Image and network](#image-and-network)
- [Time zone](#time-zone)
- [Password and sessions](#password-and-sessions)
- [Host names and reverse proxies](#host-names-and-reverse-proxies)
- [Folders to analyze](#folders-to-analyze)
- [Settings in the interface](#settings-in-the-interface)
- [Scan logs](#scan-logs)
- [Notifications](#notifications)
- [Language and theme](#language-and-theme)
- [Set by the image](#set-by-the-image)
- [Applying changes](#applying-changes)
- [Development and test variables](#development-and-test-variables)

## How settings are read

Docker Compose reads `.env` and passes the variables listed under `environment:` in
`docker-compose.yml` to the container; [`.env.example`](../.env.example) documents each one. All of
them are optional. The application checks them when it starts and refuses to start, with a clear
message in `docker compose logs`, when one is invalid: a password that is too short, an unknown
time zone or a malformed host name.

## Quick reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `IMAGE_TAG` | `latest` | Image tag to run. |
| `APP_BIND_ADDRESS` | `0.0.0.0` | Host address the web interface is published on. |
| `APP_PORT` | `7780` | Host port. |
| `TZ` | `Etc/UTC` | Time zone of the schedules and of the dates shown as server time. |
| `AUTH_PASSWORD` | empty | Single password for the web interface; empty leaves it open. |
| `AUTH_SECRET` | generated | Key that signs the session cookies and the notifications. |
| `AUTH_COOKIE_SECURE` | `false` | Session cookie over HTTPS only, and HSTS. |
| `SESSION_HOURS` | `168` | How long a sign-in lasts, in hours. |
| `ALLOWED_HOSTS` | empty | Extra host names the app answers to. |
| `TRUSTED_PROXY_IPS` | `127.0.0.1` | Reverse proxies whose forwarded headers are trusted. |

## Image and network

**`IMAGE_TAG`**: `latest` follows every release. A CalVer tag such as `20261115` (or `20261115.02`
for a second release that day) pins a release, so you update only when you change it. The
[releases](https://github.com/DrDonoso/disksprawl/releases) list them with their notes.

**`APP_BIND_ADDRESS`** and **`APP_PORT`**: Compose publishes the container's port 7780 on
`APP_BIND_ADDRESS:APP_PORT` of the host. The default, `0.0.0.0` (every interface), lets the other
devices of your network open DiskSprawl. Use `127.0.0.1` when a reverse proxy on the same host is
the only way in. The container itself always listens on 7780.

## Time zone

**`TZ`** is an IANA time zone such as `Europe/Madrid` or `America/New_York`. Schedules run on
this clock ("daily at 03:00" means 03:00 in this zone, following daylight saving time), and the
interface shows the next runs in it. Dates in the interface are otherwise shown in the browser's
time zone.

## Password and sessions

**`AUTH_PASSWORD`**: when set, every page and API request needs a sign-in with this password,
except the health check (`/health`) and the version (`/api/version`). It must have at least 12
characters. When empty, anyone who can reach the port can use DiskSprawl. It never writes to your
folders, but it shows their names and sizes, and lets anyone add directories and start scans.
Set a password whenever the port is reachable by people or devices you do not trust.

**`AUTH_SECRET`**: the key that signs the session cookies, at least 32 characters; generate one
with `openssl rand -hex 32`. When empty, DiskSprawl creates a random key once and keeps it in the
data volume (`session.key`), which is enough for a single installation. Changing the key signs
everyone out. The key that signs the [notifications](#notifications) is derived from it too:
after a change, each device with notifications on subscribes again the next time DiskSprawl
opens there.

**`AUTH_COOKIE_SECURE`**: set it to `true` when DiskSprawl is opened over HTTPS, usually behind a
reverse proxy. The session cookie is then sent over HTTPS only and browsers are told to always use
HTTPS (HSTS). Keep it `false` for plain `http://`, or signing in will not work.

**`SESSION_HOURS`**: how long a sign-in lasts, from 1 to 8760 hours (one week by default). Signing
out ends the session at once.

## Host names and reverse proxies

**`ALLOWED_HOSTS`**: DiskSprawl answers only to host names that public DNS cannot point at another
address, which blocks [DNS rebinding](security-model.md#host-names-and-dns-rebinding) attacks. IP
addresses, `localhost`, single names (`nas`, `server`) and local names (`.local`, `.lan`, `.home`,
`.home.arpa`, `.internal`, `.intranet`, `.private`, `.corp`, `.localdomain`) always work. Any other
name, typically the domain of a reverse proxy, must be listed here, comma-separated, without
scheme or port:

```dotenv
ALLOWED_HOSTS=disks.example.com
# Also every subdomain of example.com:
ALLOWED_HOSTS=.example.com
```

`*` allows every name and turns the protection off. A request for any other name gets
`400 Bad Request` with a message that names this variable.

**`TRUSTED_PROXY_IPS`**: reverse proxies whose `X-Forwarded-For` and `X-Forwarded-Proto` headers
are trusted, as comma-separated IP addresses or networks as the container sees them. The client
address matters for the sign-in throttling. A proxy on the host connects from the Compose
network's gateway, such as `172.18.0.1`; a proxy in the same Compose project from its container's
address. Never use `*` or a network that untrusted clients can use. See
[reverse proxy and HTTPS](deployment.md#reverse-proxy-and-https).

## Folders to analyze

The folders are not variables: they are mounted read-only under `/data` in `docker-compose.yml`,
one line each, and then added as directories in the interface:

```yaml
    volumes:
      - data:/app/data
      - /mnt/storage:/data/storage:ro
      - /srv/photos:/data/photos:ro
```

Every directory must be inside `/data`, after resolving symbolic links, so a directory cannot reach
anything else in the container. The container runs as user 1000, which must be able to list them:
see [permissions](deployment.md#permissions).

## Settings in the interface

**⚙ Settings → Directories** lists the directories in the order of the menu, adds new ones with a
folder picker that browses `/data`, and edits each one:

| Option | Default | Notes |
| --- | --- | --- |
| Name | the folder's name | Filled in from the folder picked; up to 60 characters. |
| Scheduled scans active | on | Off pauses the schedule; **Scan now** keeps working. |
| Schedule | Daily at 03:00 | Manual, every hour, every 6 hours, daily at 03:00, weekly (Monday at 03:00) or any cron expression. |
| Engine | Native | Native, du, ncdu or gdu; see [scan engines](engines.md). |
| Threads | 2 | 1–16 for Native and ncdu, 1–8 for gdu, always 1 for du. |
| Pace | Balanced | Maximum, Balanced or Gentle. |
| Low priority | on | `nice` and `ionice`. |
| Measure | Apparent size | Or space on disk. |
| Stay on one file system | on | Like `du -x`. |
| Exclusions | `#snapshot`, `.snapshot`, `.zfs` | Up to 50 names or patterns with `*` and `?`. |

Cron expressions have the usual five fields: minute, hour, day of the month, month and day of the
week (0 or 7 is Sunday). When both day fields are restricted, a run needs both to match. The form
checks the expression as you type and shows its next three runs.

**⚙ Settings → General** holds the settings of the whole installation:

| Setting | Default | Range | Notes |
| --- | --- | --- | --- |
| Capacity measurement | 15 minutes | 1–1440 | How often each volume's used and free space is measured (`statvfs`, without walking any folder). Scans measure it too. |
| Detailed scans kept per directory | 30 | 1–1000 | Older scans keep only their totals, for the history. |
| Smallest folder stored | 1 MiB | 1–1024 | Smaller folders count in the totals and appear as "loose files and small folders". |
| Warning threshold | 80 % | 1–99 | From this use on, a volume's bar turns amber. |
| Critical threshold | 90 % | 2–100 | From this use on, it turns red. Must be above the warning threshold. |
| Days scan logs are kept | 30 | 1–3650 | Each scan's [log file](#scan-logs) is deleted this many days after the scan ends. |

**⚙ Settings → Notifications** turns on the notifications of the browser or phone it is opened on,
and chooses which ones it gets: see below.

## Scan logs

Each scan that starts writes a text file in the `data` volume, named after its directory and the
moment it started in the server's time zone (`TZ`):

```text
/app/data/logs/<directory name>/<YYYY-MM-DD_HHMM>.log
```

For example `logs/Media/2026-11-20_0300.log`. A second scan that starts within the same minute
gets `_2`, `_3`… Characters that do not belong in a file name (`/ \ : * ? " < > |` and control
characters) become `_`, and leading or trailing dots and spaces go. Renaming a directory leaves its
earlier logs where they are, and they still download from its history; two directories with the
same name share a folder, and each log says which one it belongs to. A scan cancelled while it
waited in the queue never started, so it has no log.

A log starts with the directory, its folder, what started the scan (its schedule or **Scan now**),
the engine with its version, and the options. Then come the events, each with its time in the
server's time zone: queued, started, and while it reads the folders:

- **Every 10 seconds**, what it has read so far, its share done (estimated from the previous scan,
  when there is one), what it read in those 10 seconds and the folder it is reading. Lines where
  the counters barely move while the folder stays the same show where the scan lost its time. du
  counts no files and tells no folder; ncdu with several threads counts the files and folders
  together; gdu tells nothing until it ends.
- **Every folder or file it could not read**, up to 100,000. The interface keeps a sample of 20.
- **Slow folders** (Native engine): each folder that takes 5 seconds or more to read, without its
  subfolders (listing it and reading the details of its entries), up to 1,000.

After the reading come the phases (adding up the results, saving them) and the end. A completed
scan then tells where its time went: reading the folders, with the time it rested at its pace,
adding up the results and saving them. With the Native engine it also lists the 20 folders that
took longest to read, without their subfolders, and the reading time of the 15 first-level folders
that took longest, each with its subfolders, and below each one up to 5 of its own subfolders, those
that took at least 1 % of the reading. With several threads these times are added up over the
threads, so they can add up to more than the scan took. When older scans lose their folders (beyond
**Detailed scans kept per directory**), a last line says how long removing them took. A shortened
example:

```text
DiskSprawl 20261121: scan 123
Directory: Media (id 3)
Folder: /data/media
Started by: its schedule
Engine: native (Python 3.14)
Options: 1 thread, gentle pace, low priority, apparent size, one file system
Excluded: #snapshot, .snapshot, .zfs
Smallest folder stored: 1 MiB

2026-11-21 06:00:00+01:00  Queued
2026-11-21 06:00:00+01:00  Started
2026-11-21 06:00:10+01:00  Progress: 1,318 folders, 20,447 files, 38.9 GiB read, about 2 % done; in the last 10 s: +1,318 folders, +20,447 files, +38.9 GiB; reading /data/media/Photos/2014/2014-03
[…]
2026-11-21 06:41:20+01:00  Progress: 30,102 folders, 498,775 files, 1.62 TiB read, about 44 % done; in the last 10 s: +0 folders, +3,118 files, +412 MiB; reading /data/media/Backups/old-laptop/AppData/Local/Cache
2026-11-21 06:42:31+01:00  Slow folder: /data/media/Backups/old-laptop/AppData/Local/Cache took 1 min 52 s for 48,113 entries
[…]
2026-11-21 07:13:05+01:00  Error: /data/media/Photos/private: Permission denied
[…]
2026-11-21 07:48:04+01:00  Adding up the results
2026-11-21 07:48:07+01:00  Saving the results: 24,503 folders
2026-11-21 07:48:31+01:00  Completed in 1 h 48 min 31 s
  Size: 3.87 TiB (4,255,103,217,664 bytes)
  Files: 1,204,377
  Folders: 68,411
  Folders stored: 24,503
  Errors: 1 (listed above)
  Volume: 78.3 % used (4.12 TiB used, 1.14 TiB free, 5.26 TiB in total)
  Time:
    Reading the folders: 1 h 48 min 04 s
      Resting (gentle pace): 54 min 01 s, 50 % of it
    Adding up the results: 3.1 s
    Saving the results: 24.4 s
  Slowest folders to read, without their subfolders:
    1 min 52 s  48,113 entries  /data/media/Backups/old-laptop/AppData/Local/Cache
    41.0 s      12,077 entries  /data/media/Photos/2019/Imports
    18.2 s       9,880 entries  /data/media/Backups/old-laptop/Mail
  Reading time by folder, with its subfolders:
    29 min 08 s    53.9 %   8,120 folders  /data/media/Backups
      22 min 29 s  41.6 %   1,032 folders  /data/media/Backups/old-laptop
    17 min 17 s    32.0 %  38,977 folders  /data/media/Photos
      9 min 01 s   16.7 %   4,410 folders  /data/media/Photos/2019
    6 min 11 s     11.4 %  21,314 folders  /data/media/Music
    1 min 26 s      2.7 %                  /data/media, without its subfolders
2026-11-21 07:48:34+01:00  Removed the folders of 1 older scan in 2.8 s, keeping those of the latest 30 scans
```

A scan that fails ends with why (`Failed after 1 s: the folder does not exist (it may not be
mounted)`, followed by the error code and the last lines of the engine when it crashed); one that
is cancelled, or interrupted by a restart of DiskSprawl, says so.

**Downloading**: the **Log** column of the latest scans, in each directory's history, downloads the
log of each scan that started, even while it runs; so does the button below the errors of the
last scan. **From the host**, the logs are files of the volume:

```bash
docker compose exec app ls -R /app/data/logs
docker compose cp app:/app/data/logs ./disksprawl-logs
```

**Expiry**: a log is deleted **Days scan logs are kept** days (**⚙ Settings → General**, 30 by
default) after its last line, normally the end of its scan. DiskSprawl checks when it starts and
then every hour, and at once when the setting goes down. Deleting a directory deletes its logs.

## Notifications

There is nothing to set on the server: each browser or phone turns them on in **⚙ Settings →
Notifications**, which asks the browser for permission, and chooses which ones it gets. They
arrive in its language, even with DiskSprawl closed, and tapping one opens its directory.

| Notification | Default | What it tells |
| --- | --- | --- |
| A scan started with **Scan now** finishes | on | The directory's size, how much it changed since the previous scan, how long it took and its errors. |
| A scheduled scan finishes | off | The same, for the scans started by their schedule, which usually run at night. |
| A scan fails or is interrupted | on | Why, such as a folder that is not mounted, or a restart of DiskSprawl. Cancelled scans tell nobody. |
| A volume is nearly full | on | The directories of a volume whose use reaches the warning or the critical threshold of **⚙ Settings → General**, with its use and free space. |

A volume is told once per level, and again only after its use has dropped 2 points below the
threshold, so a volume hovering around one does not notify again and again. A device that turns
these on is told the volumes already above a threshold. The choices belong to each device: the
same person can get everything on a computer and only the failures on a phone.

They need:

1. **HTTPS.** Browsers only offer notifications to pages opened over HTTPS, or at `localhost` on
   the same computer; at `http://<address>:7780` the tab explains it. Put DiskSprawl behind a
   [reverse proxy with HTTPS](deployment.md#reverse-proxy-and-https).
2. **On an iPhone or iPad** (iOS or iPadOS 16.4 or later), DiskSprawl added to the Home Screen
   (Safari's **Share → Add to Home Screen**) and opened from there. Android, and Chrome, Edge,
   Firefox and Safari on computers, need nothing else.
3. **Outbound HTTPS** (port 443) from the container to the browsers' push services:
   `fcm.googleapis.com` (Chrome, and Edge on Android), `*.push.apple.com` (Safari, iPhone and
   iPad), `*.push.services.mozilla.com` (Firefox) and `*.notify.windows.com` (Edge on Windows).
   DiskSprawl sends to no other address and follows no redirect.

With a password, signing out on a device pauses its notifications until it signs in there again.
A device its push service no longer knows (its browser data was cleared, or the app was removed)
is forgotten, and so is one whose push service refuses three notifications in a row; DiskSprawl
keeps 50 devices at most. A notification that cannot be sent is tried again for a day, unless a
newer one about the same directory or volume replaces it first. Failures are logged with the push
service and never with the device's address, for example
`Push message not sent to fcm.googleapis.com (URLError)` when the container cannot reach it.

## Language and theme

Two menus in the top bar, next to **⚙**, also on the sign-in page:

| Menu | Options | Default |
| --- | --- | --- |
| Language (globe) | English, Español | English, whatever the browser's language |
| Theme (sun, moon or screen) | Light, Dark, Automatic (system) | Automatic: light or dark as the operating system or the browser prefers, following it when it changes |

Each browser keeps its own choice (in its local storage, not on the server), and other open tabs
follow a change. The theme is applied before the page is drawn, so it never flashes in the other
one; native controls such as scroll bars and lists, the browser's own bar on phones, the treemap
and the charts follow it.

## Set by the image

The image sets the working folder to `/app`, so the database, the scans' logs and the generated
session key live in `/app/data` (the `data` volume), the analyzed folders in `/data`, and the built
web interface in `/app/frontend/dist`. `IMAGE_TAG` and `BUILD_DATE` are set at build time and reported by
`/api/version`. The container listens on port 7780 with one worker, because the scan queue and the
scheduler live in the application process.

## Applying changes

After changing `.env` or `docker-compose.yml`, run `docker compose up -d`: Compose recreates the
container with the new values (`docker compose restart` keeps the old ones). The settings made in
the interface apply at once.

## Development and test variables

When running from the source code, three more variables replace the image's layout:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATA_DIR` | `data` | Folder of the database, the scans' logs and the session key. |
| `SCAN_ROOT` | `/data` | The folder every directory must be inside. |
| `FRONTEND_DIR` | `frontend/dist` | The built web interface served at `/`; absent when Vite serves it. |

The browser tests use `E2E_SAMPLE_ROOT` and `E2E_PYTHON`, set by the Playwright configuration; see
[development](development.md).
