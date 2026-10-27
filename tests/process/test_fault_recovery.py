"""Durable external gates position real workers at precise failure boundaries."""

import json
import os
import secrets
import socket
import subprocess
import urllib.request
from collections.abc import Iterator
from contextlib import suppress
from typing import Any
from uuid import UUID

import pytest
from chronos.api.main import create_app
from chronos.api.security import ApiKeys
from chronos.api.settings import ApiSettings
from chronos.engine.models import JobSpec
from fastapi.testclient import TestClient
from test_worker_processes import Cluster

pytestmark = pytest.mark.compose


class FaultCluster(Cluster):
    def __init__(self, point: str) -> None:
        super().__init__(8)
        with socket.socket() as port:
            port.bind(("127.0.0.1", 0))
            self.sink_port = port.getsockname()[1]
        self.env.update(
            CHRONOS_FAULT_INJECTION="1",
            CHRONOS_FAULT_POINT=point,
            CHRONOS_FAULT_WORKER="worker-1",
            CHRONOS_ENABLE_EFFECT_DEMO="1",
            CHRONOS_DEMO_ALLOW_UNSAFE="1",
            CHRONOS_DEMO_SINK_TOKEN=secrets.token_hex(32),
            CHRONOS_DEMO_SINK_PORT=str(self.sink_port),
            CHRONOS_WORKER_CONCURRENCY="1",
            CHRONOS_WORKER_LEASE_SECONDS="12",
            CHRONOS_WORKER_WORKER_TTL_SECONDS="12",
            CHRONOS_WORKER_ALLOWED_HANDLERS="demo_effect,notification,synthetic_report",
        )

    def command(self, *args: str, timeout: float = 120) -> str:
        return super().command("--profile", "faults", *args, timeout=timeout)

    def sink(self, path: str, data: dict[str, object] | None = None) -> dict[str, Any]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.sink_port}" + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.env["CHRONOS_DEMO_SINK_TOKEN"],
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as response:
            result: dict[str, Any] = json.load(response)
            return result

    def gate(self, job: UUID, stage: str, action: str = "status") -> dict[str, Any]:
        return self.sink(f"/gates/{job}/{action}", {"generation": 1, "stage": stage})

    def sink_ready(self) -> bool:
        try:
            return bool(self.sink("/health/live")["ready"])
        except OSError:
            return False

    def replacements(self) -> None:
        self.command("up", "-d", "worker-2", "worker-3")
        self.wait(
            lambda: (
                self.scalar("""
            SELECT count(*) FROM worker_registrations WHERE name IN ('worker-2','worker-3')
                AND state='active' AND heartbeat_expires_at>clock_timestamp()
        """)
                == 2
            ),
            timeout=30,
        )

    def assert_ownership(self, job: UUID) -> bool:
        assert (
            self.scalar(
                """
            SELECT count(*) FROM job_attempts WHERE job_id=:job AND outcome IS NULL
        """,
                job=job,
            )
            <= 1
        )
        assert (
            self.scalar(
                """
            SELECT count(*) FROM job_attempts a JOIN jobs j ON a.job_id=j.id
            WHERE a.job_id=:job AND a.outcome IS NULL AND
              (a.owner IS DISTINCT FROM j.lease_owner OR a.generation<>j.generation
               OR a.lease_token IS DISTINCT FROM j.lease_token)
        """,
                job=job,
            )
            == 0
        )
        return self.terminal(job)

    def report_from_old_process(self, report: dict[str, object]) -> dict[str, Any]:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                self.project,
                "--profile",
                "workers",
                "--profile",
                "faults",
                "exec",
                "-T",
                "worker-1",
                ".venv/bin/python",
                "-m",
                "chronos.worker.fault_actor",
            ],
            env=self.env,
            input=json.dumps(report),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        value: dict[str, Any] = json.loads(result.stdout)
        return value

    def facts(self, scenario: str, job: UUID, **facts: object) -> None:
        attempts = self.rows(
            """
            SELECT generation,owner,outcome,recovery_reason FROM job_attempts
            WHERE job_id=:job ORDER BY generation
        """,
            job=job,
        )
        self.evidence(
            scenario, job_id=job, attempts=attempts, sink=self.sink(f"/stats/{job}"), **facts
        )


@pytest.fixture
def fault_cluster(request: pytest.FixtureRequest) -> Iterator[FaultCluster]:
    if os.environ.get("CHRONOS_RUN_COMPOSE_TESTS") != "1":
        pytest.skip("Enable CHRONOS_RUN_COMPOSE_TESTS=1 for real process faults")
    current = FaultCluster(str(getattr(request, "param", "")))
    try:
        current.command("up", "-d", "postgres", "migrate", "effect-sink", "worker-1")
        current.wait(lambda: current.scalar("SELECT count(*) FROM worker_registrations") == 1)
        current.wait(current.sink_ready)
        yield current
    finally:
        # A paused test process must be thawed before orderly fixture cleanup.
        with suppress(RuntimeError):  # Killed or never-paused containers need no thaw.
            current.command("unpause", "worker-1")
        current.engine.dispose()
        current.command("down", "--volumes", "--remove-orphans", timeout=60)


@pytest.mark.parametrize("fault_cluster", ["after_claim", "during_execute"], indirect=True)
def test_worker_killed_at_precise_pre_effect_boundary(fault_cluster: FaultCluster) -> None:
    cluster = fault_cluster
    stage = cluster.env["CHRONOS_FAULT_POINT"]
    tenant = cluster.repo.create_tenant(stage)
    job = cluster.repo.submit(
        tenant,
        "boundary",
        JobSpec(job_type="demo_effect", retry_base_seconds=0.05, retry_cap_seconds=0.1),
    )
    cluster.wait(lambda: bool(cluster.gate(job, stage)["arrived"]))
    assert cluster.sink(f"/stats/{job}") == {"effects": 0, "requests": 0}
    if stage == "after_claim":
        assert cluster.scalar("SELECT execution_started_at FROM job_attempts") is None
    cluster.command("kill", "-s", "SIGKILL", "worker-1")
    cluster.replacements()
    cluster.wait(lambda: cluster.assert_ownership(job), timeout=60)
    attempts = cluster.rows("SELECT generation,outcome FROM job_attempts ORDER BY generation")
    assert attempts == [
        {"generation": 1, "outcome": "lease_expired"},
        {"generation": 2, "outcome": "succeeded"},
    ]
    assert cluster.sink(f"/stats/{job}") == {"effects": 1, "requests": 1}
    cluster.facts("kill-" + stage, job, observed_effects=1)


@pytest.mark.parametrize("fault_cluster", ["before_ack"], indirect=True)
@pytest.mark.parametrize("idempotent", [True, False])
def test_kill_after_external_commit_demonstrates_duplicate_effect_risk(
    fault_cluster: FaultCluster,
    idempotent: bool,
) -> None:
    cluster = fault_cluster
    tenant = cluster.repo.create_tenant("after-effect")
    job = cluster.repo.submit(
        tenant,
        "effect",
        JobSpec(
            job_type="demo_effect",
            payload={"idempotent": idempotent},
            retry_base_seconds=0.05,
            retry_cap_seconds=0.1,
        ),
    )
    cluster.wait(lambda: bool(cluster.gate(job, "before_ack")["arrived"]))
    assert cluster.sink(f"/stats/{job}") == {"effects": 1, "requests": 1}
    assert cluster.scalar("SELECT outcome FROM job_attempts") is None
    cluster.command("kill", "-s", "SIGKILL", "worker-1")
    cluster.replacements()
    cluster.wait(lambda: cluster.assert_ownership(job), timeout=60)
    stats = cluster.sink(f"/stats/{job}")
    assert stats == {"effects": 1 if idempotent else 2, "requests": 2}
    cluster.facts(
        "post-effect-idempotent" if idempotent else "post-effect-unsafe",
        job,
        idempotent=idempotent,
        observed_effects=stats["effects"],
    )


@pytest.mark.parametrize("fault_cluster", ["before_ack"], indirect=True)
def test_paused_old_worker_cannot_finalize_replacement(fault_cluster: FaultCluster) -> None:
    cluster = fault_cluster
    tenant = cluster.repo.create_tenant("paused")
    job = cluster.repo.submit(
        tenant,
        "paused",
        JobSpec(job_type="demo_effect", retry_base_seconds=0.05, retry_cap_seconds=0.1),
    )
    cluster.wait(lambda: bool(cluster.gate(job, "before_ack")["arrived"]))
    old = cluster.rows(
        """
        SELECT id AS job_id,tenant_id,lease_owner AS worker_id,lease_token AS token,generation
        FROM jobs WHERE id=:job
    """,
        job=job,
    )[0]
    cluster.command("pause", "worker-1")
    cluster.replacements()
    cluster.wait(lambda: cluster.assert_ownership(job), timeout=60)
    cluster.wait(
        lambda: (
            cluster.scalar(
                "SELECT state FROM worker_registrations WHERE id=:id", id=old["worker_id"]
            )
            == "lost"
        )
    )
    cluster.gate(job, "before_ack", "release")
    cluster.command("unpause", "worker-1")
    cluster.wait(
        lambda: any(
            entry.get("event") == "job_abandoned"
            and entry.get("worker_id") == str(old["worker_id"])
            for entry in cluster.logs()
        )
    )
    report = cluster.report_from_old_process(
        {key: str(value) if isinstance(value, UUID) else value for key, value in old.items()}
    )
    assert report == {"accepted": False, "reason": "ownership_lost"}
    assert (
        cluster.scalar(
            """
        SELECT count(*) FROM audit_events WHERE entity_id=:job
            AND action='ownership_rejected' AND presented_generation=1
    """,
            job=job,
        )
        >= 1
    )
    assert cluster.scalar("SELECT generation FROM jobs WHERE id=:job", job=job) == 2
    assert cluster.sink(f"/stats/{job}") == {"effects": 1, "requests": 2}
    cluster.facts(
        "paused-stale-owner", job, stale_success_rejected=True, heartbeat_state_observed="lost"
    )


@pytest.mark.parametrize("fault_cluster", ["during_execute"], indirect=True)
def test_two_real_workers_compete_after_expiration(fault_cluster: FaultCluster) -> None:
    cluster = fault_cluster
    tenant = cluster.repo.create_tenant("competing")
    job = cluster.repo.submit(
        tenant,
        "compete",
        JobSpec(job_type="demo_effect", retry_base_seconds=0.05, retry_cap_seconds=0.1),
    )
    cluster.wait(lambda: bool(cluster.gate(job, "during_execute")["arrived"]))
    cluster.replacements()  # Both competitors are running BEFORE the current owner dies.
    cluster.command("kill", "-s", "SIGKILL", "worker-1")
    cluster.wait(lambda: cluster.assert_ownership(job), timeout=60)
    assert cluster.scalar("SELECT count(*) FROM job_attempts") == 2
    assert cluster.scalar("SELECT generation FROM jobs") == 2
    assert cluster.sink(f"/stats/{job}")["effects"] == 1
    cluster.facts("competing-after-expiration", job, competitors=2, replacement_attempts=1)


def test_repeated_real_handler_failures_reach_dead_letter(fault_cluster: FaultCluster) -> None:
    cluster = fault_cluster
    tenant = cluster.repo.create_tenant("failures")
    job = cluster.repo.submit(
        tenant,
        "failures",
        JobSpec(
            job_type="notification",
            payload={"recipient": "local", "transient_until_attempt": 3},
            max_attempts=3,
            retry_base_seconds=0.05,
            retry_cap_seconds=0.1,
        ),
    )
    cluster.wait(lambda: cluster.terminal(job), timeout=60)
    assert cluster.scalar("SELECT state FROM jobs") == "dead_letter"
    assert cluster.scalar("SELECT count(*) FROM job_attempts WHERE outcome='retryable_error'") == 3
    assert (
        cluster.scalar("SELECT count(*) FROM job_events WHERE reason='notification_transient'") == 3
    )
    cluster.facts("repeated-process-failures", job, maximum_attempts=3)


@pytest.mark.parametrize("fault_cluster", ["before_ack"], indirect=True)
def test_retry_and_replay_apis_drive_idempotent_real_effects(fault_cluster: FaultCluster) -> None:
    cluster = fault_cluster
    tenant = cluster.repo.create_tenant("manual-recovery")
    source = cluster.repo.submit(
        tenant,
        "source",
        JobSpec(
            job_type="demo_effect", max_attempts=1, retry_base_seconds=0.05, retry_cap_seconds=0.1
        ),
    )
    cluster.wait(lambda: bool(cluster.gate(source, "before_ack")["arrived"]))
    cluster.command("kill", "-s", "SIGKILL", "worker-1")
    cluster.replacements()
    cluster.wait(lambda: cluster.terminal(source), timeout=60)
    assert cluster.scalar("SELECT state FROM jobs WHERE id=:id", id=source) == "dead_letter"
    pepper = secrets.token_hex(32)
    _, token = ApiKeys(cluster.repo, pepper).issue(tenant, "operator")
    config = ApiSettings.model_validate({"CHRONOS_API_AUTH_PEPPER": pepper})
    with TestClient(create_app(config, cluster.engine)) as client:
        headers = {"Authorization": "Bearer " + token, "Idempotency-Key": "manual-retry"}
        response = client.post(
            f"/v1/jobs/{source}/retry", json={"max_attempts": 2}, headers=headers
        )
        assert response.status_code == 201
        retried = UUID(response.json()["job_id"])
        cluster.wait(lambda: cluster.terminal(retried))
        assert cluster.scalar("SELECT state FROM jobs WHERE id=:id", id=retried) == "succeeded"
        assert cluster.sink(f"/stats/{source}") == {"effects": 1, "requests": 2}
        replay = client.post(
            f"/v1/jobs/{retried}/replay",
            json={},
            headers=headers | {"Idempotency-Key": "manual-replay"},
        )
        assert replay.status_code == 201
        replayed = UUID(replay.json()["job_id"])
        cluster.wait(lambda: cluster.terminal(replayed))
        assert cluster.sink(f"/stats/{replayed}") == {"effects": 1, "requests": 1}
    cluster.facts(
        "manual-retry-replay-effects",
        source,
        retry_id=retried,
        replay_id=replayed,
        retry_preserved_effect=True,
        replay_requested_new_effect=True,
    )
