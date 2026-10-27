import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import UUID

import pytest
from chronos.api.main import create_app
from chronos.api.security import ApiKeys
from chronos.api.settings import ApiSettings
from chronos.engine.commands import CommandOptions, InvalidJobCommand, JobCommands
from chronos.engine.models import IdempotencyConflict, JobSpec, OwnershipLost, WorkerExpired
from chronos.engine.repository import QueueRepository
from conftest import make_due
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

pytestmark = pytest.mark.integration


def dead_job(repo: QueueRepository, tenant: UUID) -> UUID:
    job = repo.submit(tenant, "dead", JobSpec(job_type="notification", max_attempts=1))
    claim = repo.claim(repo.register_worker("setup"))[0]
    repo.fail(claim, retryable=False, error_code="demonstration_rejection")
    return job


def test_retry_replay_preserve_history_and_distinguish_effect_identity(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
) -> None:
    source = dead_job(repo, tenant)
    commands = JobCommands(repo)
    retry = commands.create(
        tenant, source, "retry", "retry-key", CommandOptions(max_attempts=3), actor="test"
    )
    replay = commands.create(tenant, source, "replay", "replay-key", CommandOptions(), actor="test")
    assert (
        commands.create(
            tenant, source, "retry", "retry-key", CommandOptions(max_attempts=3), actor="test"
        )
        == retry
    )
    with pytest.raises(IdempotencyConflict):
        commands.create(
            tenant, source, "retry", "retry-key", CommandOptions(max_attempts=4), actor="test"
        )
    with db.connect() as conn:
        rows = {row["id"]: row for row in conn.execute(text("SELECT * FROM jobs")).mappings()}
        assert rows[source]["state"] == "dead_letter" and rows[source]["generation"] == 1
        assert rows[retry]["effect_key"] == rows[source]["effect_key"]
        assert rows[replay]["effect_key"] != rows[source]["effect_key"]
        assert rows[retry]["generation"] == rows[replay]["generation"] == 0
        assert rows[retry]["replay_of"] == rows[replay]["replay_of"] == source
        assert rows[retry]["max_attempts"] == 3
        assert conn.execute(text("SELECT count(*) FROM job_command_keys")).scalar_one() == 2


def test_simultaneous_commands_create_one_job(repo: QueueRepository, tenant: UUID) -> None:
    source = dead_job(repo, tenant)
    barrier = Barrier(4, timeout=10)

    def command(_: int) -> UUID:
        barrier.wait()
        return JobCommands(repo).create(
            tenant, source, "retry", "same", CommandOptions(), actor="test"
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert len(set(executor.map(command, range(4)))) == 1


def test_retry_cannot_reset_an_active_generation(repo: QueueRepository, tenant: UUID) -> None:
    job = repo.submit(tenant, "active", JobSpec(job_type="notification"))
    repo.claim(repo.register_worker("worker"))
    for action in ("retry", "replay"):
        with pytest.raises(InvalidJobCommand):
            JobCommands(repo).create(tenant, job, action, "key", CommandOptions(), actor="test")


def test_timeout_recovery_and_heartbeat_loss_are_audited(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
) -> None:
    job = repo.submit(tenant, "deadline", JobSpec(job_type="notification", max_attempts=1))
    worker = repo.register_worker("paused")
    claim = repo.claim(worker)[0]
    with db.begin() as conn:
        conn.execute(
            text("UPDATE jobs SET execution_deadline=clock_timestamp() WHERE id=:id"), {"id": job}
        )
        conn.execute(
            text("""
            UPDATE worker_registrations SET heartbeat_at=clock_timestamp()-interval '2 seconds',
                heartbeat_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id
        """),
            {"id": worker.id},
        )
    assert repo.monitor_workers() == 1
    assert repo.monitor_workers() == 0
    with pytest.raises(WorkerExpired):
        repo.heartbeat_worker(worker)
    assert repo.recover_expired() == 1
    with pytest.raises(OwnershipLost):
        repo.acknowledge(claim)
    with db.connect() as conn:
        assert (
            conn.execute(text("SELECT recovery_reason FROM job_attempts")).scalar_one()
            == "runtime_exceeded"
        )
        event = conn.execute(
            text("""
            SELECT from_state,to_state,reason FROM job_events WHERE event_type='lease_expired'
        """)
        ).one()
        assert tuple(event) == ("running", "dead_letter", "runtime_exceeded")
        assert (
            conn.execute(
                text("SELECT count(*) FROM audit_events WHERE reason='heartbeat_expired'")
            ).scalar_one()
            == 1
        )


def test_repeated_failure_uses_exact_jitter_policy_and_budget(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bounds: list[float] = []

    def midpoint(low: float, high: float) -> float:
        assert low == 0
        bounds.append(high)
        return high / 2

    monkeypatch.setattr("chronos.engine.retries.random.uniform", midpoint)
    job = repo.submit(
        tenant,
        "repeat",
        JobSpec(job_type="notification", max_attempts=4, retry_base_seconds=2, retry_cap_seconds=5),
    )
    worker = repo.register_worker("worker")
    for attempt in range(1, 5):
        claim = repo.claim(worker)[0]
        assert claim.generation == attempt
        repo.fail(claim, error_code="transient_demo")
        with db.connect() as conn:
            row = (
                conn.execute(text("SELECT state,available_at,updated_at FROM jobs"))
                .mappings()
                .one()
            )
            if attempt < 4:
                delay = (row["available_at"] - row["updated_at"]).total_seconds()
                assert abs(delay - min(5, 2 * 2 ** (attempt - 1)) / 2) < 0.1
        make_due(db, job)
    assert bounds == [2, 4, 5, 5]
    assert repo.claim(worker) == []
    with db.connect() as conn:
        assert conn.execute(text("SELECT state FROM jobs")).scalar_one() == "dead_letter"
        assert (
            conn.execute(
                text("SELECT count(*) FROM job_attempts WHERE outcome='retryable_error'")
            ).scalar_one()
            == 4
        )


def test_audit_is_append_only(db: Engine, repo: QueueRepository, tenant: UUID) -> None:
    repo.submit(tenant, "audit", JobSpec(job_type="notification"))
    for statement in (
        "DELETE FROM job_events",
        "UPDATE audit_events SET actor_id='tampered'",
        "TRUNCATE TABLE audit_events",
    ):
        with pytest.raises(DBAPIError), db.begin() as conn:
            conn.execute(text(statement))


def test_authenticated_retry_replay_http_contract(
    db: Engine, repo: QueueRepository, tenant: UUID
) -> None:
    source = dead_job(repo, tenant)
    pepper = secrets.token_hex(32)
    keys = ApiKeys(repo, pepper)
    _, operator = keys.issue(tenant, "operator")
    _, viewer = keys.issue(tenant, "viewer")
    other = repo.create_tenant("other")
    _, foreign = keys.issue(other, "operator")
    config = ApiSettings.model_validate({"CHRONOS_API_AUTH_PEPPER": pepper})
    with TestClient(create_app(config, db)) as client:
        path = f"/v1/jobs/{source}/retry"
        assert client.post(path, json={}).status_code == 401
        assert (
            client.post(
                path,
                json={},
                headers={"Authorization": "Bearer " + viewer, "Idempotency-Key": "viewer"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                path,
                json={},
                headers={"Authorization": "Bearer " + foreign, "Idempotency-Key": "foreign"},
            ).status_code
            == 404
        )
        headers = {"Authorization": "Bearer " + operator, "Idempotency-Key": "retry"}
        first = client.post(path, json={"max_attempts": 2}, headers=headers)
        assert first.status_code == 201
        assert client.post(path, json={"max_attempts": 2}, headers=headers).json() == first.json()
        assert client.post(path, json={"max_attempts": 3}, headers=headers).status_code == 409
        assert client.post(path, json={"max_attempts": 0}, headers=headers).status_code == 422
        assert first.headers["X-Request-ID"]
        replay = client.post(
            f"/v1/jobs/{source}/replay", json={}, headers=headers | {"Idempotency-Key": "replay"}
        )
        assert replay.status_code == 201 and replay.json()["job_id"] != first.json()["job_id"]


def test_key_revocation_expiry_roles_and_shared_rate_limit(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
) -> None:
    pepper = secrets.token_hex(32)
    keys = ApiKeys(repo, pepper)
    key_id, token = keys.issue(tenant, "admin")
    config = ApiSettings.model_validate(
        {"CHRONOS_API_AUTH_PEPPER": pepper, "CHRONOS_API_RATE_LIMIT": 1}
    )
    with TestClient(create_app(config, db)) as client:
        headers = {"Authorization": "Bearer " + token}
        assert client.get("/v1/workers", headers=headers).status_code == 200
        limited = client.get("/v1/workers", headers=headers)
        assert limited.status_code == 429 and limited.headers["Retry-After"] == "60"
        with db.begin() as conn:
            conn.execute(
                text("UPDATE api_keys SET revoked_at=clock_timestamp() WHERE id=:id"),
                {"id": key_id},
            )
        assert client.get("/v1/workers", headers=headers).status_code == 401
    expired_id, expired = keys.issue(tenant, "operator")
    with db.begin() as conn:
        conn.execute(
            text("UPDATE api_keys SET expires_at=clock_timestamp() WHERE id=:id"),
            {"id": expired_id},
        )
    assert keys.authenticate(expired) is None


def test_committed_command_with_lost_response_is_not_created_twice(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = dead_job(repo, tenant)
    pepper = secrets.token_hex(32)
    _, token = ApiKeys(repo, pepper).issue(tenant, "operator")
    original = JobCommands.create
    committed: list[UUID] = []

    def lose_response(self: JobCommands, *args: Any, **kwargs: Any) -> UUID:
        result = original(self, *args, **kwargs)
        if not committed:
            committed.append(result)
            # The DB transaction above really committed; only its caller's response is lost.
            raise OperationalError("commit_response_lost", None, Exception())
        return result

    monkeypatch.setattr(JobCommands, "create", lose_response)
    config = ApiSettings.model_validate({"CHRONOS_API_AUTH_PEPPER": pepper})
    with TestClient(create_app(config, db)) as client:
        headers = {"Authorization": "Bearer " + token, "Idempotency-Key": "lost-response"}
        path = f"/v1/jobs/{source}/retry"
        assert client.post(path, json={}, headers=headers).status_code == 503
        retry = client.post(path, json={}, headers=headers)
        assert retry.status_code == 201 and UUID(retry.json()["job_id"]) == committed[0]
    with db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM job_command_keys")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 2


def test_worker_health_has_no_tokens_and_uses_database_expiry(
    db: Engine,
    repo: QueueRepository,
    tenant: UUID,
) -> None:
    worker = repo.register_worker("health")
    pepper = secrets.token_hex(32)
    _, token = ApiKeys(repo, pepper).issue(tenant, "admin")
    config = ApiSettings.model_validate({"CHRONOS_API_AUTH_PEPPER": pepper})
    with TestClient(create_app(config, db)) as client:
        headers = {"Authorization": "Bearer " + token}
        live = client.get("/v1/workers", headers=headers).json()[0]
        assert live["is_alive"] is True and "heartbeat_token" not in live
        with db.begin() as conn:
            conn.execute(
                text("""
                UPDATE worker_registrations SET heartbeat_at=clock_timestamp()-interval '2 seconds',
                    heartbeat_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id
            """),
                {"id": worker.id},
            )
        stale = client.get("/v1/workers", headers=headers).json()[0]
        assert stale["is_alive"] is False
        repo.monitor_workers()
        assert client.get("/v1/workers", headers=headers).json()[0]["state"] == "lost"
