from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from chronos.engine.models import JobSpec, OwnershipLost
from chronos.engine.repository import QueueRepository
from conftest import force_expired
from sqlalchemy import Engine, text

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["acknowledge", "fail"])
def test_competing_finalizers_can_finish_attempt_only_once(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
    operation: str,
) -> None:
    repo.submit(tenant, "finalize", JobSpec(job_type="test"))
    claim = repo.claim(repo.register_worker("worker"))[0]
    barrier = Barrier(2, timeout=10)

    def finish(_: int) -> bool:
        barrier.wait()
        try:
            getattr(repo, operation)(claim)
            return True
        except OwnershipLost:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(finish, range(2))) == 1
    with db.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM job_attempts WHERE outcome IS NOT NULL")
            ).scalar_one()
            == 1
        )
        assert conn.execute(text("SELECT count(*) FROM job_events")).scalar_one() == 3


def test_expired_completion_races_recovery(db: Engine, repo: QueueRepository, tenant: UUID) -> None:
    job = repo.submit(tenant, "race", JobSpec(job_type="test"))
    claim = repo.claim(repo.register_worker("worker"))[0]
    force_expired(db, job)
    barrier = Barrier(2, timeout=10)

    def complete() -> None:
        barrier.wait()
        with pytest.raises(OwnershipLost):
            repo.acknowledge(claim)

    def recover() -> int:
        barrier.wait()
        return repo.recover_expired()

    with ThreadPoolExecutor(max_workers=2) as executor:
        completion = executor.submit(complete)
        recovery = executor.submit(recover)
        completion.result(timeout=10)
        assert recovery.result(timeout=10) == 1


def test_database_deadline_rejects_owner_even_with_future_lease(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
) -> None:
    job = repo.submit(tenant, "deadline", JobSpec(job_type="test"))
    claim = repo.claim(repo.register_worker("worker"))[0]
    with db.begin() as conn:
        conn.execute(
            text("UPDATE jobs SET execution_deadline=clock_timestamp() WHERE id=:id"), {"id": job}
        )
    for operation in (repo.renew, repo.acknowledge, repo.fail):
        with pytest.raises(OwnershipLost):
            operation(claim)
