# Project state

Updated: 2026-10-27. Phase 4 implemented; local correctness gates passed, final hosted verification pending.

## Phase 4 implementation and gates

- Migration 0003: per-job effect identity/retry policy, causal event/audit fields,
  append-only history, command idempotency, scoped HMAC API credentials and rate windows.
- Expiry/deadline reclamation, lost-heartbeat monitoring and reason-coded stale-owner
  rejection preserve generation/token fencing. Terminal generations never reopen.
- Authenticated retry/replay APIs create new jobs with preserved/new effect identity,
  respectively; worker health is infrastructure-admin only.
- Real independent HTTP destination and durable crash-boundary gates demonstrate
  one deduplicated effect versus two unsafe effects after committed work loses its ACK.
- Local complete process regression: 14 passed; database/API/handler gate: 72 passed.
  See FAILURE_MODE_MATRIX.md, FAULT_TOLERANCE.md and PHASE4_VERIFICATION.md.

Earlier phases below are historical. Current remaining work includes general job
submission/read/history APIs, recurring generation, dashboards, performance evidence,
production grants/retention and deployment. Phase 4 follows the user's recovery scope,
not the earlier proposed recurrence scope.

## Phase 3 implementation and gates

- Standalone Python worker, separate control/lease pools, bounded child-process
  execution, typed allowlisted workloads, lease guards, heartbeats and recovery.
- Four real data-only workloads: PNG metadata, CSV transformation, seeded report,
  and explicitly simulated notification with deterministic delivery identity.
- Migration 0002 records execution dispatch/result and worker lifecycle state.
- Compose's workers profile runs PostgreSQL, one migrator and three independent
  workers. Signals, lease loss, DB disconnects and timeouts stop or abandon work safely.
- Local database/runtime and handler tests: 59 passed; six real Linux Compose scenarios
  passed, including SIGTERM, SIGKILL, forced drain and PostgreSQL stop/restart.
- See WORKER_RUNTIME.md and PHASE3_VERIFICATION.md for exact reproduction and evidence.
- Implementation commit: `46148810170ebeb873946e3a618612c7257433ed`.
  [Hosted CI](https://github.com/Saurx9611/chronos-distributed-scheduler/actions/runs/40483607490)
  passed all four jobs, including all six Linux process scenarios. Redacted logs,
  facts and JUnit were uploaded as worker-process-evidence.
- Local project chronos-phase3-demo is running PostgreSQL on loopback port 15434
  with three workers; 12 seeded mixed workloads all succeeded. This is a local demo,
  not a cloud deployment. Its connection environment remains in ignored .tools storage.

Phase 1/2 evidence below is historical. Remaining current limits: no authenticated
job HTTP API, recurring generation, production deployment, downstream exactly-once
effects or throughput benchmark. No later-phase features were started.

## Phase 2 implementation and local gates

- Alembic 0001: tenants, jobs, job_attempts, schedules, schedule_occurrences,
  worker_registrations, submission_keys, job_events and audit_events.
- Validated job submission, UTC scheduling, integer priorities, bounded attempts,
  DB-time leases/deadlines and generation+UUID ownership tokens.
- Parameterized READ COMMITTED atomic claim, heartbeat, renewal, success/failure,
  cancellation and competing expiry recovery repository methods.
- Claims/attempts/events/audit and finalizations commit atomically; execution occurs
  outside repository transactions. See database/TRANSACTIONS.md for exact SQL.
- Current local gates: 42 tests passed, zero skips, against PostgreSQL 18.6;
  Ruff lint/format, strict mypy (including integration tests), Alembic heads and
  migration up/down/up passed. Three rounds each verified 8 simultaneous consumers,
  80 distinct jobs, 80 tokens and consistent ownership records.
- ADR-0005 records priority/starvation tradeoffs, execution deadlines, idempotency
  reservations and the narrowed Phase 2 scope requested by the user.
- Implementation commit: `93a793e82210b832ea7df17c72f4316da7c6689d`.
  [Hosted CI](https://github.com/Saurx9611/chronos-distributed-scheduler/actions/runs/40479244896)
  passed all three jobs; individual test/type/lint/audit/container steps were inspected.
  See PHASE2_VERIFICATION.md for the validation matrix.

The earlier Phase 1 evidence below remains historical. Current limitations:
no handler execution process, authenticated job HTTP API, recurring generator,
automatic DB transaction retries, production audit grants, deployment or benchmark.
Database gates passed; no later-phase implementation was started.

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

## Phase 1 limitations (historical)

No queue engine, runtime worker, recurring daemon, auth, deployed migration,
cloud deployment or benchmark exists yet. The DDL is a proposal. Runtime guarantees
remain design commitments pending Phase 2–4 tests. Image/action digest pinning and
production hardening remain release gates. No cloud resources or costs were incurred.

## Next step

Next recommended step: complete authenticated job submission/detail/history APIs,
then the recurring scheduler with competing-replica/occurrence tests. Keep both
separate from this completed recovery milestone; no subsequent phase was started.
No later phase is authorized implicitly by this phase's completion.
