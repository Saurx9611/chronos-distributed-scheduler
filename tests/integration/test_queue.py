from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from chronos.engine.models import (
    IdempotencyConflict,
    JobSpec,
    OwnershipLost,
    WorkerExpired,
)
from chronos.engine.repository import QueueRepository
from conftest import force_expired, make_due, migrate
from sqlalchemy import Engine, RowMapping, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


def row(db: Engine, job_id: UUID) -> RowMapping:
    with db.connect() as conn:
        return (
            conn.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id}).mappings().one()
        )


def test_migration_down_up(db: Engine) -> None:
    with db.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0003"
        indexes: set[str] = set(
            conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname=current_schema()")
            ).scalars()
        )
        assert {"jobs_claim_idx", "jobs_recovery_idx", "job_attempts_one_active_idx"} <= indexes
    migrate(db, "base", downgrade=True)
    with db.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('jobs')")).scalar_one() is None
    migrate(db, "head")
    with db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 0


def test_submit_idempotency_and_conflict(db: Engine, repo: QueueRepository, tenant: UUID) -> None:
    spec = JobSpec(job_type="test", payload={"message": "'; DROP TABLE jobs; --"})
    job = repo.submit(tenant, "same", spec)
    assert repo.submit(tenant, "same", spec) == job
    with pytest.raises(IdempotencyConflict):
        repo.submit(tenant, "same", JobSpec(job_type="different"))
    assert row(db, job)["payload"] == spec.payload
    with db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 1


def test_priority_future_and_queue_isolation(
    db: Engine, repo: QueueRepository, tenant: UUID
) -> None:
    low = repo.submit(tenant, "low", JobSpec(job_type="test", priority=-1))
    high = repo.submit(tenant, "high", JobSpec(job_type="test", priority=100))
    future = repo.submit(
        tenant,
        "future",
        JobSpec(job_type="test", priority=100, scheduled_at=datetime.now(UTC) + timedelta(days=1)),
    )
    other = repo.submit(tenant, "other", JobSpec(job_type="test", queue="other"))
    worker = repo.register_worker("worker")
    assert repo.claim(worker)[0].job_id == high
    assert repo.claim(worker)[0].job_id == low
    assert repo.claim(worker) == []
    assert row(db, future)["state"] == row(db, other)["state"] == "queued"


def test_acknowledgment_and_history_are_atomic(
    db: Engine, repo: QueueRepository, tenant: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = repo.submit(tenant, "ack", JobSpec(job_type="test"))
    claim = repo.claim(repo.register_worker("worker"))[0]
    original = repo._event

    def fail_event(*args: object) -> None:
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(repo, "_event", fail_event)
    with pytest.raises(RuntimeError, match="injected"):
        repo.acknowledge(claim)
    assert row(db, job)["state"] == "running"
    with db.connect() as conn:
        assert conn.execute(text("SELECT outcome FROM job_attempts")).scalar_one() is None
    monkeypatch.setattr(repo, "_event", original)
    repo.acknowledge(claim)
    finished = row(db, job)
    assert finished["state"] == "succeeded" and finished["lease_token"] is None
    assert finished["started_at"] <= finished["finished_at"]
    with db.connect() as conn:
        assert conn.execute(text("SELECT outcome FROM job_attempts")).scalar_one() == "succeeded"
        assert conn.execute(text("SELECT count(*) FROM job_events")).scalar_one() == 3
        assert (
            conn.execute(
                text("SELECT count(*) FROM audit_events WHERE entity_type='job'")
            ).scalar_one()
            == 3
        )
    with pytest.raises(OwnershipLost):
        repo.acknowledge(claim)


@pytest.mark.parametrize(
    "field,value", [("token", uuid4()), ("worker_id", uuid4()), ("generation", 2)]
)
def test_forged_owner_rejected(
    db: Engine, repo: QueueRepository, tenant: UUID, field: str, value: Any
) -> None:
    job = repo.submit(tenant, "forged", JobSpec(job_type="test"))
    claim = repo.claim(repo.register_worker("worker"))[0]
    forged = replace(claim, **{field: value})
    for operation in (repo.renew, repo.acknowledge, repo.fail):
        with pytest.raises(OwnershipLost):
            operation(forged)
    assert row(db, job)["state"] == "running"


def test_expiry_recovery_and_new_generation(
    db: Engine, repo: QueueRepository, tenant: UUID
) -> None:
    job = repo.submit(tenant, "expiry", JobSpec(job_type="test"))
    worker = repo.register_worker("worker")
    old = repo.claim(worker)[0]
    force_expired(db, job)
    for operation in (repo.renew, repo.acknowledge, repo.fail):
        with pytest.raises(OwnershipLost):
            operation(old)
    assert repo.recover_expired() == 1
    assert repo.recover_expired() == 0
    make_due(db, job)
    new = repo.claim(worker)[0]
    assert new.generation == old.generation + 1 and new.token != old.token
    for operation in (repo.renew, repo.acknowledge, repo.fail):
        with pytest.raises(OwnershipLost):
            operation(old)
    repo.acknowledge(new)
    with db.connect() as conn:
        assert list(
            conn.execute(text("SELECT outcome FROM job_attempts ORDER BY generation")).scalars()
        ) == ["lease_expired", "succeeded"]


def test_retry_exhaustion_and_delay(db: Engine, repo: QueueRepository, tenant: UUID) -> None:
    job = repo.submit(tenant, "retry", JobSpec(job_type="test", max_attempts=2))
    worker = repo.register_worker("worker")
    first = repo.claim(worker)[0]
    repo.fail(first)
    failed = row(db, job)
    assert failed["state"] == "retry_wait" and failed["attempts"] == 1
    assert 0 <= (failed["available_at"] - failed["updated_at"]).total_seconds() <= 1.1
    make_due(db, job)
    second = repo.claim(worker)[0]
    repo.fail(second)
    assert row(db, job)["state"] == "dead_letter"
    assert repo.claim(worker) == []


def test_permanent_failure_and_exhausted_recovery(
    db: Engine, repo: QueueRepository, tenant: UUID
) -> None:
    a = repo.submit(tenant, "permanent", JobSpec(job_type="test", priority=1))
    b = repo.submit(tenant, "crash", JobSpec(job_type="test", max_attempts=1))
    worker = repo.register_worker("worker")
    repo.fail(repo.claim(worker)[0], retryable=False, error_code="invalid_input")
    assert row(db, a)["state"] == "dead_letter"
    repo.claim(worker)
    force_expired(db, b)
    assert repo.recover_expired() == 1
    assert row(db, b)["state"] == "dead_letter"


def test_renewal_capped_by_execution_deadline(
    db: Engine, repo: QueueRepository, tenant: UUID
) -> None:
    job = repo.submit(tenant, "renew", JobSpec(job_type="test", execution_timeout_s=30))
    claim = repo.claim(repo.register_worker("worker"), lease_seconds=1)[0]
    before = row(db, job)
    repo.renew(claim, lease_seconds=300)
    renewed = row(db, job)
    assert renewed["lease_expires_at"] > before["lease_expires_at"]
    assert renewed["lease_expires_at"] == renewed["execution_deadline"]


@pytest.mark.parametrize("expire", [False, True])
def test_cancel_running_prevents_success(
    db: Engine, repo: QueueRepository, tenant: UUID, expire: bool
) -> None:
    job = repo.submit(tenant, "cancel", JobSpec(job_type="test"))
    claim = repo.claim(repo.register_worker("worker"))[0]
    assert repo.cancel(tenant, job, actor="operator") == "cancel_requested"
    with pytest.raises(OwnershipLost):
        repo.acknowledge(claim)
    if expire:
        force_expired(db, job)
        assert repo.recover_expired() == 1
    else:
        repo.acknowledge_cancellation(claim)
    assert row(db, job)["state"] == "cancelled"


def test_cancel_queued_and_tenant_scope(db: Engine, repo: QueueRepository, tenant: UUID) -> None:
    job = repo.submit(tenant, "cancel", JobSpec(job_type="test"))
    from sqlalchemy.exc import NoResultFound

    with pytest.raises(NoResultFound):
        repo.cancel(uuid4(), job, actor="other")
    assert repo.cancel(tenant, job, actor="operator") == "cancelled"
    assert repo.cancel(tenant, job, actor="operator") == "cancelled"
    assert repo.claim(repo.register_worker("worker")) == []


def test_worker_heartbeat_and_expired_registration(db: Engine, repo: QueueRepository) -> None:
    worker = repo.register_worker("worker")
    repo.heartbeat_worker(worker)
    with pytest.raises(WorkerExpired):
        repo.claim(replace(worker, heartbeat_token=uuid4()))
    with db.begin() as conn:
        conn.execute(
            text("""
            UPDATE worker_registrations SET heartbeat_at=clock_timestamp()-interval '2 seconds',
              heartbeat_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id
        """),
            {"id": worker.id},
        )
    with pytest.raises(WorkerExpired):
        repo.claim(worker)
    with pytest.raises(WorkerExpired):
        repo.heartbeat_worker(worker)


def test_database_rejects_invalid_row_shapes(
    db: Engine, repo: QueueRepository, tenant: UUID
) -> None:
    job = repo.submit(tenant, "shape", JobSpec(job_type="test"))
    for mutation in ("state='running'", "state='succeeded'", "attempts=11", "priority=101"):
        with pytest.raises(IntegrityError), db.begin() as conn:
            conn.execute(text("UPDATE jobs SET " + mutation + " WHERE id=:id"), {"id": job})
