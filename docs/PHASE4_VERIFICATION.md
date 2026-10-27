# Phase 4 verification

This phase implements the user's distributed fault-tolerance/recovery scope.
Recurring generation was not implemented. PostgreSQL 18.6 remains authoritative;
an independent private HTTP/SQLite destination supplies real side-effect evidence.

## Checks and observed evidence

- Complete local process regression: **14 passed**, zero skipped, in 358.80 seconds.
  This includes eight new precise fault/side-effect/API scenarios and the six prior
  distribution/signal/outage scenarios rerun against migration 0003.
- Database/API/handler tests: **72 passed**, zero skipped, against real PostgreSQL, including migration
  up/down/up, preservation of legacy jobs/worker identities, exact jitter bounds,
  max-attempt exhaustion, append-only history, credential roles/expiry/revocation,
  cross-tenant isolation, rate limits and command response-loss idempotency.
- Ruff lint/format, strict mypy, locked dependency check and Docker image build passed.
- Combined correctness total: **86 passing cases** across the two explicit gates.
  Hosted CI/commit reference is recorded after publication; PROJECT_STATE.md is the
  authoritative current status.

The matrix in FAILURE_MODE_MATRIX.md maps every requested failure trigger to a test
and observed result. Fault boundaries use durable HTTP gates, not sleep-based guesses.
The old-owner success probe runs in a separate actual worker-container process and
reports ownership_lost rather than changing the replacement's state/result.

The central observed ambiguity case: a worker commits its external effect then dies
before acknowledging. Both destination modes received two requests over generations
1 and 2. The idempotent destination stored one effect; the explicitly unsafe endpoint
stored two. The latter is an expected passing risk-demonstration test. A terminal
dead-letter source retried through the authenticated API likewise preserved its old
effect identity, while replay intentionally created a new one.

Curated observed facts are committed in examples/phase4-failure-facts.json. Raw
redacted JSONL logs/facts and JUnit are generated under reports/worker-processes and
uploaded as the worker-process-evidence CI artifact. Tokens, credentials and payloads
are excluded. Test durations/counts are correctness evidence, not performance benchmarks.

## Reproduce

Use FAULT_TOLERANCE.md for API credentials, semantics and runtime details. Set
CHRONOS_TEST_DATABASE_URL to a disposable DB and CHRONOS_REQUIRE_DB=1, then:

```sh
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv run --no-sync pytest -q -m "not compose"
```

For the separate Linux process gate, enable CHRONOS_RUN_COMPOSE_TESTS=1, build the
worker image and run `uv run --no-sync pytest -q -m compose tests/process`.
Tests create/remove only their own UUID-named projects and volumes. Fault flags and
unsafe destination mode are off by default. Do not enable them for public operation.

## Limits

No production cloud deployment, throughput benchmark, multi-region/primary failover
or exhaustive network/clock fault certification exists. Ordinary-SQL history protection
does not defend against a database owner. General submission/read/history APIs and
recurring generation remain separate milestones. HTTP API credential files are local
operator tooling; production needs platform secret storage, HTTPS and controlled grants.
