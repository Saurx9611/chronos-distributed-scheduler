# Infrastructure boundary

Compose provides local PostgreSQL 18 and optional foundation API/frontend builds.
`docker compose --profile foundation up --build` starts all three. No worker or
scheduler service is declared until an executable entry point exists.

Planned Railway topology: managed PostgreSQL; independent API, frontend, worker
and scheduler services; one migration job; protected metrics collection. No Railway
resource, deployment URL, production credential or billable service exists yet.
Production configuration, image digest pinning, backup/restore runbook and rollback
procedures are Phase 7 deliverables requiring operational verification.
