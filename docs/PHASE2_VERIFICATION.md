# Phase 2 verification

Date: 2026-10-27. Local PostgreSQL: 18.6 (Debian 18.6-1.pgdg13+2),
running in an isolated Docker Compose project. This is correctness testing, not a benchmark.

Local result: **42 passed, zero skips**. Ruff lint/format and strict mypy including
integration tests passed. Alembic reports 0001 (head); tests exercised fresh upgrade,
downgrade and re-upgrade.

Implementation commit: `93a793e82210b832ea7df17c72f4316da7c6689d`.
[Hosted CI run](https://github.com/Saurx9611/chronos-distributed-scheduler/actions/runs/40479244896)
passed. Each job and step was inspected: Python locked install, lint/format, strict
types, real PostgreSQL tests, advisory scan and migration head; frontend clean install,
lint/types/production build/audit; Compose validation and both container image builds.
The CI environment requires a database URL rather than silently skipping DB tests.

Evidence covered:

- Three rounds of eight consumers synchronized by a barrier, each competing for
  80 jobs in batches of 10: all jobs/tokens distinct, attempt rows agree with leases.
- Locked high-priority row skipped without waiting for its holder to commit;
  a returned claim's job row immediately lockable with NOWAIT in another session.
- Concurrent submissions with the same key create one job; changed input conflicts.
- Injected audit failures roll back submission/claim/completion with attempt/history.
- Expired or forged worker/token/generation rejected on renew, success and failure;
  expiry is rechecked after blocking on a row lock, not against stale statement time.
- Recovery increments the next claim's generation, invalidates old ownership,
  honors retry budget and cancellation; four simultaneous reapers recover once.
- Concurrent finalizers finish one attempt once; expired completion loses to recovery.
- Deadline-capped renewal, delayed eligibility, priority/queue isolation, retry jitter
  bounds, permanent/dead-letter outcomes and worker registration expiry are checked.
- Schema constraints reject invalid lease/terminal/counter/priority shapes, duplicate
  unfinished attempts, duplicate occurrences and cross-tenant occurrence references.

Reproduction: use README.md and docs/database/TRANSACTIONS.md. Set
CHRONOS_TEST_DATABASE_URL to a disposable DB and CHRONOS_REQUIRE_DB=1, then:

```sh
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv run --no-sync pytest -q
```

Each queue test migrates and removes a generated isolated schema. CI fails closed
if the DB URL is missing. An earlier local run hit Windows' reserved port range;
the disposable DB uses port 15432. Using explicit IPv4 eliminated localhost's slow
connection fallback. Failed initial checks were corrected and the complete suite rerun.

Not tested here: real handler subprocess termination, DB failover/network partitions,
cloud recovery, throughput/latency benchmarks, authenticated HTTP APIs or schedule
generation. Tests establish the exercised transactional invariants, not exactly-once
external effects, universal deadlock freedom or comprehensive production resilience.
