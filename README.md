# Chronos

PostgreSQL-backed distributed task scheduling and execution infrastructure.

**Status: Phase 4 — distributed fault tolerance and recovery.** Repository operations implement
idempotent submission, atomic claims, renewal, completion/failure, cancellation,
expiry recovery and worker registration/heartbeats. Independent workers now execute
four allowlisted workloads with concurrency, lease renewal, child isolation and
shutdown/recovery. Authenticated retry/replay and worker-health APIs now exist, with
reason-coded recovery and real crash/duplicate-effect tests. General submission/read
APIs, recurring generation, production deployment and benchmarks remain later phases.

## Design

- [Architecture and diagrams](ARCHITECTURE.md)
- [Database proposal](docs/database/README.md), [API proposal](docs/API.md)
- [Implemented transaction protocol and actual claim SQL](docs/database/TRANSACTIONS.md)
- [Worker runtime, three-process demo and real signal tests](docs/WORKER_RUNTIME.md)
- [Recovery APIs and fault-tolerance protocol](docs/FAULT_TOLERANCE.md)
- [Failure-mode matrix and observed outcomes](docs/FAILURE_MODE_MATRIX.md)
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
uv run --no-sync pytest -q -m "not integration and not compose"
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
uv run --no-sync pytest -q -m "not compose"
uv run --no-sync alembic -c backend/alembic.ini heads
docker compose --profile foundation build
```

An unset test URL skips integration tests locally; skipped does not mean passed.
Set CHRONOS_REQUIRE_DB=1 to require the DB, as CI does. Tests migrate a unique schema
per case and cover real concurrent consumers; use a disposable development database.
`alembic heads` reports revision 0003. To install it, set CHRONOS_DATABASE_URL to
`postgresql+psycopg://...`, then run:

```sh
uv run --no-sync alembic -c backend/alembic.ini upgrade head
```

Apply migrations before runtime services start. Do not apply historical proposal.sql
alongside migrations. Downgrading to base destroys data and is only for disposable DBs.
The migration URL and the integration-test URL are separate deliberately: tests own
isolated schemas and do not install application tables in your default schema.

To run three independent worker processes and actual workloads, follow
[the worker demo](docs/WORKER_RUNTIME.md#configuration-and-reproduction).
The Compose process tests use a separate CHRONOS_RUN_COMPOSE_TESTS=1 gate and do
not require the integration suite's database URL; they create disposable clusters.

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
  src/chronos/engine/    # parameterized transactional queue and ownership models
  src/chronos/worker/    # supervisor, fixed child entry point and allowlisted handlers
  migrations/           # Alembic revisions 0001..0003
frontend/src/app/       # Next.js foundation page
workers/                # worker operations entry guide
scheduler/              # recurring generation process design boundary
infrastructure/docker/  # nonroot API/frontend images
tests/
  unit/                 # liveness and OpenAPI scope
  integration/          # migrations, transactions, competing consumers and fault injection
  process/              # worker crashes/pauses, real external effects and DB outage tests
  concurrency/          # future process-crash/fault test acceptance
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
