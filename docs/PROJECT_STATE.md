# Project state

Updated: 2026-10-27. Phase 1 validation in progress.

## Implemented

- Official PostgreSQL research and detailed architecture/state/lease/failure design.
- Executable relational schema proposal, not a released migration.
- FastAPI liveness endpoint and contract tests; minimal Next.js foundation page.
- Python uv and npm dependency management, lint/types/test configuration.
- Alembic environment, container definitions and Compose PostgreSQL.
- GitHub Actions jobs for Python, frontend and container foundations.
- Git repository connected to https://github.com/Saurx9611/chronos-distributed-scheduler.

## Decisions

ADRs 0001–0004 select PostgreSQL READ COMMITTED/SKIP LOCKED, generation+token
leases, separate replicated recurrence, and polling with optional notification hints.
Fixed UTC intervals only initially; cancellation does not undo external effects.

## Validation

Pending final local checks and first GitHub Actions run. No queue engine, runtime
worker, recurring daemon, auth, deployment or benchmark exists in this phase.
Local Docker CLI is installed but the Linux engine is unavailable. Real PostgreSQL
proposal tests and container builds must therefore be verified in hosted CI.

## Next step

Finish Phase 1 validation and record immutable CI/commit references. Then Phase 2:
promote reviewed schema to Alembic migration and implement transaction invariants
with independent-session PostgreSQL tests before adding job endpoints.
