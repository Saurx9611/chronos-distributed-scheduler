"""Migration environment; Phase 2 adds the first reviewed schema revision."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

database_url = os.environ["CHRONOS_DATABASE_URL"]

if context.is_offline_mode():
    context.configure(url=database_url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
