# Phase 3 verification evidence

Date: 2026-10-27. Local PostgreSQL 18.6 in Linux Docker containers; host Python 3.14.7
and worker-image Python 3.14.10. No new runtime packages were required. Dependencies
remain locked in uv.lock. The final hosted run reference is recorded after publication.

## Executed checks

- Database/runtime/handler tests: passed, including child execution/results,
  cancellation, lease-uncertainty abandonment, unusable-window preflight, unknown
  handlers, protocol validation and redaction of unexpected handler exceptions.
- Full real Compose process suite: **6 passed** in 226.24 seconds in the initial
  local run. All scenarios use disposable UUID-named projects, real process signals,
  real PostgreSQL and independent worker containers. Subsequent targeted signal
  assertions also verify exit codes 0 for graceful drains and 137 for SIGKILL.
  The graceful-drain test uses an eight-second budget; the forced-drain case uses
  three seconds. An initial three-second successful-drain assertion was too tight
  when child startup plus workload exceeded that budget, and was corrected and rerun.
- Ruff lint/format, strict mypy and Docker worker image build passed.
- Migration tests exercise up/down/up through 0002; results remain fenced and old
  completed attempts cannot be overwritten.

The dedicated worker-processes CI job rebuilds the final image, reruns all six
scenarios, and uploads JSONL logs, facts and JUnit output. Local generated evidence
is under reports/worker-processes (ignored by Git). These are correctness tests;
elapsed test duration is not a throughput benchmark.

## Actual distribution and fault outcomes

| Scenario | Observed result |
| --- | --- |
| Three workers, concurrency 2 each, 12 mixed jobs | All 12 succeeded on generation 1 across three distinct worker identities; each worker reached two active jobs. 56 DB snapshots detected no conflicting active attempt ownership. Results verified PNG 1x1 dimensions, CSV total 3.74, 1,000-row report and simulated receipt. |
| SIGTERM | Active job drained successfully; a job submitted after shutdown began stayed queued; worker registration became stopped. |
| SIGKILL | Generation 1 recorded lease_expired; a different process completed generation 2 with a different token. |
| Grace budget exhausted | Child killed/reaped; lease left for recovery; exhausted one-attempt job became dead_letter with lease_expired outcome. |
| Retry / timeout / allowlist | Simulated transient notification succeeded on attempt 2; deadline job dead-lettered; disallowed handler never dispatched a child. |
| PostgreSQL stopped during execution | Child absent during outage; no success from uncertain generation 1; after database restart recovery recorded lease_expired, then generation 2 succeeded. |

The ownership assertions compare unfinished attempt owner/token/generation against
the current jobs row and enforce concurrency bounds while work runs. They establish
the exercised recorded-ownership invariants, not absence of all possible duplicate
external effects after pauses, clock steps or ambiguous outcomes.

## Recorded lifecycle excerpts

The following JSON records are copied from a real local run; timestamps remain UTC.
Job IDs are actual test IDs. Payloads/results, ownership tokens, credentials and
exception text are deliberately absent. Full generated logs are local/CI artifacts.

```json
{"timestamp": "2026-10-27T09:36:15.378195+00:00", "event": "job_claimed", "job_id": "e0f85c3a-3999-485f-9e8f-60aa5c7f45c7", "worker_id": "3d92c2fb-6688-4224-8b5a-3580c583d8ae", "generation": 1, "job_type": "notification"}
{"timestamp": "2026-10-27T09:36:15.490050+00:00", "event": "job_started", "job_id": "e0f85c3a-3999-485f-9e8f-60aa5c7f45c7", "worker_id": "3d92c2fb-6688-4224-8b5a-3580c583d8ae", "generation": 1, "job_type": "notification"}
{"timestamp": "2026-10-27T09:36:28.775214+00:00", "event": "job_claimed", "job_id": "e0f85c3a-3999-485f-9e8f-60aa5c7f45c7", "worker_id": "da3e36b0-3a83-4b1e-bf9c-781863eef0f2", "generation": 2, "job_type": "notification"}
{"timestamp": "2026-10-27T09:36:28.835003+00:00", "event": "job_started", "job_id": "e0f85c3a-3999-485f-9e8f-60aa5c7f45c7", "worker_id": "da3e36b0-3a83-4b1e-bf9c-781863eef0f2", "generation": 2, "job_type": "notification"}
{"timestamp": "2026-10-27T09:36:34.140993+00:00", "event": "job_succeeded", "job_id": "e0f85c3a-3999-485f-9e8f-60aa5c7f45c7", "worker_id": "da3e36b0-3a83-4b1e-bf9c-781863eef0f2", "generation": 2, "job_type": "notification"}
```

The database attempt history for that killed job was `(generation=1, lease_expired)`
then `(generation=2, succeeded)`, with different owners and tokens. A SIGKILL cannot
emit a cooperative shutdown event, so expiry/recovery and the new claim are the
evidence of recovery rather than an invented shutdown acknowledgment.

## Reproduction and limits

See WORKER_RUNTIME.md for Compose and native CLI commands. Run the database suite
with CHRONOS_REQUIRE_DB=1 and an explicit disposable DB URL. Enable
CHRONOS_RUN_COMPOSE_TESTS=1 for tests/process. The process tests allocate and remove
only their own isolated projects; they do not destroy the user's Compose volume.

No production cloud deployment, load benchmark, database failover certification,
authenticated job API or real notification service exists yet. Native non-Linux
forced-parent-death semantics are not certified by Linux container tests. Handler
isolation permits only trusted registered code; it is not an untrusted-code sandbox.
