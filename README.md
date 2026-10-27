# Chronos

PostgreSQL-backed distributed task scheduling and execution infrastructure.

**Status: Phase 1 complete — architecture and repository foundation.** The only API operation
implemented is process liveness. There is no queue execution, auth, recurring daemon,
production deployment or benchmark yet. The frontend states this scope explicitly.

## Design

- [Architecture and diagrams](ARCHITECTURE.md)
- [Database proposal](docs/database/README.md), [API proposal](docs/API.md)
- [PostgreSQL research](docs/POSTGRESQL_RESEARCH.md), [ADRs](docs/adr/0001-postgresql-queue.md)
- [Threat model](docs/THREAT_MODEL.md), [roadmap](docs/ROADMAP.md)
- [Verified state and next steps](docs/PROJECT_STATE.md)
- [Phase 1 CI and local verification evidence](docs/VERIFICATION.md)

## Prerequisites

- Git; Python **3.14.x**; uv **0.13.6**; Node.js **24.x** with npm (tested with 11.19.0).
- Docker Engine/Desktop running **Linux containers**, Compose v2, and available
  local ports 5432/8000/3000. Docker is needed for PostgreSQL/container checks,
  not Python unit tests or frontend compilation.
- Network access to PyPI, npm, GitHub and container registries for first installation.
- Windows PowerShell, macOS or Linux; commands below run from the repository root.

Install uv following [the official installation instructions](https://docs.astral.sh/uv/getting-started/installation/),
or `python -m pip install uv==0.13.6`. Python's installer must provide pip.
`uv sync --locked` creates the project environment; do not commit .venv.

```sh
git clone https://github.com/Saurx9611/chronos-distributed-scheduler.git
cd chronos-distributed-scheduler
uv sync --locked
npm --prefix frontend ci
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv run --no-sync pytest -q -m "not integration"
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

For PostgreSQL, copy .env.example to .env (`Copy-Item .env.example .env` in
PowerShell, `cp .env.example .env` on POSIX). Set POSTGRES_PASSWORD to a unique
local value. Do not commit the file or use the local superuser in production.

```sh
docker compose config --quiet
docker compose up -d --wait postgres
```

Set `CHRONOS_TEST_DATABASE_URL` to `postgresql://chronos:YOUR_URL_ENCODED_PASSWORD@localhost:5432/chronos`.
In PowerShell use `$env:CHRONOS_TEST_DATABASE_URL = '...'`; POSIX uses
`export CHRONOS_TEST_DATABASE_URL='...'`. Use a disposable development DB: the
test creates a unique schema, validates proposal constraints and rolls it back.

```sh
uv run --no-sync pytest -q
uv run --no-sync alembic -c backend/alembic.ini heads
docker compose --profile foundation build
```

An unset test URL explicitly skips the PostgreSQL test; skipped does not mean passed.
There are no migration revisions yet, so `alembic heads` returns no heads. Do not
apply proposal.sql to production. `CHRONOS_DATABASE_URL` is reserved for Alembic
and must use `postgresql+psycopg://...` when revisions are added in Phase 2.

Run the minimal surfaces in separate terminals:

```sh
uv run --no-sync uvicorn chronos.api.main:app --reload
npm --prefix frontend run dev
```

Liveness: http://localhost:8000/health/live; generated docs: http://localhost:8000/docs;
frontend: http://localhost:3000. Alternatively use `docker compose --profile
foundation up --build` to run the containerized foundation. Stop local services with
`docker compose down`; the volume persists. Removing the volume deletes local data.

## Repository structure

```text
backend/
  src/chronos/api/       # current liveness HTTP surface
  src/chronos/engine/    # planned queue transaction boundary
  migrations/           # Alembic environment; no revision yet
frontend/src/app/       # Next.js foundation page
workers/                # execution process design boundary
scheduler/              # recurring generation process design boundary
infrastructure/docker/  # nonroot API/frontend images
tests/
  unit/                 # liveness and OpenAPI scope
  integration/          # real PostgreSQL DDL proposal checks
  concurrency/          # future competing worker/fault test acceptance
  load/                 # future benchmark methodology
docs/
  adr/                  # decisions 0001–0004
  database/             # relational proposal + executable SQL
.github/workflows/      # Python, frontend and container foundation checks
compose.yaml
pyproject.toml / uv.lock
AGENTS.md
ARCHITECTURE.md
```

CI runs immutable dependency installs and the checks above on Ubuntu/Python 3.14/
Node 24/PostgreSQL 18. Container builds are separate from unit correctness. See
the [Actions page](https://github.com/Saurx9611/chronos-distributed-scheduler/actions)
for actual hosted results. Reproduce failures before advancing the roadmap.
