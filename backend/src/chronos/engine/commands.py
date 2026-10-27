"""Explicit operator commands create new identities; terminal attempts never reopen."""

import hashlib
import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from chronos.engine.models import IdempotencyConflict
from chronos.engine.repository import QueueRepository


class CommandOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int | None = Field(default=None, ge=1, le=10, strict=True)
    retry_base_seconds: float | None = Field(default=None, gt=0, le=60, allow_inf_nan=False)
    retry_cap_seconds: float | None = Field(default=None, gt=0, le=3600, allow_inf_nan=False)

    @model_validator(mode="after")
    def bounds(self) -> CommandOptions:
        if (
            self.retry_base_seconds is not None
            and self.retry_cap_seconds is not None
            and self.retry_cap_seconds < self.retry_base_seconds
        ):
            raise ValueError("retry cap must be >= base")
        return self


class JobNotFound(Exception):
    pass


class InvalidJobCommand(Exception):
    pass


class JobCommands:
    def __init__(self, repository: QueueRepository) -> None:
        self.repo = repository

    def create(
        self,
        tenant_id: UUID,
        source_id: UUID,
        action: Literal["retry", "replay"],
        key: str,
        options: CommandOptions,
        *,
        actor: str,
    ) -> UUID:
        if not 1 <= len(key.encode()) <= 128:
            raise ValueError("idempotency key must be 1..128 bytes")
        with self.repo._transaction() as conn:
            source = (
                conn.execute(
                    text("""
                SELECT * FROM jobs WHERE tenant_id=:tenant AND id=:id FOR SHARE
            """),
                    {"tenant": tenant_id, "id": source_id},
                )
                .mappings()
                .first()
            )
            if source is None:
                raise JobNotFound()
            allowed = (
                {"dead_letter"} if action == "retry" else {"dead_letter", "succeeded", "cancelled"}
            )
            if source["state"] not in allowed:
                raise InvalidJobCommand()
            attempts = options.max_attempts or source["max_attempts"]
            base = options.retry_base_seconds or source["retry_base_seconds"]
            cap = options.retry_cap_seconds or source["retry_cap_seconds"]
            if cap < base:
                raise ValueError("effective retry cap must be >= base")
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "action": action,
                        "source": str(source_id),
                        "attempts": attempts,
                        "base": base,
                        "cap": cap,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            new_id = uuid4()
            params = {
                "tenant": tenant_id,
                "key": key,
                "source": source_id,
                "action": action,
                "hash": digest,
                "id": new_id,
            }
            reserved = conn.execute(
                text("""
                INSERT INTO job_command_keys(
                    tenant_id,key,action,source_job_id,new_job_id,request_hash)
                VALUES (:tenant,:key,:action,:source,:id,:hash)
                ON CONFLICT(tenant_id,key) DO NOTHING RETURNING new_job_id
            """),
                params,
            ).first()
            if reserved is None:
                prior = (
                    conn.execute(
                        text("""
                    SELECT request_hash,new_job_id FROM job_command_keys
                    WHERE tenant_id=:tenant AND key=:key
                """),
                        params,
                    )
                    .mappings()
                    .one()
                )
                if prior["request_hash"] != digest:
                    raise IdempotencyConflict()
                return UUID(str(prior["new_job_id"]))
            job = (
                conn.execute(
                    text("""
                INSERT INTO jobs(id,tenant_id,queue,job_type,payload,priority,max_attempts,
                    execution_timeout_s,replay_of,effect_key,retry_base_seconds,retry_cap_seconds)
                SELECT :id,tenant_id,queue,job_type,payload,priority,:attempts,execution_timeout_s,
                    id,:effect,:base,:cap FROM jobs WHERE tenant_id=:tenant AND id=:source
                RETURNING *
            """),
                    params
                    | {
                        "attempts": attempts,
                        "base": base,
                        "cap": cap,
                        "effect": source["effect_key"] if action == "retry" else new_id,
                    },
                )
                .mappings()
                .one()
            )
            self.repo._event(conn, job, "submitted", actor, "operator_" + action)
            self.repo._event(
                conn, source, action + "_created", actor, "operator_" + action, source["state"]
            )
        return new_id
