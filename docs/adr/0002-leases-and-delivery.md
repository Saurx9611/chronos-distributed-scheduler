# ADR-0002: Fenced leases and at-least-once execution

Status: accepted for Phase 1. Date: 2026-10-27.

Context: A process may pause past its lease then resume after another worker claims
the job. A process ID or UUID alone cannot order successive executions.

Decision: Each claim increments a durable bigint generation and creates a fresh
random UUID token. Every renewal/finalization requires state, job ID, generation,
token and an unexpired DB-time lease. Validate expiry after acquiring the job lock.
Finalize the attempt and audit event in the same transaction. Retry budgets count
claims, including those lost before handler entry. Replay creates another job.

Alternatives: Hold a transaction lock during execution (unbounded locks and connection
usage), trust worker liveness (pauses are indistinguishable from failure), or token-only
leases (cannot fence ordered writes at cooperating destinations).

Consequences: Old workers cannot mutate newer Chronos attempts. They may still emit
external effects; handlers must carry a stable business idempotency key, or a generation
that the destination checks. Async cancellation is cooperative. Exactly-once external
effects are impossible without downstream cooperation or a shared atomic transaction.

Validation: Race expired renewal/completion against recovery; pause/kill workers;
test ambiguous commit and concurrent cancellation. No guarantee is proven in Phase 1.
