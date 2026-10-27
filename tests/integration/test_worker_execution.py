import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import UUID

import pytest
from chronos.engine.models import JobSpec, LeaseWindow, OwnershipLost, WorkerExpired
from chronos.engine.repository import QueueRepository
from chronos.worker.execution import Execution
from chronos.worker.settings import WorkerSettings
from conftest import migrate
from pydantic import JsonValue
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def settings() -> WorkerSettings:
    return WorkerSettings.model_validate(
        {
            "CHRONOS_DATABASE_URL": "postgresql://unused@localhost/unused",
            "lease_seconds": 6,
            "renew_interval_s": 0.25,
            "db_call_timeout_s": 1,
        }
    )


async def test_runtime_executes_in_child_and_records_results(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
) -> None:
    job = repo.submit(
        tenant, "execution", JobSpec(job_type="synthetic_report", payload={"rows": 123})
    )
    claim = repo.claim(repo.register_worker("worker"), lease_seconds=6)[0]
    with ThreadPoolExecutor(max_workers=2) as executor:
        await Execution(repo, settings(), executor, claim).run()
    with db.connect() as conn:
        assert (
            conn.execute(text("SELECT state FROM jobs WHERE id=:id"), {"id": job}).scalar_one()
            == "succeeded"
        )
        result: dict[str, JsonValue] = conn.execute(
            text("SELECT result FROM job_attempts")
        ).scalar_one()
        assert result["rows"] == 123
        assert (
            conn.execute(text("SELECT execution_started_at FROM job_attempts")).scalar_one()
            is not None
        )


async def test_lease_failure_cancels_child_without_success_or_failure_transition(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo.submit(tenant, "disconnect", JobSpec(job_type="synthetic_report", payload={"delay_s": 10}))
    claim = repo.claim(repo.register_worker("worker"), lease_seconds=6)[0]
    original = repo.renew
    calls = 0

    def disconnect(*args: object, **kwargs: object) -> LeaseWindow:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OperationalError("redacted", None, Exception("not logged"))
        return original(claim, lease_seconds=6)

    monkeypatch.setattr(repo, "renew", disconnect)
    with ThreadPoolExecutor(max_workers=2) as executor:
        await asyncio.wait_for(Execution(repo, settings(), executor, claim).run(), timeout=5)
    with db.connect() as conn:
        assert conn.execute(text("SELECT state FROM jobs")).scalar_one() == "running"
        assert conn.execute(text("SELECT outcome FROM job_attempts")).scalar_one() is None


async def test_disallowed_handler_never_starts_child(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
) -> None:
    repo.submit(tenant, "unknown", JobSpec(job_type="os.system", payload={"command": "ignored"}))
    claim = repo.claim(repo.register_worker("worker"))[0]
    with ThreadPoolExecutor(max_workers=2) as executor:
        await Execution(repo, settings(), executor, claim).run()
    with db.connect() as conn:
        assert conn.execute(text("SELECT state FROM jobs")).scalar_one() == "dead_letter"
        assert (
            conn.execute(text("SELECT execution_started_at FROM job_attempts")).scalar_one() is None
        )


async def test_results_remain_fenced(db: Engine, repo: QueueRepository, tenant: UUID) -> None:
    repo.submit(tenant, "result", JobSpec(job_type="synthetic_report"))
    claim = repo.claim(repo.register_worker("worker"))[0]
    repo.acknowledge(claim, result={"rows": 10})
    with pytest.raises(OwnershipLost):
        repo.acknowledge(claim, result={"rows": 999})
    with db.connect() as conn:
        assert conn.execute(text("SELECT result FROM job_attempts")).scalar_one() == {"rows": 10}


async def test_unusable_lease_window_never_dispatches(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo.submit(tenant, "late", JobSpec(job_type="synthetic_report"))
    claim = repo.claim(repo.register_worker("worker"))[0]

    def late_reply(*args: object, **kwargs: object) -> LeaseWindow:
        return LeaseWindow(-0.1, 20)

    monkeypatch.setattr(repo, "renew", late_reply)
    with ThreadPoolExecutor(max_workers=1) as executor:
        await Execution(repo, settings(), executor, claim).run()
    with db.connect() as conn:
        assert (
            conn.execute(text("SELECT execution_started_at FROM job_attempts")).scalar_one() is None
        )


async def test_running_cancellation_stops_child_then_acknowledges(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
) -> None:
    job = repo.submit(
        tenant, "cancel-running", JobSpec(job_type="synthetic_report", payload={"delay_s": 10})
    )
    claim = repo.claim(repo.register_worker("worker"), lease_seconds=6)[0]
    with ThreadPoolExecutor(max_workers=2) as executor:
        running = asyncio.create_task(Execution(repo, settings(), executor, claim).run())
        async with asyncio.timeout(5):
            while True:
                with db.connect() as conn:
                    started: datetime | None = conn.execute(
                        text("SELECT execution_started_at FROM job_attempts")
                    ).scalar_one()
                if started is not None:
                    break
                await asyncio.sleep(0.05)
        await asyncio.to_thread(repo.cancel, tenant, job, actor="test")
        await asyncio.wait_for(running, timeout=5)
    with db.connect() as conn:
        assert conn.execute(text("SELECT state FROM jobs")).scalar_one() == "cancelled"
        assert conn.execute(text("SELECT outcome FROM job_attempts")).scalar_one() == "cancelled"


async def test_upgrade_preserves_phase2_jobs_and_registrations(
    db: Engine,
    repo: QueueRepository,
) -> None:
    migrate(db, "0001", downgrade=True)
    tenant = repo.create_tenant("existing-phase2")
    job = repo.submit(tenant, "existing", JobSpec(job_type="synthetic_report"))
    worker = repo.register_worker("existing-worker")
    migrate(db, "head")
    claim = repo.claim(worker)[0]
    assert claim.job_id == job
    repo.acknowledge(claim, result={"preserved": True})
    with db.connect() as conn:
        assert conn.execute(text("SELECT result FROM job_attempts")).scalar_one() == {
            "preserved": True
        }


async def test_draining_and_stopped_workers_cannot_claim(
    repo: QueueRepository,
    tenant: UUID,
) -> None:
    repo.submit(tenant, "lifecycle", JobSpec(job_type="synthetic_report"))
    worker = repo.register_worker("lifecycle-worker")
    repo.worker_state(worker, "draining")
    repo.heartbeat_worker(worker)
    with pytest.raises(WorkerExpired):
        repo.claim(worker)
    repo.worker_state(worker, "stopped")
    with pytest.raises(WorkerExpired):
        repo.heartbeat_worker(worker)
