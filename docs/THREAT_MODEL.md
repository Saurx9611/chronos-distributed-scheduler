# Threat model — Phase 1 baseline

Phase 3 update: worker execution now exists behind trusted database access, without
public job HTTP endpoints. Only four typed built-in data workloads are allowed;
no paths, URLs, imports, shell commands or submitted code are executed. Handlers
run as fixed child modules with bounded JSON IPC and without DB credentials.
Compose workers run nonroot with dropped capabilities, read-only filesystems and
resource limits. Parent-death and lease guards limit orphan work; they do not provide
an untrusted-code sandbox or exactly-once remote effects. Result JSON can contain
job data and must remain tenant-authorized before a future API exposes it. Runtime
DB grants and durable audit protection remain production release gates.

Assets: API keys, job payloads, job/schedule integrity, lease ownership, tenant data,
database credentials, audit history and execution capacity. Trust boundaries: browser
to API, API to PostgreSQL, workers to handler effects, CI to source/artifacts, and cloud
platform secrets to runtime. Workers/registered handlers are trusted deployment code.

| Threat | Planned controls | Verification / residual risk |
| --- | --- | --- |
| Cross-tenant reads/writes (IDOR) | Tenant-scoped keys and SQL predicates, composite foreign keys, role checks; 404 for other-tenant IDs | API negative auth matrix in Phase 2; no RLS defense initially |
| Arbitrary execution / injection | Registered handler allowlist, Pydantic bounds, parameterized SQL, no shell/user Python | Reject unknown handler, malformed payload and SQL injection fixtures |
| Credential theft | Hash high-entropy keys with server-side pepper, rotate/revoke, platform secrets, TLS, no browser localStorage keys | Redaction and revocation tests; worker compromise still grants trusted DB access |
| Replay / duplicate effects | Tenant idempotency keys plus request hash; token/generation fencing; downstream business idempotency | Repeat requests and stale-worker tests; irreversible remote effects may duplicate |
| Resource exhaustion | 64 KiB payloads, 100-row pages, bounded runtime/concurrency/pools, rate limits and queue quotas | Admission and oversized-input tests, benchmark saturation; noisy tenants remain a capacity concern |
| SSRF / data exfiltration | Initial handlers make no arbitrary user-selected URL calls; later egress allowlist and private-address rejection | Network tests before any webhook handler ships |
| CSRF / XSS | SameSite/secure HTTP-only cookies, CSRF checks, escaped React content, no raw HTML rendering, restrictive headers | Browser security tests in Phase 5; no auth UI in Phase 1 |
| Log/metric disclosure | Exclude payloads, keys, lease tokens and DSNs; internal authenticated metrics, bounded labels | Seed secret markers and assert absence from outputs |
| Supply-chain / CI compromise | Lockfiles, read-only CI token, npm audit and pip-audit, review dependencies and workflow updates, nonroot images | Advisory scans in foundation CI; pin action/image digests before production |
| Destructive schema or retention error | Migration role separated from runtime, backups and restore rehearsal, bounded retention transactions | Migration and recovery drills in Phase 7 |

Phase 1 exposes only process liveness and a static foundation page. Job/admin/metrics
routes do not exist; there is no production security claim. Local PostgreSQL binds
only loopback and requires an explicit password; .env is ignored. CI database passwords
are disposable fixture values, not production secrets. Production must disable public
DB access, use TLS with certificate verification, and allocate least-privilege runtime
roles. Authentication, rate limits, audit permissions, secure headers and readiness
are acceptance gates before exposing any job endpoint.

Security response: revoke affected keys, stop claiming, rotate database credentials,
preserve redacted audit evidence, assess possibly repeated effects, and restore only
with an explicit reconciliation plan. Restoring a queue may repeat external actions.
