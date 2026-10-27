from uuid import UUID

import pytest
from chronos.engine.models import JobSpec
from chronos.engine.repository import QueueRepository
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


def test_failed_submission_does_not_reserve_key(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = repo._event

    def injected(*args: object) -> None:
        raise RuntimeError("injected submission audit failure")

    monkeypatch.setattr(repo, "_event", injected)
    with pytest.raises(RuntimeError, match="injected"):
        repo.submit(tenant, "rollback", JobSpec(job_type="test"))
    with db.connect() as conn:
        for table in ("jobs", "submission_keys", "job_events"):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    monkeypatch.setattr(repo, "_event", original)
    repo.submit(tenant, "rollback", JobSpec(job_type="test"))


def test_occurrence_identity_is_unique_and_tenant_scoped(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
) -> None:
    first = repo.submit(tenant, "first", JobSpec(job_type="test"))
    second = repo.submit(tenant, "second", JobSpec(job_type="test"))
    other_tenant = repo.create_tenant("other")
    foreign_job = repo.submit(other_tenant, "foreign", JobSpec(job_type="test"))
    with db.begin() as conn:
        schedule: UUID = conn.execute(
            text("""
            INSERT INTO schedules(tenant_id,queue,job_type,payload,interval_seconds,next_fire_at)
            VALUES (:tenant,'default','test','{}',60,statement_timestamp()) RETURNING id
        """),
            {"tenant": tenant},
        ).scalar_one()
        conn.execute(
            text("""
            INSERT INTO schedule_occurrences(schedule_id,tenant_id,revision,scheduled_for,job_id)
            VALUES (:schedule,:tenant,1,'2026-10-27T00:00:00Z',:job)
        """),
            {"schedule": schedule, "tenant": tenant, "job": first},
        )
    for job, revision in ((second, 1), (foreign_job, 2)):
        with pytest.raises(IntegrityError), db.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO schedule_occurrences(
                    schedule_id,tenant_id,revision,scheduled_for,job_id)
                VALUES (:schedule,:tenant,:revision,'2026-10-27T00:00:00Z',:job)
            """),
                {"schedule": schedule, "tenant": tenant, "job": job, "revision": revision},
            )


def test_only_one_unfinished_attempt_per_job(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
) -> None:
    repo.submit(tenant, "active", JobSpec(job_type="test"))
    claim = repo.claim(repo.register_worker("worker"))[0]
    with pytest.raises(IntegrityError), db.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO job_attempts(job_id,generation,lease_token,owner)
            VALUES (:job,2,gen_random_uuid(),:worker)
        """),
            {"job": claim.job_id, "worker": claim.worker_id},
        )
