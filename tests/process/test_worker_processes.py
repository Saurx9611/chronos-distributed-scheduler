"""Real Linux workers, real PostgreSQL, real container signals; no fake process layer."""

import json
import os
import secrets
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from chronos.engine.models import JobSpec
from chronos.engine.repository import QueueRepository, create_queue_engine
from chronos.worker.demo import workloads
from sqlalchemy import text
from sqlalchemy.engine import URL

pytestmark = pytest.mark.compose


class Cluster:
    def __init__(self, shutdown_grace_s: float = 3) -> None:
        self.project = "chronos-process-" + uuid4().hex[:12]
        password = secrets.token_hex(24)
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        self.env = dict(
            os.environ,
            POSTGRES_PASSWORD=password,
            POSTGRES_USER="chronos_process",
            POSTGRES_DB="chronos_process",
            POSTGRES_PORT=str(port),
            CHRONOS_WORKER_CONCURRENCY="2",
            CHRONOS_WORKER_LEASE_SECONDS="6",
            CHRONOS_WORKER_RENEW_INTERVAL_S="1",
            CHRONOS_WORKER_HEARTBEAT_INTERVAL_S="1",
            CHRONOS_WORKER_WORKER_TTL_SECONDS="12",
            CHRONOS_WORKER_POLL_INTERVAL_S="0.1",
            CHRONOS_WORKER_RECOVERY_INTERVAL_S="0.2",
            CHRONOS_WORKER_SHUTDOWN_GRACE_S=str(shutdown_grace_s),
            CHRONOS_WORKER_RESTART="no",
        )
        self.engine = create_queue_engine(
            URL.create(
                "postgresql+psycopg",
                username="chronos_process",
                password=password,
                host="127.0.0.1",
                port=port,
                database="chronos_process",
            ),
            pool_size=2,
        )
        self.repo = QueueRepository(self.engine)

    def command(self, *args: str, timeout: float = 120) -> str:
        result = subprocess.run(
            ["docker", "compose", "-p", self.project, "--profile", "workers", *args],
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode:
            # Never include connection env/credentials in pytest's error output.
            safe = (result.stdout + result.stderr).replace(
                self.env["POSTGRES_PASSWORD"], "[redacted]"
            )
            raise RuntimeError(f"Compose {args[0]} failed: {safe[-4000:]}")
        return result.stdout

    def scalar(self, query: str, **params: object) -> Any:
        with self.engine.connect() as conn:
            return conn.execute(text(query), params).scalar_one()

    def rows(self, query: str, **params: object) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(text(query), params).mappings()]

    @staticmethod
    def wait(predicate: Callable[[], bool], *, timeout: float = 45) -> None:
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if predicate():
                return
            time.sleep(0.1)  # Condition polling only; execution ordering uses durable DB/log state.
        raise AssertionError("real worker condition did not become true before deadline")

    def logs(self) -> list[dict[str, Any]]:
        raw = self.command(
            "logs", "--no-log-prefix", "--no-color", "worker-1", "worker-2", "worker-3"
        )
        if self.env["POSTGRES_PASSWORD"] in raw or "PAYLOAD_MUST_NOT_APPEAR" in raw:
            pytest.fail("worker log redaction check failed")
        return [json.loads(line) for line in raw.splitlines() if line.startswith("{")]

    def job_started(self, job: UUID) -> bool:
        return any(
            entry.get("event") == "handler_spawned" and entry.get("job_id") == str(job)
            for entry in self.logs()
        )

    def exit_code(self, service: str) -> int:
        container = self.command("ps", "-a", "-q", service).strip()
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.ExitCode}}", container],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return int(result.stdout.strip())

    def terminal(self, job: UUID) -> bool:
        return bool(
            self.scalar(
                """
            SELECT state IN ('succeeded','dead_letter','cancelled') FROM jobs WHERE id=:id
        """,
                id=job,
            )
        )

    def only_first_worker(self) -> None:
        self.command("stop", "--timeout", "12", "worker-2", "worker-3")

    def evidence(self, scenario: str, **facts: object) -> None:
        directory = Path("reports/worker-processes")
        directory.mkdir(parents=True, exist_ok=True)
        entries = self.logs()
        (directory / f"{scenario}.jsonl").write_text(
            "\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8"
        )
        (directory / f"{scenario}.json").write_text(
            json.dumps(facts, indent=2, default=str), encoding="utf-8"
        )


@pytest.fixture
def cluster(request: pytest.FixtureRequest) -> Iterator[Cluster]:
    if os.environ.get("CHRONOS_RUN_COMPOSE_TESTS") != "1":
        pytest.skip("Enable CHRONOS_RUN_COMPOSE_TESTS=1 for real Compose signal tests")
    current = Cluster(float(getattr(request, "param", 3)))
    try:
        current.command("up", "-d", "postgres", "migrate", "worker-1", "worker-2", "worker-3")
        current.wait(
            lambda: (
                current.scalar("""
            SELECT count(DISTINCT name) FROM worker_registrations
            WHERE state='active' AND heartbeat_expires_at>clock_timestamp()
        """)
                == 3
            ),
            timeout=45,
        )
        yield current
    finally:
        current.engine.dispose()
        # Only this fixture's UUID-named project/volumes are removed, never the user's project.
        current.command("down", "--volumes", "--remove-orphans", timeout=60)


def test_three_processes_distribute_real_work_with_bounded_concurrency(cluster: Cluster) -> None:
    tenant = cluster.repo.create_tenant("distributed-demo")
    specs = workloads(2)
    jobs = [cluster.repo.submit(tenant, str(i), specs[i % 4]) for i in range(12)]
    peaks: dict[str, int] = {}
    snapshots = 0

    def finished() -> bool:
        nonlocal snapshots
        active = cluster.rows("""
            SELECT lease_owner,count(*) AS active FROM jobs
            WHERE state='running' GROUP BY lease_owner
        """)
        for item in active:
            owner = str(item["lease_owner"])
            peaks[owner] = max(peaks.get(owner, 0), item["active"])
            assert item["active"] <= 2
        assert (
            cluster.scalar("""
            SELECT count(*) FROM (
                SELECT job_id FROM job_attempts WHERE outcome IS NULL
                GROUP BY job_id HAVING count(DISTINCT owner)>1
            ) duplicates
        """)
            == 0
        )
        assert (
            cluster.scalar("""
            SELECT count(*) FROM job_attempts a JOIN jobs j ON a.job_id=j.id
            WHERE a.outcome IS NULL AND (a.owner IS DISTINCT FROM j.lease_owner
                OR a.generation<>j.generation OR a.lease_token IS DISTINCT FROM j.lease_token)
        """)
            == 0
        )
        snapshots += 1
        return bool(cluster.scalar("SELECT count(*) FROM jobs WHERE state='succeeded'") == 12)

    cluster.wait(finished)
    results = cluster.rows("SELECT job_id,owner,generation,outcome,result FROM job_attempts")
    owners = {str(item["owner"]) for item in results}
    assert len(owners) == 3 and len(results) == len(jobs) == 12
    assert all(item["generation"] == 1 and item["outcome"] == "succeeded" for item in results)
    assert all(peak == 2 for peak in peaks.values())
    kinds = cluster.rows(
        "SELECT j.job_type,a.result FROM jobs j JOIN job_attempts a ON a.job_id=j.id"
    )
    for item in kinds:
        if item["job_type"] == "image_metadata":
            assert item["result"]["width"] == item["result"]["height"] == 1
        elif item["job_type"] == "csv_transform":
            assert item["result"]["amount_total"] == "3.74"
        elif item["job_type"] == "synthetic_report":
            assert item["result"]["rows"] == 1000
        else:
            assert item["result"]["simulated"] is True
    cluster.evidence(
        "distribution",
        jobs=len(jobs),
        owners=sorted(owners),
        peaks=peaks,
        snapshots=snapshots,
        all_attempts_succeeded=True,
    )


@pytest.mark.parametrize("cluster", [8], indirect=True)
def test_sigterm_drains_and_stops_claiming(cluster: Cluster) -> None:
    cluster.only_first_worker()
    tenant = cluster.repo.create_tenant("sigterm")
    job = cluster.repo.submit(
        tenant, "active", JobSpec(job_type="synthetic_report", payload={"delay_s": 2})
    )
    cluster.wait(lambda: cluster.job_started(job))
    owner = cluster.scalar("SELECT lease_owner FROM jobs WHERE id=:id", id=job)
    with ThreadPoolExecutor(max_workers=1) as executor:
        stopping = executor.submit(cluster.command, "stop", "--timeout", "12", "worker-1")
        cluster.wait(
            lambda: any(
                entry.get("event") == "worker_draining" and entry.get("worker_id") == str(owner)
                for entry in cluster.logs()
            )
        )
        late = cluster.repo.submit(tenant, "late", JobSpec(job_type="synthetic_report"))
        stopping.result(timeout=15)
    assert cluster.scalar("SELECT state FROM jobs WHERE id=:id", id=job) == "succeeded"
    assert cluster.scalar("SELECT state FROM jobs WHERE id=:id", id=late) == "queued"
    assert cluster.exit_code("worker-1") == 0
    assert (
        cluster.scalar("SELECT state FROM worker_registrations WHERE name='worker-1'") == "stopped"
    )
    cluster.evidence("sigterm", drained_job=job, queued_after_signal=late)


def test_sigkill_expires_and_recovers_in_another_process(cluster: Cluster) -> None:
    cluster.only_first_worker()
    tenant = cluster.repo.create_tenant("sigkill")
    job = cluster.repo.submit(
        tenant,
        "kill",
        JobSpec(
            job_type="notification", payload={"recipient": "PAYLOAD_MUST_NOT_APPEAR", "delay_s": 4}
        ),
    )
    cluster.wait(lambda: cluster.job_started(job))
    cluster.wait(
        lambda: cluster.scalar("SELECT count(*) FROM job_events WHERE event_type='renewed'") >= 2
    )
    cluster.command("kill", "-s", "SIGKILL", "worker-1")
    assert cluster.exit_code("worker-1") == 137
    cluster.command("start", "worker-2")
    cluster.wait(lambda: cluster.terminal(job))
    attempts = cluster.rows(
        "SELECT generation,owner,lease_token,outcome FROM job_attempts ORDER BY generation"
    )
    assert [item["outcome"] for item in attempts] == ["lease_expired", "succeeded"]
    assert [item["generation"] for item in attempts] == [1, 2]
    assert attempts[0]["owner"] != attempts[1]["owner"]
    assert attempts[0]["lease_token"] != attempts[1]["lease_token"]
    cluster.evidence(
        "sigkill",
        job_id=job,
        outcomes=[item["outcome"] for item in attempts],
        generations=[1, 2],
        replacement_owner=attempts[1]["owner"],
    )


def test_grace_budget_kills_child_and_leaves_lease_for_recovery(cluster: Cluster) -> None:
    cluster.only_first_worker()
    tenant = cluster.repo.create_tenant("forced-drain")
    job = cluster.repo.submit(
        tenant,
        "long",
        JobSpec(job_type="synthetic_report", max_attempts=1, payload={"delay_s": 30}),
    )
    cluster.wait(lambda: cluster.job_started(job))
    cluster.command("stop", "--timeout", "12", "worker-1")
    assert cluster.exit_code("worker-1") == 0
    assert cluster.scalar("SELECT state FROM jobs WHERE id=:id", id=job) == "running"
    cluster.command("start", "worker-2")
    cluster.wait(lambda: cluster.terminal(job))
    assert cluster.scalar("SELECT state FROM jobs WHERE id=:id", id=job) == "dead_letter"
    assert cluster.scalar("SELECT outcome FROM job_attempts") == "lease_expired"
    assert any(
        entry.get("event") == "job_abandoned" and entry.get("reason") == "worker_shutdown"
        for entry in cluster.logs()
    )
    cluster.evidence("forced-drain", job_id=job, outcome="lease_expired", final_state="dead_letter")


def test_retries_timeouts_and_invalid_handlers(cluster: Cluster) -> None:
    tenant = cluster.repo.create_tenant("failures")
    retry = cluster.repo.submit(
        tenant,
        "retry",
        JobSpec(
            job_type="notification", payload={"recipient": "local", "transient_until_attempt": 1}
        ),
    )
    timeout = cluster.repo.submit(
        tenant,
        "timeout",
        JobSpec(
            job_type="synthetic_report",
            payload={"delay_s": 10},
            execution_timeout_s=2,
            max_attempts=1,
        ),
    )
    invalid = cluster.repo.submit(
        tenant,
        "invalid",
        JobSpec(job_type="shell.command", payload={"code": "PAYLOAD_MUST_NOT_APPEAR"}),
    )
    cluster.wait(lambda: all(cluster.terminal(job) for job in (retry, timeout, invalid)))
    assert cluster.scalar("SELECT state FROM jobs WHERE id=:id", id=retry) == "succeeded"
    assert cluster.scalar("SELECT attempts FROM jobs WHERE id=:id", id=retry) == 2
    assert cluster.scalar("SELECT state FROM jobs WHERE id=:id", id=timeout) == "dead_letter"
    assert cluster.scalar("SELECT state FROM jobs WHERE id=:id", id=invalid) == "dead_letter"
    assert (
        cluster.scalar(
            """
        SELECT execution_started_at FROM job_attempts WHERE job_id=:id
    """,
            id=invalid,
        )
        is None
    )
    cluster.evidence(
        "retry-timeout-allowlist",
        retry_job=retry,
        timeout_job=timeout,
        disallowed_job=invalid,
        retry_attempts=2,
    )


def test_database_disconnect_abandons_then_recovers(cluster: Cluster) -> None:
    cluster.only_first_worker()
    tenant = cluster.repo.create_tenant("disconnect")
    job = cluster.repo.submit(
        tenant, "disconnect", JobSpec(job_type="synthetic_report", payload={"delay_s": 8})
    )
    cluster.wait(lambda: cluster.job_started(job))
    cluster.command("stop", "--timeout", "3", "postgres")
    cluster.wait(
        lambda: any(entry.get("event") == "job_abandoned" for entry in cluster.logs()), timeout=15
    )
    assert "chronos.worker.execute" not in cluster.command("top", "worker-1")
    cluster.command("up", "-d", "--wait", "postgres")
    cluster.engine.dispose()
    cluster.wait(lambda: cluster.terminal(job), timeout=50)
    assert cluster.scalar("SELECT state FROM jobs WHERE id=:id", id=job) == "succeeded"
    assert cluster.scalar("SELECT attempts FROM jobs WHERE id=:id", id=job) == 2
    outcomes = cluster.rows("SELECT generation,outcome FROM job_attempts ORDER BY generation")
    assert [item["outcome"] for item in outcomes] == ["lease_expired", "succeeded"]
    cluster.evidence(
        "database-disconnect", job_id=job, outcomes=outcomes, child_absent_during_outage=True
    )
