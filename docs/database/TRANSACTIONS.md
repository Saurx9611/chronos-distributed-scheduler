# Phase 2 transactional queue

Implemented by `backend/src/chronos/engine/repository.py`; migration `0001` is the
authoritative schema. The Phase 1 proposal remains a historical design artifact.
This phase implements persistence and trusted-process repository methods, not HTTP
job endpoints, handler execution, or the recurring scheduler daemon.

## Atomic claim: actual runtime SQL

The exact query below is `CLAIM_SQL` in `backend/src/chronos/engine/sql.py`.
All colon parameters are bound by SQLAlchemy/psycopg; none are interpolated.

```sql
WITH candidates AS MATERIALIZED (
    SELECT id FROM jobs
    WHERE queue = :queue
      AND state IN ('queued', 'retry_wait')
      AND available_at <= statement_timestamp()
      AND attempts < max_attempts
    ORDER BY priority DESC, available_at, id
    LIMIT :limit FOR UPDATE SKIP LOCKED
)
UPDATE jobs AS j
SET state = 'running', attempts = j.attempts + 1, generation = j.generation + 1,
    lease_token = gen_random_uuid(), lease_owner = :worker_id,
    lease_expires_at = LEAST(
        clock_timestamp() + make_interval(secs => :lease_seconds),
        clock_timestamp() + make_interval(secs => j.execution_timeout_s)),
    execution_deadline = clock_timestamp() + make_interval(secs => j.execution_timeout_s),
    started_at = COALESCE(j.started_at, clock_timestamp()),
    attempt_started_at = clock_timestamp(), updated_at = clock_timestamp()
FROM candidates AS c WHERE j.id = c.id
RETURNING j.*
```

Before this query, the transaction locks the worker registration FOR SHARE, then
checks its token and DB-time heartbeat expiry in a separate command. Immediately
after the query, it inserts a job_attempts row and both job/audit events for each
returned job. All writes commit together. The method returns only after commit.
The caller starts execution after receiving the returned claim, holding no DB lock.
Claims are limited to 100 rows and should match immediately available execution slots.

## Why competing claims are safe

The candidate CTE locks eligible job rows. Another consumer skips those rows while
the first transaction holds them; after commit it sees running state and cannot
select them as eligible. Each claim increments generation/attempts and generates
a distinct random token in PostgreSQL. Unique `(job_id,generation)`, unique token,
and a partial unique index allowing one unfinished attempt per job provide additional
constraints. An event/attempt failure rolls the job update back as well.

This guarantees one current recorded owner, not physical execution exclusivity:
a paused or partitioned worker may continue after its lease expires. Downstream
effects need a stable business idempotency key or a cooperating fencing receiver.
At-least-once execution, bounded by max_attempts, remains the delivery model.

The query does not guarantee FIFO: priority descending precedes available_at/UUID,
SKIP LOCKED reorders starts, and UPDATE RETURNING does not promise candidate order.
Future high-priority jobs are ineligible; continuously arriving high-priority jobs
can starve low-priority jobs. Fairness/aging is deferred and must be measured.
The partial `(queue,priority DESC,available_at,id)` index trades priority ordering
against potentially scanning many not-yet-due jobs. Query plans under realistic
delayed-job distributions are a Phase 6 optimization gate, not a throughput claim.

## Ownership and time authority

Renew, acknowledge and fail acquire the job row with SELECT FOR UPDATE, then issue
a new conditional query requiring id, worker ID, UUID token, generation, running
state, lease_expires_at > clock_timestamp(), and execution_deadline > clock_timestamp().
Checking time after acquisition prevents an earlier statement timestamp from
authorizing a lease that expired while waiting. Zero affected rows raises
OwnershipLost and rolls back. The final write repeats ownership/time predicates.

Renewal caps the new lease at the immutable per-attempt execution_deadline.
Worker registration heartbeat expiry prevents new claims, but does not revoke an
otherwise valid already-issued job lease. Expired registrations must be replaced
with a fresh worker identity; an expired heartbeat cannot resurrect the old identity.
All authoritative eligibility, lease and retry timestamps use PostgreSQL time.
UTC scheduled_at stores original intent; available_at changes with retry backoff.
Naive datetimes are rejected; aware submission times normalize to UTC.

Acknowledgment atomically marks the attempt succeeded, the job succeeded, clears
ownership, sets finished_at and appends events. Failure closes the attempt and
selects retry_wait versus dead_letter using the attempt budget. Full-jitter retry
delay is sampled in [0,min(300,2^(attempts-1))] seconds, then added to DB time.
Permanent errors dead-letter immediately. Error codes are bounded identifiers,
not raw exception messages. Claims count as attempts even if execution never starts.

Cancellation serializes with completion on the same row. Queued/retry_wait jobs
become cancelled immediately; running jobs become cancel_requested. Success,
failure and renewal then reject the old running-state predicate. The owner may
acknowledge cancellation while the lease/deadline remains valid, or recovery marks
it cancelled after expiry. Cancellation cannot undo external effects.

Recovery locks bounded expired running/cancel_requested rows with SKIP LOCKED,
rechecks token/generation/state/expiry, closes the old attempt and clears ownership.
It selects retry_wait or dead_letter by budget; cancellation is terminal. Competing
reapers cannot both close the same attempt. A new claim gets a strictly larger
generation and a new token, so the old owner cannot finalize the new attempt.

## Isolation, locks, timeouts and deadlocks

Every repository transaction explicitly uses READ COMMITTED, a 5s per-statement
timeout, 2s lock timeout, and 5s idle-in-transaction timeout. The pool has a fixed
connection budget (default 5), no overflow, a 5s pool wait and connection timeout.
Methods contain only DB operations and bounded local bookkeeping, never handlers
or external network effects. Statement timeout is not a total transaction deadline;
bounded batch sizes and monitoring transaction duration remain important.

Lock paths:

1. Submission: tenant FK key-share, unique tenant/idempotency-key reservation,
   new job, then job event/audit rows. The job FK on the reservation is deferred
   until commit; a failed submission rolls back the key. Identical concurrent
   requests read the winning committed job; changed canonical hashes conflict.
2. Claim: worker registration FOR SHARE, eligible jobs in priority/due order using
   SKIP LOCKED, then each job's attempt and events. Registration heartbeat locks
   only its worker row; it never subsequently locks jobs.
3. Renew/finalize/cancel: one job FOR UPDATE, then its current attempt if applicable,
   then event/audit inserts. Never take worker or schedule locks after a job lock.
4. Recovery: job locks in UUID order with SKIP LOCKED, then corresponding attempts
   and events. No blocking cross-job acquisition is used.
5. Future scheduler: schedule row first, then new jobs/occurrences. No generation
   loop exists yet; disabling a schedule does not revoke existing jobs.

These paths reduce lock cycles; they do not establish universal deadlock freedom.
DDL/table locks, foreign keys, direct SQL by privileged operators, and future code
can still cause waits/deadlocks. SKIP LOCKED skips row conflicts, not table locks.
PostgreSQL may abort with 40P01 (deadlock), 40001 (serialization), 55P03 (lock
timeout), or 57014 (statement timeout). SQLAlchemy rolls the transaction back.
This phase deliberately propagates DB errors: process-level bounded retry/backoff
is a Phase 3 responsibility, and must replay only a whole DB-only operation.

On connection loss during COMMIT the outcome is ambiguous. Never blindly repeat a
claim and execute guessed work: reconcile recorded ownership or allow expiry.
Submission retries use the same key. A lost completion response can be reconciled
against recorded state/attempt; repeated completion is rejected as OwnershipLost.
Tokens must remain private to trusted worker processes. Direct privileged SQL can
bypass transition protocols and mutate audit rows: least-privilege runtime grants
and production audit protection are release gates, not guarantees of this migration.

## Migration and schema boundaries

`0001_queue.py` installs versioned static DDL from its companion SQL file; downgrade
drops dependents in reverse order without CASCADE. Migration tests exercise up,
down, and up again on a fresh isolated schema. Initial-schema downgrade destroys
data and is only suitable for disposable environments; backup/restore and forward
migration are production recovery tools. Run migrations as a separate operator job.

Tables: tenants, jobs, job_attempts, schedules, schedule_occurrences,
worker_registrations, submission_keys, job_events, audit_events. Schedule storage
includes revision, template, priority, retry policy, next-fire time and a unique
occurrence identity. Recurring generation and schedule-creation idempotency methods
are deferred to Phase 4; there is no public schedule-create endpoint to deduplicate.
Job submission keys have no expiry/purge implementation yet and are retained.

CHECKs validate states, payload size/shape, counters, lease tuple and terminal
timestamps. They do not enforce every previous-to-new state transition; the trusted
repository owns that protocol. Keep direct DB access restricted. Audit history
records actor/action/entity/time without payloads, credentials or lease tokens.

## Evidence and reproduction

Tests use real PostgreSQL, migrated unique schemas and independent pooled connections.
Three barrier-synchronized rounds launch eight consumers over 80 jobs each. Every
round asserts 80 distinct jobs/tokens and 80 consistent active attempt rows.
Additional tests hold a row lock while another consumer skips it, then acquire the
claimed row NOWAIT after commit to prove execution holds no transaction lock.
Event failure injection proves claim/attempt/history rollback. Other tests cover
idempotency races, duplicate finalization, expiry after row-lock waits, stale
generation/token/worker rejection, retry/dead-letter, recovery races, cancellation,
worker heartbeats, deadline caps, migration round-trips and schema constraints.

Set CHRONOS_TEST_DATABASE_URL to a disposable PostgreSQL database, then run:

```sh
uv run --no-sync pytest -q
uv run --no-sync pytest -q tests/integration/test_competing_consumers.py
```

Set CHRONOS_REQUIRE_DB=1 to fail rather than skip if the database URL is missing;
CI always enables this gate. Fixtures create/migrate/drop only unique generated
`chronos_test_<uuid>` schemas. These tests are correctness evidence, not benchmarks
or full process-crash/network/failover certification. Those remain Phase 3/6 gates.

Official references: [PostgreSQL locking](https://www.postgresql.org/docs/18/explicit-locking.html),
[SELECT/SKIP LOCKED](https://www.postgresql.org/docs/18/sql-select.html),
[READ COMMITTED](https://www.postgresql.org/docs/18/transaction-iso.html), and
[Alembic connection sharing](https://alembic.sqlalchemy.org/en/latest/cookbook.html#connection-sharing).
