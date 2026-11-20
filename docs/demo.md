# The public demo

The [live demo](https://demo.disksprawl.drdonoso.com) is the real DiskSprawl interface running
entirely in the browser: no server, no database and nothing saved. Its data is synthetic and
starts over on every reload.

## Contents

- [Using the demo](#using-the-demo)
- [The data](#the-data)
- [What differs from a real server](#what-differs-from-a-real-server)
- [Running it locally](#running-it-locally)
- [Updating the screenshots](#updating-the-screenshots)
- [How it works](#how-it-works)
- [Deploying to Cloudflare](#deploying-to-cloudflare)

## Using the demo

A welcome dialog explains the demo once per page load, and a bar at the bottom reminds that the
data is synthetic, with a link to the source code. There is no password. Everything else works as
in a real installation:

- browse the four directories, their treemaps (also of earlier scans, opened from the history),
  changes between any two kept scans and history;
- download the log of each scan, written as a real server writes it;
- add, edit, reorder and delete directories, choosing folders in a fictional `/data`;
- change the schedules (the next runs are computed in the browser), engines, threads, pace and
  every other option, and the general settings;
- press **Scan now**: the scan is queued, shows a few seconds of progress and adds a new scan with
  some changes, so the **Changes** view and the history update.

## The data

The demo describes a fictional home server with four directories, generated deterministically and
relative to the current date, so it always looks recent, whatever the day and the hour:

| Directory | Engine and schedule | What it shows |
| --- | --- | --- |
| **Media** (`/data/media`) | gdu, 4 threads, daily | Series and movies growing, downloads that come and go, a recycle bin that gets emptied, and a film added since the previous scan. |
| **Photos** (`/data/photos`) | Native, 2 threads, gentle, daily | A folder per year, growing steadily; same volume as Media. |
| **Backups** (`/data/backups`) | ncdu, 1 thread, weekly | A volume above the critical threshold and scans with read errors. |
| **Documents** (`/data/documents`) | du, every 6 hours | A small volume, and a last attempt that failed because the folder was not mounted. |

A year of scans, capacity measurements every 15 minutes and the usual retention (the last 30 scans
of each directory keep their folders) make the history and the comparisons realistic. Every name
is invented.

## What differs from a real server

- Scans are simulated: they take about seven seconds, whatever the engine, and change a few
  folders at random.
- The folder picker browses an invented `/data` with the four directories and a couple of other
  folders.
- The server's time zone is the browser's.
- There are no notifications: there is no server to send them. **⚙ Settings → Notifications**
  says so, and the API's notification operations reply with a clear "not available in the demo"
  error. Everything else works.

## Running it locally

In `frontend/` (no backend needed):

| Command | What it does |
| --- | --- |
| `npm run dev:demo` | Vite development server in demo mode. |
| `npm run build:demo` | Type-checks and builds the demo into `dist-demo/` (never touches `dist/`). |
| `npm run preview:demo` | Serves `dist-demo/` with the demo's security headers. |
| `npm run e2e:demo` | Playwright tests of the **built** demo on port 4183 (`DEMO_E2E_PORT`): the welcome dialog, banner and security headers, every screen in English and Spanish with accessibility (axe) checks, earlier scans opened from the history, downloading a scan's log, a scan, adding, editing and deleting a directory, the language and theme menus with a browser in Spanish, the phone layout and windows 1920, 2560 and 3440 px wide. They fail on any request the demo does not answer and on any console error. Run `npm run build:demo` first. |
| `npx vitest run src/demo` | Unit tests of the demo's API and data. |

`DEMO_E2E_BASE_URL=https://demo.disksprawl.drdonoso.com npm run e2e:demo` checks the deployed
demo instead of a local build.

The demo relies on a service worker, which browsers allow only in secure contexts: `localhost`,
`127.0.0.1` and HTTPS. Elsewhere, and in browsers or private windows without service workers, the
demo explains why it cannot start.

## Updating the screenshots

The screenshots of the [README](../README.md) come from the demo. To capture them again, in
`frontend/`:

```bash
npm run build:demo
npm run screenshots
```

[`scripts/capture-screenshots.mjs`](../frontend/scripts/capture-screenshots.mjs) serves
`dist-demo/` on port 4183, unless something already answers on `SCREENSHOT_BASE_URL`, and writes
`docs/screenshots/*.png` with Playwright's Chromium: in English, with the light theme, at 1440×900
and on a 390×844 phone, with the demo banner hidden. The clock is fixed at 17:30 UTC of the day of
the capture. The script fails if a page logs an error; `SCREENSHOT_ONLY=overview,mobile` captures
only some images. Optimize them losslessly before committing them, for example with
`docker run --rm -v "$PWD/../docs/screenshots:/work" ghcr.io/shssoichiro/oxipng -o 4 --strip safe --recursive /work`.

## How it works

- The demo is the normal frontend built with `vite build --mode demo`, which loads
  `frontend/.env.demo` (`VITE_DEMO=1`) and writes `dist-demo/`.
- [Mock Service Worker](https://mswjs.io) answers every `/api` request in the browser from an
  in-memory store (`frontend/src/demo/`). The handlers use the generated API types and mirror the
  backend's validation and error codes. A request nobody handles gets a `501` and a console
  error, and a unit test fails if an API operation has neither a handler nor an explicit "not
  available in the demo" answer (`UNAVAILABLE_IN_DEMO` in `handlers.ts`, with the reason).
- Production builds contain no demo code: `main.tsx` loads it only behind a build-time check, and
  `mockServiceWorker.js` is removed from `dist/`. The demo, in turn, ships without the service
  worker of the notifications (`sw.js`).
- The static host gets a `_headers` file with the backend's security headers and Content Security
  Policy, plus `worker-src 'self'` for the service worker.

## Deploying to Cloudflare

The demo is served by Cloudflare Workers as static assets, configured in
[`frontend/wrangler.jsonc`](../frontend/wrangler.jsonc): a Worker named `disksprawl-demo` that
serves `dist-demo/` with a single-page-application fallback, on the custom domain
`demo.disksprawl.drdonoso.com`. There is no Worker script and no secret.

With **Workers Builds** (Cloudflare dashboard, **Workers & Pages**, create an application by
importing the `DrDonoso/disksprawl` repository):

| Setting | Value |
| --- | --- |
| Root directory | `/frontend` |
| Build command | `npm run build:demo` |
| Deploy command | `npx wrangler@4 deploy` |
| Preview command (non-production branches) | `npx wrangler@4 preview` |
| Environment variable | `NODE_VERSION=24` |

The empty `previews` block in `wrangler.jsonc` enables branch previews; the static demo needs no
preview-specific bindings. Keep `wrangler deploy` for production; `wrangler preview` publishes an
isolated preview of a branch instead.

The custom domain comes from `wrangler.jsonc` and needs the domain's zone in the same Cloudflare
account; otherwise, add the domain in the Worker's **Domains & Routes** settings and remove
`routes` from the file. From a terminal, run `npx wrangler@4 login` once, then
`npm run build:demo && npx wrangler@4 deploy`.

To publish your own copy, change `name` and `routes` in `wrangler.jsonc`.
