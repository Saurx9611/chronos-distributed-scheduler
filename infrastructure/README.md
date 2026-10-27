# Infrastructure boundary

Compose provides local PostgreSQL 18 and optional foundation API/frontend builds.
`docker compose --profile foundation up --build` starts those three services.
The workers profile adds one Alembic migrator and worker-1/worker-2/worker-3,
with explicit migration completion dependencies, bounded resources and SIGTERM
draining. See docs/WORKER_RUNTIME.md. The recurring scheduler is not implemented yet.

Planned Railway topology: managed PostgreSQL; independent API, frontend, worker
and scheduler services; one migration job; protected metrics collection. No Railway
resource, deployment URL, production credential or billable service exists yet.
Production configuration, image digest pinning, backup/restore runbook and rollback
procedures are Phase 7 deliverables requiring operational verification.
