"""Short, DB-only transactions. A returned claim has already committed."""

import hashlib
import json
import random
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, RowMapping, create_engine, text
from sqlalchemy.engine import URL

from chronos.engine.models import (
    Claim,
    IdempotencyConflict,
    JobSpec,
    LeaseWindow,
    OwnershipLost,
    Worker,
    WorkerExpired,
)
from chronos.engine.sql import CLAIM_SQL, OWNER_PREDICATE


def create_queue_engine(url: str | URL, *, pool_size: int = 5) -> Engine:
    return create_engine(
        url,
        isolation_level="READ COMMITTED",
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=5,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


class QueueRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @contextmanager
    def _transaction(self) -> Iterator[Connection]:
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL READ COMMITTED;"
                "SET LOCAL statement_timeout = '5s';"
                "SET LOCAL lock_timeout = '2s';"
                "SET LOCAL idle_in_transaction_session_timeout = '5s'"
                ";SET LOCAL TIME ZONE 'UTC'"
            )
            yield conn

    @staticmethod
    def _event(conn: Connection, job: RowMapping, action: str, actor: str) -> None:
        params = {
            "job": job["id"],
            "tenant": job["tenant_id"],
            "generation": job["generation"],
            "action": action,
            "actor": actor,
        }
        conn.execute(
            text("""
            INSERT INTO job_events(job_id, generation, event_type, actor_id)
            VALUES (:job, :generation, :action, :actor)
        """),
            params,
        )
        conn.execute(
            text("""
            INSERT INTO audit_events(tenant_id, entity_id, entity_type, action, actor_id)
            VALUES (:tenant, :job, 'job', :action, :actor)
        """),
            params,
        )

    def create_tenant(self, name: str) -> UUID:
        with self._transaction() as conn:
            value: UUID = conn.execute(
                text("INSERT INTO tenants(name) VALUES (:name) RETURNING id"), {"name": name}
            ).scalar_one()
        return UUID(str(value))

    def register_worker(self, name: str, *, ttl_seconds: int = 60) -> Worker:
        self._duration(ttl_seconds, 3600)
        with self._transaction() as conn:
            row = (
                conn.execute(
                    text("""
                INSERT INTO worker_registrations(name, heartbeat_expires_at)
                VALUES (:name, clock_timestamp() + make_interval(secs => :ttl))
                RETURNING id, heartbeat_token
            """),
                    {"name": name, "ttl": ttl_seconds},
                )
                .mappings()
                .one()
            )
            conn.execute(
                text("""
                INSERT INTO audit_events(entity_id,entity_type,action,actor_id)
                VALUES (:id, 'worker', 'registered', :actor)
            """),
                {"id": row["id"], "actor": str(row["id"])},
            )
            worker = Worker(row["id"], row["heartbeat_token"])
        return worker

    def heartbeat_worker(self, worker: Worker, *, ttl_seconds: int = 60) -> None:
        self._duration(ttl_seconds, 3600)
        with self._transaction() as conn:
            conn.execute(
                text("SELECT id FROM worker_registrations WHERE id=:id FOR UPDATE"),
                {"id": worker.id},
            )
            row = conn.execute(
                text("""
                UPDATE worker_registrations SET heartbeat_at=clock_timestamp(),
                    heartbeat_expires_at=clock_timestamp() + make_interval(secs => :ttl)
                WHERE id=:id AND heartbeat_token=:token
                  AND heartbeat_expires_at > clock_timestamp()
                  AND state IN ('active','draining')
                RETURNING id
            """),
                {"id": worker.id, "token": worker.heartbeat_token, "ttl": ttl_seconds},
            ).first()
            if row is None:
                raise WorkerExpired()

    def submit(self, tenant_id: UUID, key: str, spec: JobSpec) -> UUID:
        if not 1 <= len(key.encode()) <= 128:
            raise ValueError("idempotency key must be 1..128 bytes")
        canonical = json.dumps(
            spec.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        # Use the serialized snapshot for persistence as well as hashing: nested
        # Pydantic payload dictionaries can otherwise be mutated by the caller.
        payload_snapshot = json.loads(canonical)["payload"]
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        job_id = uuid4()
        with self._transaction() as conn:
            inserted = conn.execute(
                text("""
                INSERT INTO submission_keys(tenant_id,key,request_hash,job_id)
                VALUES (:tenant,:key,:hash,:job)
                ON CONFLICT (tenant_id,key) DO NOTHING RETURNING job_id
            """),
                {"tenant": tenant_id, "key": key, "hash": digest, "job": job_id},
            ).first()
            if inserted is None:
                existing = (
                    conn.execute(
                        text("""
                    SELECT job_id,request_hash FROM submission_keys
                    WHERE tenant_id=:tenant AND key=:key
                """),
                        {"tenant": tenant_id, "key": key},
                    )
                    .mappings()
                    .one()
                )
                if existing["request_hash"] != digest:
                    raise IdempotencyConflict()
                return UUID(str(existing["job_id"]))
            job = (
                conn.execute(
                    text("""
                INSERT INTO jobs(id,tenant_id,queue,job_type,payload,priority,
                    scheduled_at,available_at,max_attempts,execution_timeout_s)
                VALUES (:id,:tenant,:queue,:type,CAST(:payload AS jsonb),:priority,
                    COALESCE(:scheduled,statement_timestamp()),
                    COALESCE(:scheduled,statement_timestamp()),:max_attempts,:timeout)
                RETURNING *
            """),
                    {
                        "id": job_id,
                        "tenant": tenant_id,
                        "queue": spec.queue,
                        "type": spec.job_type,
                        "payload": json.dumps(payload_snapshot, allow_nan=False),
                        "priority": spec.priority,
                        "scheduled": spec.scheduled_at,
                        "max_attempts": spec.max_attempts,
                        "timeout": spec.execution_timeout_s,
                    },
                )
                .mappings()
                .one()
            )
            self._event(conn, job, "submitted", str(tenant_id))
        return job_id

    def claim(
        self, worker: Worker, *, queue: str = "default", limit: int = 1, lease_seconds: int = 30
    ) -> list[Claim]:
        self._duration(lease_seconds, 300)
        self._duration(limit, 100)
        with self._transaction() as conn:
            # Worker row precedes job locks; registration operations never lock jobs.
            conn.execute(
                text("SELECT id FROM worker_registrations WHERE id=:id FOR SHARE"),
                {"id": worker.id},
            )
            live = conn.execute(
                text("""
                SELECT id FROM worker_registrations
                WHERE id=:id AND heartbeat_token=:token
                  AND heartbeat_expires_at > clock_timestamp()
                  AND state='active'
            """),
                {"id": worker.id, "token": worker.heartbeat_token},
            ).first()
            if live is None:
                raise WorkerExpired()
            rows = (
                conn.execute(
                    text(CLAIM_SQL),
                    {
                        "queue": queue,
                        "limit": limit,
                        "lease_seconds": lease_seconds,
                        "worker_id": worker.id,
                    },
                )
                .mappings()
                .all()
            )
            claims = []
            for job in rows:
                conn.execute(
                    text("""
                    INSERT INTO job_attempts(job_id,generation,lease_token,owner,started_at)
                    VALUES (:id,:generation,:token,:worker,:started)
                """),
                    {
                        "id": job["id"],
                        "generation": job["generation"],
                        "token": job["lease_token"],
                        "worker": worker.id,
                        "started": job["attempt_started_at"],
                    },
                )
                self._event(conn, job, "claimed", str(worker.id))
                claims.append(
                    Claim(
                        job["id"],
                        job["tenant_id"],
                        worker.id,
                        job["lease_token"],
                        job["generation"],
                        job["job_type"],
                        job["payload"],
                        job["lease_expires_at"],
                    )
                )
        return claims

    @staticmethod
    def _duration(value: int, maximum: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"value must be an integer in 1..{maximum}")

    @staticmethod
    def _owner_params(claim: Claim) -> dict[str, Any]:
        return {
            "job_id": claim.job_id,
            "worker_id": claim.worker_id,
            "token": claim.token,
            "generation": claim.generation,
        }

    @staticmethod
    def _lock(conn: Connection, job_id: UUID) -> None:
        # This is a separate command: expiry is evaluated only after the lock is held.
        conn.execute(text("SELECT id FROM jobs WHERE id=:id FOR UPDATE"), {"id": job_id})

    def renew(self, claim: Claim, *, lease_seconds: int = 30) -> LeaseWindow:
        self._duration(lease_seconds, 300)
        with self._transaction() as conn:
            self._lock(conn, claim.job_id)
            job = (
                conn.execute(
                    text(
                        """
                UPDATE jobs SET lease_expires_at=LEAST(
                    clock_timestamp()+make_interval(secs => :seconds),execution_deadline),
                    updated_at=clock_timestamp()
                WHERE """
                        + OWNER_PREDICATE
                        + """ RETURNING *,
                        EXTRACT(EPOCH FROM lease_expires_at-clock_timestamp()) AS lease_remaining,
                        EXTRACT(EPOCH FROM execution_deadline-clock_timestamp())
                            AS execution_remaining
                        """
                    ),
                    self._owner_params(claim) | {"seconds": lease_seconds},
                )
                .mappings()
                .first()
            )
            if job is None:
                raise OwnershipLost()
            self._event(conn, job, "renewed", str(claim.worker_id))
            window = LeaseWindow(float(job["lease_remaining"]), float(job["execution_remaining"]))
        return window

    @staticmethod
    def _close_attempt(
        conn: Connection,
        job: RowMapping,
        outcome: str,
        error_code: str | None = None,
        result: str | None = None,
    ) -> None:
        changed = conn.execute(
            text("""
            UPDATE job_attempts SET outcome=:outcome,finished_at=clock_timestamp(),
                error_code=:error, result=CAST(:result AS jsonb)
                WHERE job_id=:job AND generation=:generation
                AND outcome IS NULL RETURNING job_id
        """),
            {
                "job": job["id"],
                "generation": job["generation"],
                "outcome": outcome,
                "error": error_code,
                "result": result,
            },
        ).first()
        if changed is None:
            raise RuntimeError("attempt invariant violated")

    def acknowledge(self, claim: Claim, *, result: dict[str, Any] | None = None) -> None:
        encoded = (
            json.dumps(result, allow_nan=False, ensure_ascii=False) if result is not None else None
        )
        if encoded is not None and len(encoded.encode()) > 65536:
            raise ValueError("result exceeds 64 KiB")
        self._finish(claim, mode="success", result=encoded)

    def fail(
        self, claim: Claim, *, retryable: bool = True, error_code: str = "handler_error"
    ) -> None:
        if not 1 <= len(error_code) <= 100 or not all(c.isalnum() or c in "_-" for c in error_code):
            raise ValueError("error_code must be a bounded identifier, never raw exception text")
        self._finish(claim, mode="retry" if retryable else "permanent", error_code=error_code)

    def acknowledge_cancellation(self, claim: Claim) -> None:
        self._finish(claim, mode="cancel")

    def _finish(
        self, claim: Claim, *, mode: str, error_code: str | None = None, result: str | None = None
    ) -> None:
        with self._transaction() as conn:
            self._lock(conn, claim.job_id)
            predicate = OWNER_PREDICATE
            if mode == "cancel":
                predicate = predicate.replace("state = 'running'", "state = 'cancel_requested'")
            current = (
                conn.execute(
                    text("SELECT * FROM jobs WHERE " + predicate), self._owner_params(claim)
                )
                .mappings()
                .first()
            )
            if current is None:
                raise OwnershipLost()
            if mode == "success":
                state, outcome = "succeeded", "succeeded"
            elif mode == "cancel":
                state, outcome = "cancelled", "cancelled"
            else:
                state = (
                    "retry_wait"
                    if mode == "retry" and (current["attempts"] < current["max_attempts"])
                    else "dead_letter"
                )
                outcome = "retryable_error" if mode == "retry" else "permanent_error"
            delay = random.uniform(0, min(300, 2 ** (current["attempts"] - 1)))
            job = (
                conn.execute(
                    text(
                        """
                UPDATE jobs SET state=:state, lease_token=NULL, lease_owner=NULL,
                    lease_expires_at=NULL, execution_deadline=NULL, updated_at=clock_timestamp(),
                    available_at=CASE WHEN :state='retry_wait'
                        THEN clock_timestamp()+make_interval(secs => :delay)
                            ELSE available_at END,
                    finished_at=CASE WHEN :state='retry_wait'
                            THEN NULL ELSE clock_timestamp() END
                WHERE """
                        + predicate
                        + " RETURNING *"
                    ),
                    self._owner_params(claim) | {"state": state, "delay": delay},
                )
                .mappings()
                .first()
            )
            if job is None:
                raise OwnershipLost()
            self._close_attempt(conn, job, outcome, error_code, result)
            self._event(conn, job, state, str(claim.worker_id))

    def cancel(self, tenant_id: UUID, job_id: UUID, *, actor: str) -> str:
        with self._transaction() as conn:
            current = (
                conn.execute(
                    text("""
                SELECT * FROM jobs WHERE id=:id AND tenant_id=:tenant FOR UPDATE
            """),
                    {"id": job_id, "tenant": tenant_id},
                )
                .mappings()
                .one()
            )
            state = str(current["state"])
            if state in ("queued", "retry_wait", "running"):
                state = "cancel_requested" if state == "running" else "cancelled"
                job = (
                    conn.execute(
                        text("""
                    UPDATE jobs SET state=:state,updated_at=clock_timestamp(),
                        finished_at=CASE WHEN :state='cancelled'
                            THEN clock_timestamp() ELSE NULL END
                    WHERE id=:id RETURNING *
                """),
                        {"id": job_id, "state": state},
                    )
                    .mappings()
                    .one()
                )
                self._event(conn, job, state, actor)
        return state

    def recover_expired(self, *, limit: int = 100) -> int:
        self._duration(limit, 100)
        recovered = 0
        with self._transaction() as conn:
            rows = (
                conn.execute(
                    text("""
                SELECT * FROM jobs WHERE state IN ('running','cancel_requested')
                  AND lease_expires_at <= clock_timestamp()
                ORDER BY id LIMIT :limit FOR UPDATE SKIP LOCKED
            """),
                    {"limit": limit},
                )
                .mappings()
                .all()
            )
            for old in rows:
                state = (
                    "cancelled"
                    if old["state"] == "cancel_requested"
                    else "retry_wait"
                    if old["attempts"] < old["max_attempts"]
                    else "dead_letter"
                )
                delay = random.uniform(0, min(300, 2 ** (old["attempts"] - 1)))
                job = (
                    conn.execute(
                        text("""
                    UPDATE jobs SET state=:state,lease_token=NULL,lease_owner=NULL,
                        lease_expires_at=NULL,execution_deadline=NULL,updated_at=clock_timestamp(),
                        available_at=CASE WHEN :state='retry_wait'
                            THEN clock_timestamp()+make_interval(secs => :delay)
                            ELSE available_at END,
                        finished_at=CASE WHEN :state='retry_wait'
                            THEN NULL ELSE clock_timestamp() END
                    WHERE id=:id AND generation=:generation AND lease_token=:token
                      AND state IN ('running','cancel_requested')
                      AND lease_expires_at <= clock_timestamp() RETURNING *
                """),
                        {
                            "state": state,
                            "id": old["id"],
                            "generation": old["generation"],
                            "token": old["lease_token"],
                            "delay": delay,
                        },
                    )
                    .mappings()
                    .first()
                )
                if job is not None:
                    self._close_attempt(
                        conn, job, "cancelled" if state == "cancelled" else "lease_expired"
                    )
                    self._event(conn, job, "lease_expired", "recovery")
                    recovered += 1
        return recovered

    def execution_started(self, claim: Claim) -> None:
        with self._transaction() as conn:
            self._lock(conn, claim.job_id)
            job = (
                conn.execute(
                    text("SELECT * FROM jobs WHERE " + OWNER_PREDICATE), self._owner_params(claim)
                )
                .mappings()
                .first()
            )
            if job is None:
                raise OwnershipLost()
            changed = conn.execute(
                text("""
                UPDATE job_attempts SET execution_started_at=clock_timestamp()
                WHERE job_id=:job_id AND generation=:generation
                    AND outcome IS NULL AND execution_started_at IS NULL RETURNING job_id
            """),
                self._owner_params(claim),
            ).first()
            if changed is None:
                raise RuntimeError("attempt already started")
            self._event(conn, job, "execution_started", str(claim.worker_id))

    def worker_state(self, worker: Worker, state: str) -> None:
        if state not in ("draining", "stopped"):
            raise ValueError("invalid worker lifecycle transition")
        with self._transaction() as conn:
            changed = conn.execute(
                text("""
                UPDATE worker_registrations SET state=:state
                WHERE id=:id AND heartbeat_token=:token
                  AND (state='active' OR (state='draining' AND :state='stopped'))
                RETURNING id
            """),
                {"id": worker.id, "token": worker.heartbeat_token, "state": state},
            ).first()
            if changed is not None:
                conn.execute(
                    text("""
                    INSERT INTO audit_events(entity_id,entity_type,action,actor_id)
                    VALUES (:id,'worker',:action,:actor)
                """),
                    {"id": worker.id, "action": state, "actor": str(worker.id)},
                )
