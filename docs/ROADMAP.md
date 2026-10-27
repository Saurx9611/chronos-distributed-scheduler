# Implementation roadmap

Each phase reports actual files, checks, decisions, limitations and next steps in
PROJECT_STATE.md. No phase is complete solely because code was generated.

| Phase | Scope | Exit evidence |
| --- | --- | --- |
| 1 — Architecture/foundation | Research, state machine, ownership/failure design, ADRs, threat model, monorepo, tooling, CI, public repository | Fresh clone installs locked dependencies; liveness tests, types/lint, frontend production build, proposal DDL and container builds pass in GitHub Actions |
| 2 — Transactional queue | Alembic initial migration, job/attempt/schedule/worker/audit storage, idempotency, atomic claims and fenced state transitions | Real PostgreSQL tests, simultaneous consumers, stale-owner/rollback/recovery assertions, migration up/down/up |
| API gate — before public job operations/dashboard | Phase 4 implements auth/rate-limited retry/replay and worker health; general submit/list/detail/cancel and pagination remain | Negative auth matrix, quotas/input limits, OpenAPI and tenant-scoping contract tests |
| 3 — Worker/recovery | Handler registry, bounded async execution, heartbeat, fencing, retries/dead letters, reaper and SIGTERM | Multi-process crash/pause tests, stale completion/renewal rejection, DB restart/ambiguous commit tests, cancellation races |
| 4 — Fault tolerance/recovery (user-selected scope) | Lease/deadline reclamation, fencing, retry policy, reason-coded audit, heartbeat monitoring, explicit retry/replay APIs and independent effect tests | Deterministic real-process crash/pause/commit-boundary tests, failure matrix, stale rejection, duplicate-risk evidence and all correctness gates passing |
| Future — Recurrence | Separate scheduler, UTC interval generation, revisions, skip/catch_up policy | Competing replicas never duplicate occurrence identity; crash/edit/disable/misfire tests |
| Future — Operations frontend | Real API client/types, authenticated dashboard, job forms/history/tables, themes/accessibility, protected metrics and structured logs | End-to-end real job workflow, error/loading/empty states, auth and accessibility checks; screenshots of actual functionality |
| Future — Performance/hardening | Locust workloads, query plans/index tuning, fairness/backpressure, fault injection and vulnerability remediation | Reproducible measured results with hardware/configuration and confidence limits; performance regression protocol |
| Future — Deployment/release | Approved Railway resources, managed PostgreSQL, independent API/workers/scheduler, HTTPS frontend, monitoring/runbooks | Tested public URL and core workflow, passing CI, backup/restore and rollback rehearsal, limitations published |

No cloud spend is authorized by repository setup. Ask before provisioning paid
services or changing sensitive cloud settings. LISTEN/NOTIFY, cron/DST, RLS,
partitioning and horizontal multi-region operation are explicitly deferred.
