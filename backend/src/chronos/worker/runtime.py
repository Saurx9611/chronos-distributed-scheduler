"""Independent poller with bounded execution and separate DB control/lease budgets."""

import asyncio
import contextlib
import os
import random
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from chronos.engine.models import Worker, WorkerExpired
from chronos.engine.repository import QueueRepository, create_queue_engine
from chronos.worker.execution import Execution, db_call
from chronos.worker.logging import emit
from chronos.worker.settings import WorkerSettings


class WorkerRuntime:
    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self.control_engine = create_queue_engine(settings.connection_url, pool_size=2)
        self.lease_engine = create_queue_engine(
            settings.connection_url, pool_size=settings.concurrency
        )
        self.control_repo = QueueRepository(self.control_engine)
        self.lease_repo = QueueRepository(self.lease_engine)
        self.control_threads = ThreadPoolExecutor(max_workers=2, thread_name_prefix="control")
        self.lease_threads = ThreadPoolExecutor(
            max_workers=settings.concurrency, thread_name_prefix="lease"
        )
        self.stop = asyncio.Event()
        self.worker: Worker | None = None
        self.running: set[asyncio.Task[None]] = set()

    def request_stop(self) -> None:
        if not self.stop.is_set():
            emit("worker_draining", worker_id=str(self.worker.id) if self.worker else None)
        self.stop.set()

    def task_finished(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            emit("supervision_failed", error_class=type(task.exception()).__name__)
            self.request_stop()

    def check_schema(self) -> None:
        with self.control_engine.connect() as conn:
            if conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() != "0002":
                raise RuntimeError("worker requires migration 0002")

    async def pause(self, duration: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.stop.wait(), timeout=duration)

    async def run(self) -> None:
        failures = 0
        heartbeat_due = recovery_due = 0.0
        try:
            while not self.stop.is_set():
                try:
                    now = asyncio.get_running_loop().time()
                    if self.worker is None:
                        await db_call(
                            self.control_threads, self.settings.db_call_timeout_s, self.check_schema
                        )
                        self.worker = await db_call(
                            self.control_threads,
                            self.settings.db_call_timeout_s,
                            self.control_repo.register_worker,
                            self.settings.name,
                            ttl_seconds=self.settings.worker_ttl_seconds,
                        )
                        emit(
                            "worker_registered",
                            worker_id=str(self.worker.id),
                            pid=os.getpid(),
                            name=self.settings.name,
                            concurrency=self.settings.concurrency,
                        )
                        heartbeat_due = 0
                    if now >= heartbeat_due:
                        await db_call(
                            self.control_threads,
                            self.settings.db_call_timeout_s,
                            self.control_repo.heartbeat_worker,
                            self.worker,
                            ttl_seconds=self.settings.worker_ttl_seconds,
                        )
                        heartbeat_due = now + self.settings.heartbeat_interval_s
                        emit("worker_heartbeat", worker_id=str(self.worker.id))
                    if now >= recovery_due:
                        count = await db_call(
                            self.control_threads,
                            self.settings.db_call_timeout_s,
                            self.control_repo.recover_expired,
                        )
                        recovery_due = now + self.settings.recovery_interval_s
                        if count:
                            emit("leases_recovered", worker_id=str(self.worker.id), count=count)
                    self.running = {task for task in self.running if not task.done()}
                    capacity = self.settings.concurrency - len(self.running)
                    if capacity:
                        claims = await db_call(
                            self.control_threads,
                            self.settings.db_call_timeout_s,
                            self.control_repo.claim,
                            self.worker,
                            queue=self.settings.queue,
                            limit=capacity,
                            lease_seconds=self.settings.lease_seconds,
                        )
                        for claim in claims:
                            emit(
                                "job_claimed",
                                job_id=str(claim.job_id),
                                worker_id=str(claim.worker_id),
                                generation=claim.generation,
                                job_type=claim.job_type,
                            )
                            if not self.stop.is_set():
                                task = asyncio.create_task(
                                    Execution(
                                        self.lease_repo, self.settings, self.lease_threads, claim
                                    ).run()
                                )
                                task.add_done_callback(self.task_finished)
                                self.running.add(task)
                            else:
                                emit(
                                    "job_abandoned",
                                    job_id=str(claim.job_id),
                                    worker_id=str(claim.worker_id),
                                    generation=claim.generation,
                                    reason="shutdown_before_dispatch",
                                )
                    failures = 0
                    await self.pause(self.settings.poll_interval_s * random.uniform(0.8, 1.2))
                except WorkerExpired:
                    emit("worker_registration_expired")
                    self.worker = None
                except (SQLAlchemyError, TimeoutError) as exc:
                    failures += 1
                    emit("database_unavailable", error_class=type(exc).__name__)
                    # Never replay an ambiguous claim; any unreturned lease expires.
                    await self.pause(random.uniform(0.1, min(5, 0.25 * 2 ** min(failures, 5))))
        finally:
            self.stop.set()
            if self.worker:
                await self.lifecycle("draining")
            if self.running:
                _, pending = await asyncio.wait(
                    self.running, timeout=self.settings.shutdown_grace_s
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*self.running, return_exceptions=True)
            if self.worker:
                await self.lifecycle("stopped")
            # In-flight synchronous DB calls remain bounded by DB/pool/connect timeouts.
            self.control_threads.shutdown(wait=True, cancel_futures=True)
            self.lease_threads.shutdown(wait=True, cancel_futures=True)
            self.control_engine.dispose()
            self.lease_engine.dispose()
            emit("worker_stopped", worker_id=str(self.worker.id) if self.worker else None)

    async def lifecycle(self, state: str) -> None:
        try:
            await db_call(
                self.control_threads,
                self.settings.db_call_timeout_s,
                self.control_repo.worker_state,
                self.worker,
                state,
            )
        except SQLAlchemyError, TimeoutError:
            emit("worker_state_uncertain", state=state)
