# Notes for AI coding agents

DiskSprawl is a self-hosted disk usage analyzer: a FastAPI backend (`src/disksprawl`) with SQLite,
a React frontend (`frontend/`), a static demo (`frontend/src/demo`) and one Docker image.
[CONTRIBUTING.md](CONTRIBUTING.md) and [docs/development.md](docs/development.md) are the
reference; these are the rules that matter most.

- **Checks**: `uv run pytest`, `uv run ruff check src tests scripts`, `uv run mypy`,
  `uv run python scripts/export_openapi.py --check`; in `frontend/`: `npm run gen:api -- --check`,
  `npm run lint`, `npm test`, `npm run build`, `npm run e2e`, `npm run build:demo`,
  `npm run e2e:demo`.
- **API changes**: regenerate `frontend/src/api/openapi.json` and `schema.gen.ts`, and update the
  demo's handlers.
- **Texts**: every interface text goes in both `frontend/src/i18n/locales/en.ts` and `es.ts`.
- **Schema changes**: add an Alembic migration with a downgrade.
- **Read-only**: DiskSprawl never writes to the analyzed folders. Keep the container's hardening,
  the `/data` boundary, and engines started with argument lists, never a shell.
- **Synthetic data only**: no real folder names, host names, addresses or personal data in code,
  tests, fixtures, docs or screenshots; use generic paths such as `/mnt/storage`.
- **CI**: the required check is the job `application` in `.github/workflows/ci.yml`; keep its
  name.
