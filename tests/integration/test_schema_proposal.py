"""Execute proposed DDL in a unique schema and roll everything back."""

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

pytestmark = pytest.mark.integration


def test_proposed_schema_constraints() -> None:
    dsn = os.environ.get("CHRONOS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set CHRONOS_TEST_DATABASE_URL to run the real PostgreSQL DDL check")
    schema = "chronos_test_" + uuid4().hex
    ddl = Path("docs/database/proposal.sql").read_text(encoding="utf-8")
    with psycopg.connect(dsn) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
            conn.execute(ddl)
            tenant = conn.execute(
                "INSERT INTO tenants(name) VALUES ('test') RETURNING id"
            ).fetchone()
            assert tenant is not None
            job = conn.execute(
                "INSERT INTO jobs(tenant_id, queue, job_type, payload) "
                "VALUES (%s, 'default', 'test', '{}') RETURNING id, state, generation",
                (tenant[0],),
            ).fetchone()
            assert job is not None and job[1:] == ("queued", 0)
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute("UPDATE jobs SET state = 'running' WHERE id = %s", (job[0],))
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute("UPDATE jobs SET state = 'succeeded' WHERE id = %s", (job[0],))
            conn.execute(
                "INSERT INTO submission_keys(tenant_id,key,request_hash,job_id) "
                "VALUES (%s, 'request-1', %s, %s)",
                (tenant[0], "a" * 64, job[0]),
            )
            with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
                conn.execute(
                    "INSERT INTO submission_keys(tenant_id,key,request_hash,job_id) "
                    "VALUES (%s, 'request-1', %s, %s)",
                    (tenant[0], "a" * 64, job[0]),
                )
        finally:
            conn.rollback()
