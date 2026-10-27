# Migration revisions

Phase 3 adds revision 0002 for worker lifecycle state, execution dispatch timestamp
and bounded JSON results. Tests cover upgrading existing Phase 2 data and the full
up/down/up path. Downgrading 0002 removes result/start/lifecycle metadata; it does not
undo external effects. Do not use destructive downgrades as a production rollback plan.

Revision 0001 installs the transactional queue schema from its versioned companion
SQL file. CI tests upgrade, downgrade and re-upgrade on isolated real PostgreSQL
schemas. Use `uv run alembic -c backend/alembic.ini upgrade head`; set
CHRONOS_DATABASE_URL to a SQLAlchemy `postgresql+psycopg://...` URL first.
Do not edit a released revision after deployment; add a new revision instead.
Downgrade to base drops queue data and is for disposable test environments only.
