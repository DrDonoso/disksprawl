# Contributing to DiskSprawl

Thanks for your interest in improving DiskSprawl! Bug reports, ideas, documentation fixes,
translations and code are all welcome.

## Contents

- [Ground rules](#ground-rules)
- [Project layout](#project-layout)
- [Prerequisites](#prerequisites)
- [Setting up](#setting-up)
- [Running locally](#running-locally)
- [Checks before opening a pull request](#checks-before-opening-a-pull-request)
- [Rules for changes](#rules-for-changes)
- [Commit messages](#commit-messages)
- [Pull requests](#pull-requests)
- [Reporting bugs and requesting features](#reporting-bugs-and-requesting-features)

## Ground rules

- Follow the [code of conduct](CODE_OF_CONDUCT.md).
- Report security problems privately, as described in [SECURITY.md](SECURITY.md), never in an
  issue or a pull request.
- For anything larger than a small fix, open an issue first to agree on the approach.
- Keep DiskSprawl simple and read-only: it measures folders, it never changes them.
- Use synthetic data only: no real folder names, host names, addresses or screenshots of your own
  servers in issues, code, tests or documentation.

## Project layout

The backend is a FastAPI application in `src/disksprawl/` (the scan engines in
`src/disksprawl/engines/`), with its tests in `tests/`. The React frontend, the static demo and the
browser tests live in `frontend/`. [docs/development.md](docs/development.md#repository-layout)
describes every folder.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) 0.13, which also installs Python 3.14.
- Node.js 26 with npm.
- Docker with the Compose plugin, to build and try the image.
- Optionally ncdu 2 and gdu, for the engine tests: `scripts/install-engines.sh .engines` installs
  the versions the image uses into `.engines/`; add it to `PATH`.

## Setting up

```bash
git clone https://github.com/DrDonoso/disksprawl.git
cd disksprawl
uv sync
cd frontend && npm ci && npx playwright install --with-deps chromium
```

## Running locally

### With Docker

```bash
python3 scripts/make_sample_tree.py sample
docker compose -f docker-compose.local.yml up -d --build
```

Open `http://localhost:7780`, add `/data/sample` and scan it.
`python3 scripts/make_sample_tree.py sample --mutate 1` changes the tree for the next scan.

### With hot reload

```bash
python3 scripts/make_sample_tree.py sample
DATA_DIR=data SCAN_ROOT="$PWD/sample" uv run uvicorn --factory disksprawl.app:app --port 7780 --reload
```

In another terminal, `cd frontend && npm run dev`, and open `http://127.0.0.1:5173`.
`npm run dev:demo` runs the demo instead, with no backend.

## Checks before opening a pull request

CI runs all of these; running them first saves a round trip.

### Backend

```bash
uv run pytest
uv run ruff check src tests scripts
uv run mypy
uv run python scripts/export_openapi.py --check
```

### Frontend

In `frontend/`:

```bash
npm run gen:api -- --check
npm run lint
npm test
npm run build && npm run e2e
npm run build:demo && npm run e2e:demo
```

## Rules for changes

### Translations

Every text of the interface is in `frontend/src/i18n/locales/en.ts` and `es.ts`, with the same
keys: the type checker fails when one is missing. If you do not speak Spanish, add the English text
and say so in the pull request.

### API changes

After changing the API, regenerate the contract and the types, and commit both:

```bash
uv run python scripts/export_openapi.py
cd frontend && npm run gen:api
```

Then update the demo's handlers in `frontend/src/demo/`, which must answer every operation.

### Database changes

Add an Alembic migration with an upgrade and a downgrade (see
[database migrations](docs/development.md#database-migrations)); `tests/test_migrations.py` fails
when the migrations and the models disagree.

### Scan engines

All engines must keep giving the same totals: extend `tests/test_engines_integration.py` when you
change how sizes are counted, and keep the parsers' fixtures in `tests/fixtures/` synthetic. Engines
are started with argument lists, never through a shell. To update ncdu or gdu, change their
versions and checksums in `scripts/install-engines.sh` and in `licenses/THIRD-PARTY.txt`.

### The demo

The demo shows the whole interface with synthetic data. New features should look right in it, and
its tests (`npm run e2e:demo`) must pass. If a feature cannot work without a server, answer it
with the explicit "not available in the demo" error.

### Synthetic data only

Tests, fixtures, documentation and screenshots use invented names and generic paths such as
`/mnt/storage` or `/data/media`. Screenshots come from the demo (`npm run screenshots`, see
[docs/demo.md](docs/demo.md#updating-the-screenshots)).

### Security

Keep the container's hardening and the read-only design. Changes to authentication, sessions,
the host and origin checks, path handling or how engines are started need tests and a note in the
[security model](docs/security-model.md).

## Commit messages

Use short, imperative messages with a type prefix, such as `feat: …`, `fix: …`, `docs: …`,
`test: …`, `build: …`, `ci: …` or `chore: …`, and explain the reason in the body when it is not
obvious.

## Pull requests

- Target `main`, and fill in the pull request template.
- Keep each pull request focused on one change.
- The `application` check must pass: it requires the backend, frontend and Docker jobs.
- For interface changes, include screenshots taken from the demo or with synthetic data.

## Reporting bugs and requesting features

Use the [issue templates](https://github.com/DrDonoso/disksprawl/issues/new/choose). For bugs,
include the image tag, how the folders are mounted and the engine you use, without personal
folder names. The [live demo](https://demo.disksprawl.drdonoso.com) helps check whether something
also happens there.
