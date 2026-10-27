from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from typing import Any
from uuid import UUID

import pytest
from chronos.engine.models import Claim, JobSpec, OwnershipLost, Worker
from chronos.engine.repository import QueueRepository
from conftest import force_expired
from sqlalchemy import Engine, event, text

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("round_number", range(3))
def test_eight_simultaneous_consumers_claim_distinct_attempts(
    db: Engine, repo: QueueRepository, tenant: UUID, round_number: int
) -> None:
    expected = {
        repo.submit(tenant, f"{round_number}-{i}", JobSpec(job_type="test")) for i in range(80)
    }
    workers = [repo.register_worker(f"worker-{i}") for i in range(8)]
    barrier = Barrier(8, timeout=10)

    def consume(worker: Worker) -> list[Claim]:
        barrier.wait()
        return repo.claim(worker, limit=10)

    with ThreadPoolExecutor(max_workers=8) as executor:
        batches = list(executor.map(consume, workers))
    claims = [claim for batch in batches for claim in batch]
    assert len(claims) == 80
    assert {claim.job_id for claim in claims} == expected
    assert len({claim.token for claim in claims}) == 80
    assert all(claim.generation == 1 for claim in claims)
    with db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM job_attempts")).scalar_one() == 80
        assert (
            conn.execute(
                text("""
            SELECT count(*) FROM job_attempts a JOIN jobs j ON j.id=a.job_id
            WHERE a.generation=j.generation AND a.lease_token=j.lease_token
              AND a.owner=j.lease_owner AND a.outcome IS NULL AND j.state='running'
        """)
            ).scalar_one()
            == 80
        )


def test_locked_job_is_skipped_without_blocking_and_claim_locks_are_released(
    db: Engine, repo: QueueRepository, tenant: UUID
) -> None:
    locked = repo.submit(tenant, "locked", JobSpec(job_type="test", priority=100))
    free = repo.submit(tenant, "free", JobSpec(job_type="test"))
    worker = repo.register_worker("worker")
    with db.begin() as conn:
        conn.execute(text("SELECT id FROM jobs WHERE id=:id FOR UPDATE"), {"id": locked})
        with ThreadPoolExecutor(max_workers=1) as executor:
            claims = executor.submit(repo.claim, worker).result(timeout=10)
        assert [claim.job_id for claim in claims] == [free]
        # A separate session acquires the claimed row NOWAIT while execution would run.
        with db.begin() as observer:
            observer.execute(
                text("SELECT id FROM jobs WHERE id=:id FOR UPDATE NOWAIT"), {"id": free}
            )
    assert repo.claim(worker)[0].job_id == locked


def test_claim_failure_rolls_back_job_attempt_and_events(
    db: Engine, repo: QueueRepository, tenant: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = repo.submit(tenant, "rollback", JobSpec(job_type="test"))
    worker = repo.register_worker("worker")
    original = repo._event

    def injected(*args: object) -> None:
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(repo, "_event", injected)
    with pytest.raises(RuntimeError, match="injected"):
        repo.claim(worker)
    with db.connect() as conn:
        assert conn.execute(text("SELECT attempts FROM jobs")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM job_attempts")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM job_events")).scalar_one() == 1
    monkeypatch.setattr(repo, "_event", original)
    assert repo.claim(worker)[0].job_id == job


def test_simultaneous_idempotent_submissions(
    db: Engine, repo: QueueRepository, tenant: UUID
) -> None:
    barrier = Barrier(8, timeout=10)

    def submit(_: int) -> UUID:
        barrier.wait()
        return repo.submit(tenant, "concurrent-key", JobSpec(job_type="test"))

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(submit, range(8)))
    assert len(set(results)) == 1
    with db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM submission_keys")).scalar_one() == 1


@pytest.mark.parametrize("operation", ["renew", "acknowledge", "fail"])
def test_expiry_rechecked_after_waiting_for_row_lock(
    db: Engine, repo: QueueRepository, tenant: UUID, operation: str
) -> None:
    job = repo.submit(tenant, "wait", JobSpec(job_type="test"))
    claim = repo.claim(repo.register_worker("worker"))[0]
    about_to_lock = Event()

    def observe(
        conn: Any, cursor: Any, statement: Any, parameters: Any, context: Any, executemany: Any
    ) -> None:
        if statement.startswith("SELECT id FROM jobs WHERE id="):
            about_to_lock.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with db.begin() as holder:
            holder.execute(text("SELECT id FROM jobs WHERE id=:id FOR UPDATE"), {"id": job})
            event.listen(db, "before_cursor_execute", observe)
            try:
                future = executor.submit(getattr(repo, operation), claim)
                assert about_to_lock.wait(timeout=5)
                holder.execute(
                    text("""
                    UPDATE jobs SET lease_expires_at=clock_timestamp()-interval '1 second'
                    WHERE id=:id
                """),
                    {"id": job},
                )
            finally:
                event.remove(db, "before_cursor_execute", observe)
        with pytest.raises(OwnershipLost):
            future.result(timeout=10)


def test_competing_reapers_recover_once(db: Engine, repo: QueueRepository, tenant: UUID) -> None:
    job = repo.submit(tenant, "reaper", JobSpec(job_type="test"))
    repo.claim(repo.register_worker("worker"))
    force_expired(db, job)
    barrier = Barrier(4, timeout=10)

    def recover(_: int) -> int:
        barrier.wait()
        return repo.recover_expired()

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(recover, range(4))) == 1
    with db.connect() as conn:
        assert (
            conn.execute(text("SELECT outcome FROM job_attempts")).scalar_one() == "lease_expired"
        )
