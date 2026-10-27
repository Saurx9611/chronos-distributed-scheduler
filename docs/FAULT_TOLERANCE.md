# Phase 4: distributed fault tolerance and recovery

This milestone follows the user's Phase 4 fault-tolerance scope. Recurring generation
remains deferred. Queue authority stays in PostgreSQL; the separate HTTP/SQLite
effect service is an opt-in demonstration destination, never a queue or lease authority.

## Ownership, expiration and runtime

Claims still use a short READ COMMITTED `FOR UPDATE SKIP LOCKED` transaction.
Each committed attempt has a fresh UUID token and a strictly increasing generation
**within its job identity**. Row locks end before any handler effect. Renewal,
success and failure lock the job first, then compare job ID, worker ID, token,
generation, running state, DB-time lease expiry and execution deadline. Old workers
cannot complete a replacement attempt, including after a process pause.

Reclamation selects bounded batches when **either** lease_expires_at or
execution_deadline has passed, using PostgreSQL clock_timestamp(). After locking,
it checks again, closes the prior attempt, clears ownership and chooses retry_wait,
dead_letter or cancelled. Attempt/event/audit writes commit together. Competing
reapers skip each other's locked rows; competing claimers cannot obtain the same
current attempt. Partial indexes cover lease and execution-deadline recovery paths.

Worker heartbeat monitoring marks expired active/draining registrations lost and
records heartbeat_expired. Heartbeat staleness is diagnostic: it never revokes an
otherwise valid job lease. This avoids stealing work merely because a control-plane
heartbeat was delayed while lease renewal remained healthy. An expired/lost identity
cannot be revived; the process registers a new UUID. Existing valid job leases keep
their own identity and fencing requirements.

The runtime stops children before abandonment or cancellation acknowledgment.
DB disconnects and uncertain commits never authorize blind effect replay. A committed
claim whose response is lost may consume an attempt without dispatch; recovery handles
it after expiry. A lost command response is resolved by the same idempotency key.
Process-level correctness evidence does not certify every network/primary-failover
or database-clock-step scenario.

## Retries and terminal records

JobSpec and the explicit command API accept max_attempts 1..10 (default 5),
retry_base_seconds >0..60 (default 1), and retry_cap_seconds >=base..3600
(default 300). The exact full-jitter policy is:

```text
delay = uniform(0, min(retry_cap_seconds,
                       retry_base_seconds * 2 ** (attempts - 1)))
available_at = PostgreSQL clock_timestamp() + delay
```

Attempts count claims, including workers killed before execution. Permanent errors
dead-letter immediately; retryable failures and reclamations stop at the configured
budget. A runtime timeout may be recorded as execution_timeout by the supervisor
or runtime_exceeded by reclamation, depending on which fenced transition wins.
Both paths stop/recover bounded work and preserve the actual winning reason.

Terminal records never reopen. Explicit **retry** accepts only dead_letter sources,
creates a new job with a fresh budget and preserves effect_key. Explicit **replay**
accepts any terminal source, creates a new job and intentionally chooses a new
effect_key. Both retain replay_of lineage. The old job's attempts and generation
remain untouched. Generation fencing must be scoped to job identity; it is not a
global business-key sequence across operator-created jobs.

Operator retry preserves the business identity so a destination can deduplicate
previously committed work. Replay expresses new execution intent and may repeat
external work. Different command idempotency keys express different commands;
identical tenant/key/action/source/effective-policy requests return the same new job.
Reusing a key with different input conflicts. Reservation, new job, lineage and
source/child audit events commit atomically, with no orphan jobs on failure.

## Auditable transitions

Migration 0003 adds from_state, to_state and reason to job events, recovery_reason
to attempts, and reason/presented_generation to audit events. Legacy history is
explicitly marked legacy_event rather than inventing missing causal details.
Reclamation records lease_expired, runtime_exceeded or cancellation_expired;
dead-letter outcome and attempt budget remain independently visible. Rejected
ownership is recorded separately after its failed transaction rolls back, without
changing current state or recording the submitted ownership token.

History triggers reject UPDATE, DELETE and TRUNCATE. A database owner can still
alter/disable triggers; this is protection against ordinary accidental mutation,
not tamper-proof evidence against a privileged database administrator. Runtime grants,
archival/retention and production audit custody remain deployment gates. Error codes
and reason labels are bounded identifiers, not exception messages. SQLAlchemy hides
bound SQL parameters, and worker logs exclude payloads, results, credentials and tokens.

## Authenticated recovery APIs

Implemented routes:

| Route | Required role | Behavior |
| --- | --- | --- |
| POST /v1/jobs/{id}/retry | operator/admin | New job from a dead-letter source, same effect identity |
| POST /v1/jobs/{id}/replay | operator/admin | New job from a terminal source, new effect identity |
| GET /v1/workers | admin | At most 100 newest infrastructure registrations, DB-time is_alive; no tokens |

Both POSTs require Idempotency-Key (1..128 bytes) and a JSON body ({} uses the source
policy). Optional fields are max_attempts, retry_base_seconds, retry_cap_seconds.
Responses contain job_id, source_job_id and action, with status 201; identical
idempotent requests preserve that response identity even after the new job has run.
No arbitrary job payload/type/tenant overrides are accepted by these command routes.

Credentials are high-entropy random bearer values stored only as HMAC-SHA256 hashes,
scoped to one tenant and viewer/operator/admin role, with expiry and revocation.
Set a stable CHRONOS_API_AUTH_PEPPER of at least 32 characters in secret storage.
Rotation invalidates existing hashes and requires credential reissuance. Authentication
checks expiry/revocation after acquiring the credential row lock. The DB-backed fixed
minute rate limiter defaults to 60 authenticated requests per key, configurable up
to 1000 with CHRONOS_API_RATE_LIMIT. Rate-window/key/history retention needs an
operational purge policy; no unverified automatic retention job is claimed.

Errors use an error code, generic message and X-Request-ID correlation value:
401 missing/invalid/expired/revoked credentials; 403 role; 404 absent or other-tenant
source; 409 state/idempotency conflict; 422 input/policy; 429 with Retry-After;
503 unavailable DB/unconfigured API. No raw validation values or SQL errors are returned.
The shared worker-health view is an infrastructure-admin surface, not a tenant job view.

### Local operator setup

Apply Alembic head 0003. Set CHRONOS_DATABASE_URL (postgresql+psycopg://...) and
CHRONOS_API_AUTH_PEPPER privately in the API environment. Create a credential for
an existing tenant using trusted local DB access:

```sh
uv run --no-sync python -m chronos.api.keys --tenant YOUR_TENANT_UUID --role operator --output .tools/operator.key
uv run --no-sync uvicorn chronos.api.main:app --host 127.0.0.1 --port 8000
```

The bootstrap command exclusively creates the requested file and omits the token
from stdout. Keep it in ignored private storage and apply appropriate OS permissions;
production credentials belong in platform secret storage and require HTTPS.
Send Authorization: Bearer using that privately read value, Idempotency-Key, and
the JSON command body. Submission/list/detail/history endpoints are still a separate
API milestone; these recovery commands do not claim to implement them.

## Independent side effects and deterministic process faults

The faults Compose profile enables an authenticated, separate effect-sink process.
It commits to its own SQLite WAL volume before responding, independently of Chronos
acknowledgment. Normal deliveries atomically compare the business key/request digest
and reuse a recorded receipt. The unsafe endpoint is disabled unless explicitly
enabled for tests and demonstrates why a non-idempotent destination may duplicate work.
Neither the queue nor its workers can atomically commit an arbitrary remote effect
and a PostgreSQL acknowledgment.

The demo_effect handler is opt-in and absent from the default four-handler allowlist.
It accepts bounded data only. The destination URL/token come from trusted operator
configuration, never job payloads; redirects and environment HTTP proxies are disabled.
No shell commands, imports, paths or user code are accepted. Production deployments
must leave CHRONOS_ENABLE_EFFECT_DEMO, CHRONOS_DEMO_ALLOW_UNSAFE and
CHRONOS_FAULT_INJECTION disabled unless deliberately running this private demonstration.

Authenticated durable gates position only the selected test worker at after_claim,
during_execute (input preparation complete, external commit not started), or before_ack
(external commit already complete). Tests wait for those durable observations before
SIGKILL or whole-container pause/unpause. No sleep is used to guess a crash boundary.
The fixed stale-report actor is disabled without the same explicit fault flag and
accepts only a typed ownership report over stdin, never executable code.

The failure matrix and recorded observations are in FAILURE_MODE_MATRIX.md. Reproduce:

```sh
# Real disposable PostgreSQL URL required; fail rather than skip missing DB tests.
uv run --no-sync pytest -q -m "not compose"
# Set CHRONOS_RUN_COMPOSE_TESTS=1 for real Docker/Linux faults.
docker compose --profile workers build worker-1
uv run --no-sync pytest -q -m compose tests/process --junitxml=reports/phase4-processes.xml
```

Process fixtures create UUID-named projects, independent destinations and random
free loopback ports. They remove only their own resources. Reports contain redacted
lifecycle JSON and measured effect/attempt counts. No production deployment or
performance benchmark is implied by these correctness checks.
