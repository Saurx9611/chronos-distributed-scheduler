"""Every queue test migrates its own real PostgreSQL schema, then removes it."""

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from chronos.engine.repository import QueueRepository, create_queue_engine
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url


def migrate(engine: Engine, target: str, *, downgrade: bool = False) -> None:
    config = Config("backend/alembic.ini")
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        if downgrade:
            command.downgrade(config, target)
        else:
            command.upgrade(config, target)


@pytest.fixture
def db() -> Iterator[Engine]:
    dsn = os.environ.get("CHRONOS_TEST_DATABASE_URL")
    if not dsn:
        if os.environ.get("CI") or os.environ.get("CHRONOS_REQUIRE_DB"):
            pytest.fail("A real PostgreSQL URL is required; refusing to skip database gates")
        pytest.skip("Set CHRONOS_TEST_DATABASE_URL to a disposable PostgreSQL database")
    url = make_url(dsn).set(drivername="postgresql+psycopg")
    schema = "chronos_test_" + uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5})
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_queue_engine(
        url.update_query_dict({"options": f"-csearch_path={schema}"}), pool_size=12
    )
    try:
        migrate(engine, "head")
        yield engine
    finally:
        engine.dispose()
        # Identifier is generated above from a constant prefix and UUID hex, never user input.
        with admin.connect() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


@pytest.fixture
def repo(db: Engine) -> QueueRepository:
    return QueueRepository(db)


@pytest.fixture
def tenant(repo: QueueRepository) -> UUID:
    return repo.create_tenant("integration")


def force_expired(db: Engine, job_id: UUID) -> None:
    with db.begin() as conn:
        conn.execute(
            text("""
            UPDATE jobs SET lease_expires_at=clock_timestamp()-interval '1 second'
            WHERE id=:id
        """),
            {"id": job_id},
        )


def make_due(db: Engine, job_id: UUID) -> None:
    with db.begin() as conn:
        conn.execute(
            text("UPDATE jobs SET available_at=clock_timestamp() WHERE id=:id"), {"id": job_id}
        )
