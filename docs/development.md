# Development

## Contents

- [Toolchain](#toolchain)
- [Repository layout](#repository-layout)
- [Running locally](#running-locally)
- [Backend checks](#backend-checks)
- [Frontend checks](#frontend-checks)
- [API contract and generated types](#api-contract-and-generated-types)
- [Database migrations](#database-migrations)
- [Translations](#translations)
- [What's new notes](#whats-new-notes)
- [The demo](#the-demo)
- [Continuous integration](#continuous-integration)
- [Supply chain](#supply-chain)
- [Releases](#releases)
- [Development and test variables](#development-and-test-variables)

## Toolchain

| Tool | Version | Used for |
| --- | --- | --- |
| [uv](https://docs.astral.sh/uv/) | 0.13.8 | Python 3.14 and the backend's dependencies (`uv.lock`). |
| Node.js and npm | 26 | The frontend (`frontend/package-lock.json`). |
| Docker with Compose | recent | The image, the local stack and its smoke test. |
| GNU du, ncdu 2 and gdu | as in the image | The engine integration tests. |

`scripts/install-engines.sh .engines` downloads the ncdu and gdu builds that the image uses,
checked by their SHA-256 checksums, into `.engines/` (ignored by git); add that folder to `PATH`.
On systems whose `du` is not GNU du, the tests use `gnudu` when it exists.

## Repository layout

```text
src/disksprawl/          FastAPI application
  app.py                 application factory, startup and shutdown, static files
  api.py, schemas.py     HTTP API and its request and response models
  queries.py             tree, changes and history queries
  scanning.py            scan queue, worker processes, results, retention
  scan_logs.py           the log file of each scan, and its expiry
  scheduler.py, cron.py  schedules (APScheduler) and cron expressions
  capacity.py, paths.py  volume capacity (statvfs) and the /data boundary
  notifications.py       notifications: what each device is told, queued and sent in rounds
  webpush.py             Web Push: encryption, VAPID signature, the push services
  security.py, auth.py   host and origin checks, headers, optional password
  engines/               the worker process and the Native, du, ncdu and gdu engines
  migrations/            Alembic migrations
tests/                   pytest suite, with engine output fixtures
scripts/                 sample tree, OpenAPI export, engine installer, smoke tests
frontend/                React application, demo (src/demo) and Playwright tests (e2e)
docs/                    documentation and README screenshots
licenses/                notices of the third-party programs in the image
```

## Running locally

**From the source code, with hot reload**: the backend on port 7780 and Vite on 5173, which
forwards `/api` to the backend:

```bash
uv sync
python3 scripts/make_sample_tree.py sample
DATA_DIR=data SCAN_ROOT="$PWD/sample" uv run uvicorn --factory disksprawl.app:app --port 7780 --reload
```

```bash
cd frontend
npm ci
npm run dev        # http://127.0.0.1:5173
```

Install the engines as above to scan with du, ncdu and gdu; without them only the Native engine is
available.

**With Docker**, as in production but built from the checkout:

```bash
python3 scripts/make_sample_tree.py sample
docker compose -f docker-compose.local.yml up -d --build
```

Then open `http://localhost:7780` and add `/data/sample`. The sample tree is synthetic: large files
are sparse, so it takes almost no space, and it includes hard links, a symbolic link, a recycle bin
and a `.snapshot` folder. `python3 scripts/make_sample_tree.py sample --mutate 1` (then 2, 3…)
changes it, to see the **Changes** view after another scan.

## Backend checks

```bash
uv run pytest
uv run ruff check src tests scripts
uv run mypy
uv run python scripts/export_openapi.py --check
```

- The integration tests in `tests/test_engines_integration.py` scan the same generated tree with
  every engine and require identical totals in both measures. An engine whose binary is missing is
  skipped; CI installs all of them.
- A couple of permission tests are skipped when run as root, because root can read anything.
- The tests pass on any day, at any hour and in any time zone: the server's time zone is
  `Etc/UTC`, and the scheduler starts paused (`Scheduler.paused`, also in `smoke_server.py`), so no
  schedule fires in the middle of a test; tests call the scheduler's methods instead. To check,
  run them with the clock moved, for example with libfaketime:
  `LD_PRELOAD=/usr/lib/x86_64-linux-gnu/faketime/libfaketimeMT.so.1 FAKETIME=+45d uv run pytest`.
  Use relative offsets (`+2d`, `+1y`): they also move the monotonic clock that `time.sleep` needs.
- Nothing a test sends reaches a push service: `tests/conftest.py` replaces the transport of
  `webpush` with a stand-in that records the requests (`tests/push_helpers.py` also decrypts them,
  as a browser would), and the rounds of notifications are paused like the scheduler
  (`Notifier.paused`): tests call `Notifier.tick()` with the moment they need.
  `smoke_server.py` answers for the push services too.
- `scripts/docker_smoke.sh` builds nothing: it starts the image tagged `IMAGE_TAG` (default `ci`)
  with Compose and a sample tree, checks `/health`, `/api/version`, the four engines, a scan with
  each engine in both measures with matching totals, the user 1000, the read-only file system and
  the password protection. Its directories have no schedule, so no scheduled scan joins in. Build
  the image first with `docker build --build-arg IMAGE_TAG=ci -t drdonoso/disksprawl:ci .`.

## Frontend checks

In `frontend/`:

```bash
npm run gen:api -- --check
npm run lint
npm test
npm run build
npm run e2e
npm run build:demo
npm run e2e:demo
```

- `npm test` runs the unit tests (Vitest and Testing Library): formatting, the squarified layout,
  colors, forms, components and the demo's store and handlers. They run in UTC with `Date` frozen
  at `NOW` (`src/test/clock.ts`, set in `src/test-setup.ts`; timers keep running), so texts such
  as "3 hours ago" and the demo's data are the same on any day. Use `NOW` for a test's dates, and
  `vi.setSystemTime` to try another moment.
- The Playwright tests use the real clock and must pass at any hour: never expect an exact age
  from data that depends on the time of day. To try a moment, call
  `page.clock.setSystemTime(...)` before `page.goto(...)`; the demo's data follows the browser's
  clock.
- `npm run e2e` runs the Playwright tests of the real application in Chromium against
  `scripts/smoke_server.py`: a disposable server on port 7889 with a fresh database and a sample
  tree, serving the build in `frontend/dist` (run `npm run build` first). They add a directory,
  scan it, explore its treemap and changes, and check the settings, the menu, the language and
  theme menus (with a browser in Spanish) and a 3440 px wide window, with accessibility (axe)
  checks. `e2e/push.spec.ts` turns the notifications of the browser on and off and changes its
  choices, with stand-ins for the permission and the browser's subscription (Chromium has no push
  service in tests). Install the browser once with `npx playwright install --with-deps chromium`.
- `npm run e2e:demo` tests the built demo, see [the demo](demo.md).

## API contract and generated types

The backend's OpenAPI contract is committed as `frontend/src/api/openapi.json`, and the frontend's
types are generated from it into `frontend/src/api/schema.gen.ts`. After changing the API:

```bash
uv run python scripts/export_openapi.py
cd frontend && npm run gen:api
```

CI fails when either file is out of date. The demo's handlers use the same types, so the demo has
to follow API changes too.

## Database migrations

The schema is managed by [Alembic](https://alembic.sqlalchemy.org/) in
`src/disksprawl/migrations/versions`, and the application migrates the database on every start.
After changing the models in `src/disksprawl/db.py`, add a migration with an upgrade and a
downgrade:

```bash
DATA_DIR=data uv run alembic revision --autogenerate -m "describe the change"
DATA_DIR=data uv run alembic upgrade head
```

Review generated migrations: SQLite needs batch operations for most column changes.

## Translations

The interface is in English and Spanish: English until Spanish is chosen in the top bar, whatever
the browser's language, and the browser keeps the choice. `frontend/src/i18n/locales/en.ts` is the
source of every text and `es.ts` must have the same keys, which the type checker enforces. Plurals
use `one` and `other` keys, so never name a group of texts `other`. Texts are plain: no HTML except
the few `<code>` chunks rendered by the `rich` helper.

## What's new notes

Changes people will notice come with a note in `frontend/src/whatsNew/notes/`, one JSON file named
`YYYY-MM-DD-slug.json`, shown in the top bar's bell dialog until each person reads it:

```json
{
  "kind": "new",
  "en": { "title": "A short title", "body": "One line saying what changed and why it helps." },
  "es": { "title": "Un título corto", "body": "Una línea que dice qué cambió y para qué sirve." }
}
```

`kind` is `new`, `improved` or `fixed`; titles have at most 70 characters and bodies 300, on one
line. The file name is the note's permanent id: never rename or delete a released note. The server
stores which ids have been read (`GET /api/whats-new`, `POST /api/whats-new/seen`); a new install
marks everything read without showing it.

## The demo

The static demo shares the whole interface with the application and answers the API in the
browser. Every API change needs a matching handler in `frontend/src/demo/`, or the demo's unit
tests fail. See [the demo](demo.md).

## Continuous integration

[`ci.yml`](../.github/workflows/ci.yml) runs on every pull request to `main`, on demand, and as the
first step of every release:

| Job | What it checks |
| --- | --- |
| `backend` | Installs the engines, then pytest, ruff, mypy and the OpenAPI contract. Lockfiles must resolve from PyPI. |
| `frontend` | Generated types, oxlint, Vitest, the build, the Playwright tests of the app, the demo build and its Playwright tests. Lockfiles must resolve from the npm registry. |
| `docker` | Builds the production image and runs `scripts/docker_smoke.sh`. |
| `application` | Passes only when the three jobs above pass; it is the check `main` requires. |

The repository variable `CI_RUNNER` can name a self-hosted runner label for the repository's own
branches. Pull requests from forks always run on GitHub-hosted runners.

[`codeql.yml`](../.github/workflows/codeql.yml) analyzes the Python, TypeScript and workflow code,
and [`dependency-review.yml`](../.github/workflows/dependency-review.yml) blocks pull requests that
add dependencies with known high-severity vulnerabilities.

## Supply chain

- Base images are pinned by digest and GitHub Actions by commit, with the version in a comment.
- `uv.lock` and `package-lock.json` pin every package, and CI rejects lockfiles that point to
  another registry.
- ncdu and gdu are downloaded from their official releases and verified against the SHA-256
  checksums in `scripts/install-engines.sh`.
- [Dependabot](../.github/dependabot.yml) proposes weekly updates of the Python and npm packages,
  the actions and the Docker images. To update ncdu or gdu, change their versions and checksums in
  `scripts/install-engines.sh` and in [the notices](../licenses/THIRD-PARTY.txt).
- The published image is built without a layer cache, from the commit that passed CI.

## Releases

[`docker-deploy.yml`](../.github/workflows/docker-deploy.yml) runs on every push to `main` that
changes more than documentation. It runs the whole CI, then builds the image for `linux/amd64` and
`linux/arm64` and pushes it to Docker Hub as `drdonoso/disksprawl:latest` and as a CalVer tag: the
date (`20261115`), with a suffix for further releases that day (`20261115.02`). Finally it creates
a GitHub release with that tag. Its notes start with the "What's new" notes added since the
previous release (`scripts/release_notes.py`), followed by the generated list of merged pull
requests.

It needs two repository secrets: `DOCKER_USERNAME` and `DOCKER_PASSWORD`, a Docker Hub access
token with permission to push to the repository. Forks build and test, but never publish.

## Development and test variables

| Variable | Used by | Purpose |
| --- | --- | --- |
| `DATA_DIR` | backend | Folder of the database, the scans' logs and the session key (`data`). |
| `SCAN_ROOT` | backend | Folder every directory must be inside (`/data`). |
| `FRONTEND_DIR` | backend | Built web interface served at `/` (`frontend/dist`). |
| `E2E_PORT`, `E2E_BASE_URL` | `npm run e2e` | Port of the test server (7889), or another, empty server to test. |
| `E2E_SAMPLE_ROOT` | `npm run e2e` | Where `smoke_server.py` creates its sample tree; set by `playwright.config.ts`. |
| `E2E_PYTHON` | `npm run e2e` | Python that runs `smoke_server.py`; the repository's `.venv` by default. |
| `DEMO_E2E_PORT`, `DEMO_E2E_BASE_URL` | `npm run e2e:demo` | Port of the local demo, or a deployed demo to test. |
| `SCREENSHOT_BASE_URL`, `SCREENSHOT_OUT_DIR`, `SCREENSHOT_ONLY` | `npm run screenshots` | See [updating the screenshots](demo.md#updating-the-screenshots). |
| `PLAYWRIGHT_CHANNEL` | Playwright | A browser channel instead of Playwright's Chromium. |
| `IMAGE_TAG`, `COMPOSE_PROJECT_NAME` | `scripts/docker_smoke.sh` | Image to test and Compose project. |
