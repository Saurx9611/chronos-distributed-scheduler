"""Validated submission and immutable ownership handles."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, field_validator


class JobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    queue: str = Field(default="default", min_length=1, max_length=100)
    job_type: str = Field(min_length=1, max_length=100)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    priority: int = Field(default=0, ge=-100, le=100, strict=True)
    scheduled_at: AwareDatetime | None = None
    max_attempts: int = Field(default=5, ge=1, le=10, strict=True)
    execution_timeout_s: int = Field(default=900, ge=1, le=900, strict=True)

    @field_validator("scheduled_at")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @field_validator("payload")
    @classmethod
    def bounded_payload(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        import json

        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 65536:
            raise ValueError("payload exceeds 64 KiB")
        return value


@dataclass(frozen=True)
class Worker:
    id: UUID
    heartbeat_token: UUID


@dataclass(frozen=True)
class Claim:
    job_id: UUID
    tenant_id: UUID
    worker_id: UUID
    token: UUID
    generation: int
    job_type: str
    payload: dict[str, JsonValue]
    lease_expires_at: datetime


class OwnershipLost(Exception):
    """The supplied lease is stale, expired, cancelled or otherwise invalid."""


class IdempotencyConflict(Exception):
    """A key was reused with a different canonical request."""


class WorkerExpired(Exception):
    """A worker registration is missing or no longer live."""
