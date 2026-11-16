# Security model

DiskSprawl reads the folders you give it and shows their names and sizes. This page describes
what it can reach, how access to it is protected, and how to deploy it safely. To report a
vulnerability, see [SECURITY.md](../SECURITY.md).

## Contents

- [Read-only by design](#read-only-by-design)
- [Access and the optional password](#access-and-the-optional-password)
- [Without a password](#without-a-password)
- [Cross-site requests](#cross-site-requests)
- [Host names and DNS rebinding](#host-names-and-dns-rebinding)
- [Headers and Content Security Policy](#headers-and-content-security-policy)
- [Reverse proxies](#reverse-proxies)
- [Data handling](#data-handling)
- [The public demo](#the-public-demo)
- [Dependencies and the supply chain](#dependencies-and-the-supply-chain)
- [Deployment checklist](#deployment-checklist)

## Read-only by design

DiskSprawl never needs to write to, open or change the folders it measures: it lists them and
reads their metadata. The deployment makes that a guarantee rather than a promise:

- The folders are mounted **read-only** (`:ro`), and a read-only account on the NAS is
  recommended for network shares.
- The container runs as the unprivileged **user 1000**, on a **read-only root file system**, with
  **no Linux capabilities** (`cap_drop: ALL`) and `no-new-privileges`. It can write only to its
  `data` volume and to `/tmp`, which lives in memory.
- Every directory must be **inside `/data`** after resolving symbolic links, so neither a path nor a
  link inside a mounted folder can point a scan elsewhere in the container. The folder picker lists
  folders only.
- Engines are started with a list of arguments, **never through a shell**. Names come from the
  file system, and exclusion patterns are validated (names, `*` and `?` only) before they reach an
  engine. Symbolic links are never followed while scanning.

DiskSprawl never reads the contents of files.

## Access and the optional password

Without `AUTH_PASSWORD`, anyone who can reach the port can use DiskSprawl. With it, every page and
API request requires a sign-in, except the health check (`/health`), the version
(`/api/version`) and the sign-in endpoints themselves.

- The password must have at least 12 characters. It is compared in constant time with respect to
  its contents, and never written to the database or the logs.
- **Sign-in throttling**: after 5 failed attempts from one address in 15 minutes, that address
  must wait; after 50 failed attempts in total, every sign-in must wait. The answer is
  `429 Too Many Requests` with a `Retry-After` header.
- A successful sign-in creates a **session**: a random 256-bit token in a cookie signed with
  HMAC-SHA-256. The database stores only a SHA-256 digest of the token, so a copy of the database
  cannot be used to sign in, and signing out revokes the session at once.
- The cookie is `HttpOnly` and `SameSite=Strict`, and with `AUTH_COOKIE_SECURE=true` also
  `Secure` (HTTPS only, with HSTS). It expires after `SESSION_HOURS` (one week by default).
- The signing key is `AUTH_SECRET`, or a random key created once in the data volume, readable by
  the container's user only. Changing it ends every session.

## Without a password

An open installation lets anyone who can reach it see the names and sizes of the folders, add
directories inside `/data` and start scans, which load the storage. It cannot read or change any
file. Leave it open only on a network where everyone is trusted, and set a password otherwise, or
when DiskSprawl is published through a reverse proxy.

## Cross-site requests

Every request that changes something (`POST`, `PUT`, `PATCH` and `DELETE`) must carry the header
`X-DiskSprawl-Request: 1` and an `Origin` header with the same scheme, host and port as the
request. A web page on another site cannot send the custom header without the browser asking
first, and its origin would not match, so it cannot add directories, start scans or sign out a
user. With a password, the `SameSite=Strict` cookie adds another layer.

## Host names and DNS rebinding

A malicious web page can make its own domain name resolve to an address on your network (DNS
rebinding) and then talk to a service there as if it were the same site, bypassing the origin
check. DiskSprawl therefore answers only to host names that cannot be used for that: IP addresses,
`localhost`, single-label names such as `nas`, and local suffixes such as `.local`, `.lan`,
`.home.arpa` or `.internal`. Any other name must be listed in `ALLOWED_HOSTS`; a request for an
unknown name gets `400 Bad Request`. `ALLOWED_HOSTS=*` disables the protection: use it only if
something in front of DiskSprawl checks the host name.

## Headers and Content Security Policy

Every response carries:

```text
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
  img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none';
  base-uri 'none'; form-action 'self'; object-src 'none'
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Permissions-Policy: camera=(), microphone=(), geolocation=()
```

and `Strict-Transport-Security` when `AUTH_COOKIE_SECURE=true`. The web interface loads nothing
from other sites: no fonts, scripts or analytics. It has no inline scripts either: the script that
applies the theme before the page is drawn is a file of the bundle, like the rest. Request bodies
over 64 KiB are refused.

## Reverse proxies

Forwarded headers (`X-Forwarded-For`, `X-Forwarded-Proto`) are trusted only from the addresses in
`TRUSTED_PROXY_IPS` (`127.0.0.1` by default). The client address decides the sign-in throttling,
so trusting a network that untrusted clients can use would let them evade it. See
[reverse proxy and HTTPS](deployment.md#reverse-proxy-and-https).

## Data handling

- **Stored** in the `data` volume (SQLite): the directories and their options, the folders of each
  kept scan with their sizes and counts, the totals of older scans, the paths that could not be
  read, capacity measurements, the settings, the digests of active sessions and, when
  `AUTH_SECRET` is not set, the session signing key. Files and directories are created readable by
  the container's user only.
- **Not stored**: file contents, the names of files (except those that could not be read) and the
  password.
- **Logs** identify directories and scans by number. Database errors never include the values of
  their parameters, and unexpected errors log their type and stack trace but not their message.
  When an engine fails, its last error lines are logged, and they may quote folder names.
- Nothing is sent anywhere: DiskSprawl makes no outgoing connections.
- Deleting a directory deletes its history; scans beyond the retention lose their folders.

## The public demo

The [demo](demo.md) is a static site: the interface answers its own API inside the visitor's
browser with synthetic data, and nothing is sent to a server or saved. It is served with the same
security headers, plus `worker-src 'self'` for its service worker.

## Dependencies and the supply chain

- Base images are pinned by digest, GitHub Actions by commit, and ncdu and gdu by version and
  SHA-256 checksum; Python and npm packages are locked.
- CodeQL analyzes every pull request, dependency review blocks new dependencies with known
  high-severity vulnerabilities, and Dependabot proposes updates weekly.
- Published images are built from scratch from the commit that passed CI, for amd64 and arm64.

## Deployment checklist

- Mount the folders read-only, with a read-only account on the NAS if possible.
- Keep the hardening of `docker-compose.yml`: `read_only`, `cap_drop: ALL`, `no-new-privileges`
  and the user 1000. Never run the container privileged or as root.
- Set `AUTH_PASSWORD` unless every device that can reach the port is trusted.
- Behind a reverse proxy: HTTPS, `AUTH_COOKIE_SECURE=true`, `APP_BIND_ADDRESS=127.0.0.1`, the
  proxy's name in `ALLOWED_HOSTS` and its address only in `TRUSTED_PROXY_IPS`.
- Do not expose DiskSprawl directly to the internet; use a VPN or an authenticating proxy.
- Update regularly and back up the `data` volume.
