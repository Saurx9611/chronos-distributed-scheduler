# Project state

Updated: 2026-10-27. Phase 1 complete; Phase 2 has not begun.

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

Foundation commit: `f26e5e725c34dfa4e568476dd6e8853807d5bb99`.
[Hosted verification](https://github.com/Saurx9611/chronos-distributed-scheduler/actions/runs/40476193112)
completed successfully; each Python, frontend and containers job was inspected.

- Local: Ruff lint/format and strict mypy passed; pytest 2 passed, 1 PostgreSQL
  test explicitly skipped; Alembic heads and Compose validation passed.
- Local: clean npm ci, ESLint 10, TypeScript and Next.js production build passed.
  npm audit and pip-audit reported no known vulnerabilities at verification time.
- Hosted: locked Python install, lint/format/types, tests against PostgreSQL 18,
  Python advisory scan and Alembic scaffold check all passed.
- Hosted: clean npm install, lint/types/production build and npm audit passed.
- Hosted: Compose validation and both Docker image builds passed.
- Fresh clone from the public GitHub repository succeeded and matched the
  foundation commit. Hosted checkout then installed dependencies from lockfiles.

Local Docker's Linux engine is unavailable, so database/container execution was
verified in hosted CI. Windows sandbox blocked local test/build subprocesses;
the authorized reruns outside the sandbox passed. See VERIFICATION.md for commands.

## Remaining limitations

No queue engine, runtime worker, recurring daemon, auth, deployed migration,
cloud deployment or benchmark exists yet. The DDL is a proposal. Runtime guarantees
remain design commitments pending Phase 2–4 tests. Image/action digest pinning and
production hardening remain release gates. No cloud resources or costs were incurred.

## Next step

Phase 2: promote reviewed schema to an Alembic migration and implement atomic
claim, renewal and finalization invariants with independent-session PostgreSQL
tests before adding authenticated job endpoints. Do not begin Phase 2 implicitly.
