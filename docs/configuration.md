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
| `AUTH_SECRET` | generated | Key that signs the session cookies. |
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
everyone out.

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

The image sets the working folder to `/app`, so the database and the generated session key live
in `/app/data` (the `data` volume), the analyzed folders in `/data`, and the built web interface in
`/app/frontend/dist`. `IMAGE_TAG` and `BUILD_DATE` are set at build time and reported by
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
| `DATA_DIR` | `data` | Folder of the database and the session key. |
| `SCAN_ROOT` | `/data` | The folder every directory must be inside. |
| `FRONTEND_DIR` | `frontend/dist` | The built web interface served at `/`; absent when Vite serves it. |

The browser tests use `E2E_SAMPLE_ROOT` and `E2E_PYTHON`, set by the Playwright configuration; see
[development](development.md).
