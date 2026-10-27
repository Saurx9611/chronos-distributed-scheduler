# ADR-0007: Recovery identity, auditable causes and independent effect tests

Status: implemented in the user's Phase 4 fault-tolerance milestone, 2026-10-27.

Keep PostgreSQL as the queue authority. Expiry reclamation checks both the lease
and execution deadline, while worker heartbeat loss is a separate diagnostic state.
Do not steal a valid lease simply because a heartbeat was delayed. Preserve fresh
ownership UUIDs and monotonically increasing generations within each job, and
compare all ownership fields under row lock for every terminal mutation.

Expose authenticated, tenant-scoped operator retry/replay commands. Retry creates
a new job/budget but preserves the logical effect key; replay creates a new effect
identity to express new work. Neither resurrects a terminal generation. A separate
command idempotency ledger atomically binds tenant/key/action/source/effective-policy
to the resulting job, so uncertain responses do not create duplicate command jobs.

Persist from/to states, causal reason labels and stale presented generations.
Reject ordinary UPDATE/DELETE/TRUNCATE of event/audit history. This protects against
accidental mutation, not a privileged owner who can disable triggers. Authentication
uses random scoped tokens stored as HMAC hashes, DB expiry/revocation checks and
DB-backed fixed-window rate limits. Public deployment still needs TLS, ingress
controls, least-privilege DB grants, retention and broader API functionality.

For fault evidence use a private HTTP destination with its own durable SQLite
transaction, rather than writing fake side-effect counters in the claim transaction.
Its normal endpoint deduplicates effect key/request digest; a separately enabled
unsafe endpoint demonstrates duplicate risk. It is test/demo infrastructure only,
not an alternate queue engine. Standard-library SQLite avoids another external
infrastructure dependency and survives worker/queue DB crashes independently.

Durable gates position a selected real process at claim/prepare/ack boundaries.
They are disabled without explicit operator flags and do not accept executable code.
SIGKILL, Docker pause/unpause and PostgreSQL stop/start then exercise actual recovery.
After a committed effect and lost acknowledgment, two requests yielding one effect
at an idempotent destination and two effects at a non-idempotent destination are
both expected observations. Delivery remains at-least-once.

Validation: failure matrix, real-process destination counts, rejected old-generation
success probe, command/credential/tenant/rate tests, rollback and commit-then-response-
loss injection with real PostgreSQL. No universal fault-coverage or deadlock-free
claim follows from the tested scenarios.
