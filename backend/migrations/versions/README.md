# Migration revisions

Revision 0001 installs the transactional queue schema from its versioned companion
SQL file. CI tests upgrade, downgrade and re-upgrade on isolated real PostgreSQL
schemas. Use `uv run alembic -c backend/alembic.ini upgrade head`; set
CHRONOS_DATABASE_URL to a SQLAlchemy `postgresql+psycopg://...` URL first.
Do not edit a released revision after deployment; add a new revision instead.
Downgrade to base drops queue data and is for disposable test environments only.
