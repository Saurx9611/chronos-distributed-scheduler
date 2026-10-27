# Proposed HTTP API (Phase 2 onward)

Only GET /health/live is implemented today. All /v1 and /metrics endpoints below
are design contracts. Publish generated OpenAPI from typed Pydantic models when built.

Bearer API keys are stored as keyed hashes, scoped to tenant and roles: viewer,
operator, admin. Job write requires operator, schedule edits require admin; all
roles are tenant scoped. Browser access uses a server-side session/BFF so durable
API keys are never stored in localStorage. Public demo uses restrictive quotas and
an allowlist of harmless handlers. CSRF protection applies to cookie-auth writes.

| Method / path | Contract |
| --- | --- |
| POST /v1/jobs | Idempotency-Key required (max 128 bytes); type, queue, payload object <= 64 KiB, optional UTC available_at, max_attempts 1..10 and execution_timeout_s 1..900. 201 for created; replay returns original job with 200; changed canonical request under same key returns 409. |
| GET /v1/jobs | Tenant-scoped filter by state/queue/type/time; stable (created_at,id) cursor, limit 1..100 default 50; sort allowlist created_at ascending/descending. |
| GET /v1/jobs/{id} | Job details, timestamps, state, generation; no lease token or secret fields. |
| GET /v1/jobs/{id}/attempts | Cursor-paginated redacted history and outcome, duration, error code. |
| POST /v1/jobs/{id}/cancel | 202 running -> cancel_requested; 200 queued/retry_wait -> cancelled or already terminal; no rollback of side effects. |
| POST /v1/jobs/{id}/replay | New idempotent submission referencing terminal parent; 409 if parent is nonterminal. |
| POST /v1/schedules | UTC fixed interval >= 60s, job template, start_at and misfire policy; Idempotency-Key required. |
| PATCH /v1/schedules/{id} | If-Match revision required; edit/enable/disable under row lock; 409 stale revision. |
| GET /v1/schedules[/{id}] | Tenant-scoped reads, cursor pagination. |
| GET /health/ready | Future: bounded DB connectivity + expected migration revision; 503 on failure. |
| GET /metrics | Protected internal Prometheus endpoint. |

Error envelope: `{ "error": { "code": "...", "message": "...", "request_id": "..." } }`.
Use 401 missing/invalid auth, 403 insufficient role, 404 missing or other-tenant
resource, 409 conflict, 422 validation, 429 quota/rate limit with Retry-After,
503 dependency unavailable. Redact internal SQL/credentials. Idempotency records
are kept while the related job exists and at least 30 days; clients must understand
that reusing a key after documented expiry can create a new job. Insert key, hash
and job in one transaction; conflicting concurrent keys serialize at unique index.

Frontend initially polls every 2s with visibility-aware backoff; SSE is deferred.
No public claim/heartbeat/completion HTTP endpoints: workers call engine functions
using trusted database access. OpenAPI schemas and generated frontend types become
versioned contract checks in Phase 2/5.
