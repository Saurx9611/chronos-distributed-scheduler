"""Migration environment supporting CLI and caller-owned test connections."""

import os

from alembic import context
from sqlalchemy import Connection, create_engine, pool

database_url = os.environ.get("CHRONOS_DATABASE_URL", "")


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
