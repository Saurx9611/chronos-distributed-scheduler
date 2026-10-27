# Migration revisions

No schema migration has been released in Phase 1. The proposal is validated by CI
without becoming a deployed contract. Phase 2 adds its first revision and upgrade,
downgrade, and fresh-database tests. Use `uv run alembic -c backend/alembic.ini ...`;
set CHRONOS_DATABASE_URL to a SQLAlchemy `postgresql+psycopg://...` URL first.
