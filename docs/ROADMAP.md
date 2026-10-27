# Implementation roadmap

Each phase reports actual files, checks, decisions, limitations and next steps in
PROJECT_STATE.md. No phase is complete solely because code was generated.

| Phase | Scope | Exit evidence |
| --- | --- | --- |
| 1 — Architecture/foundation | Research, state machine, ownership/failure design, ADRs, threat model, monorepo, tooling, CI, public repository | Fresh clone installs locked dependencies; liveness tests, types/lint, frontend production build, proposal DDL and container builds pass in GitHub Actions |
| 2 — Persistence/API | Alembic initial migration, tenant/key auth, quotas, typed submit/list/detail/cancel/replay, idempotency, claim/renew/finalize SQL | Real PostgreSQL integration tests, concurrent idempotency and claim races, negative auth matrix, migration up/down/fresh DB, OpenAPI contract |
| 3 — Worker/recovery | Handler registry, bounded async execution, heartbeat, fencing, retries/dead letters, reaper and SIGTERM | Multi-process crash/pause tests, stale completion/renewal rejection, DB restart/ambiguous commit tests, cancellation races |
| 4 — Recurrence | Separate scheduler, UTC interval generation, revisions, skip/catch_up policy | Competing replicas never duplicate occurrence identity; crash/edit/disable/misfire tests |
| 5 — Operations frontend | Real API client/types, authenticated dashboard, job forms/history/tables, themes/accessibility, protected metrics and structured logs | End-to-end real job workflow, error/loading/empty states, auth and accessibility checks; screenshots of actual functionality |
| 6 — Performance/hardening | Locust workloads, query plans/index tuning, fairness/backpressure, fault injection and vulnerability remediation | Reproducible measured results with hardware/configuration and confidence limits; performance regression protocol |
| 7 — Deployment/release | Approved Railway resources, managed PostgreSQL, independent API/workers/scheduler, HTTPS frontend, monitoring/runbooks | Tested public URL and core workflow, passing CI, backup/restore and rollback rehearsal, limitations published |

No cloud spend is authorized by repository setup. Ask before provisioning paid
services or changing sensitive cloud settings. LISTEN/NOTIFY, cron/DST, RLS,
partitioning and horizontal multi-region operation are explicitly deferred.
