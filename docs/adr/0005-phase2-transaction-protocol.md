# ADR-0005: Phase 2 queue protocol and priority policy

Status: implemented, 2026-10-27. Supersedes Phase 1 sketches where noted.

The requested Phase 2 scope is the transactional PostgreSQL queue, with no handler
runner or job HTTP endpoints. API authentication/quotas remain a separate gate.
Revision 0001 is the first actual schema migration; historical proposal.sql is not
an upgrade source. Attempt storage is named job_attempts, and worker registrations
carry a separate heartbeat token/expiry from each job's ownership token/lease.

Claims now order by priority descending, available_at, then UUID. Priority range
is -100..100. This satisfies explicit priority support at the cost of possible
low-priority starvation; it does not promise fairness. Original scheduled_at stays
fixed while retries change available_at. Each attempt has a DB-time execution
deadline, and renewal cannot extend beyond it.

Use READ COMMITTED and two-command lock-then-time-check for owner mutations. An
UPDATE predicate can otherwise evaluate a clock before a conflicting row lock is
acquired. Final writes repeat identity, generation, token, state and expiry predicates.
Claim/attempt/job-event/audit writes and finalization writes commit atomically.

Reserve tenant-scoped submission keys before creating their jobs; a deferred FK
validates the reserved UUID at commit. ON CONFLICT serializes identical keys without
an application advisory-lock namespace. Failed transactions leave no key reserved.
No key expiry or purge exists yet. Changes to canonical requests conflict.

Use bounded pool/batches and local transaction timeouts. Do not hide ambiguous
commits behind unconditional retries. Repository DB exceptions propagate; Phase 3
must add bounded transaction retries and ownership reconciliation before execution.
No cloud deployment or production security guarantees follow from this phase.

Validation: real PostgreSQL 18 migration round-trips, simultaneous independent
connections, stale generation/token/worker rejection, expiry after a lock wait,
rollback injection, duplicate finalization and competing recovery. See
docs/database/TRANSACTIONS.md and docs/PHASE2_VERIFICATION.md.
