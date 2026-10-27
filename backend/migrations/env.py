"""Migration environment supporting CLI and caller-owned test connections."""

import os

from alembic import context
from sqlalchemy import Connection, create_engine, pool
from sqlalchemy.engine import URL

database_url = os.environ.get("CHRONOS_DATABASE_URL") or URL.create(
    "postgresql+psycopg",
    username=os.environ.get("PGUSER", "chronos"),
    password=os.environ.get("PGPASSWORD"),
    host=os.environ.get("PGHOST", "localhost"),
    port=int(os.environ.get("PGPORT", "5432")),
    database=os.environ.get("PGDATABASE", "chronos"),
)


def migrate(connection: Connection) -> None:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=database_url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied = context.config.attributes.get("connection")
    if supplied is not None:
        migrate(supplied)
    else:
        engine = create_engine(database_url, poolclass=pool.NullPool)
        with engine.connect() as connection:
            migrate(connection)
        engine.dispose()
