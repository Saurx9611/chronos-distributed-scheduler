"""High-entropy tenant-scoped bearer credentials, HMAC hashes and DB rate limits."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import text

from chronos.engine.repository import QueueRepository


@dataclass(frozen=True)
class Principal:
    key_id: UUID
    tenant_id: UUID
    role: str


class RateLimited(Exception):
    pass


class ApiKeys:
    def __init__(self, repository: QueueRepository, pepper: str, limit: int = 60) -> None:
        if len(pepper) < 32 or not 1 <= limit <= 1000:
            raise ValueError("strong API pepper and bounded rate limit required")
        self.repo, self.pepper, self.limit = repository, pepper.encode(), limit

    def digest(self, token: str) -> str:
        return hmac.new(self.pepper, token.encode(), hashlib.sha256).hexdigest()

    def issue(
        self, tenant: UUID, role: Literal["viewer", "operator", "admin"], *, ttl_seconds: int = 3600
    ) -> tuple[UUID, str]:
        if not 1 <= ttl_seconds <= 2592000:
            raise ValueError("credential TTL must be 1 second..30 days")
        token = secrets.token_urlsafe(32)
        with self.repo._transaction() as conn:
            key_id: UUID = conn.execute(
                text("""
                INSERT INTO api_keys(tenant_id,token_hash,role,expires_at)
                VALUES (:tenant,:hash,:role,clock_timestamp()+make_interval(secs=>:ttl))
                    RETURNING id
            """),
                {"tenant": tenant, "hash": self.digest(token), "role": role, "ttl": ttl_seconds},
            ).scalar_one()
            conn.execute(
                text("""
                INSERT INTO audit_events(tenant_id,entity_id,entity_type,action,actor_id,reason)
                VALUES (:tenant,:tenant,'tenant','api_key_created',:actor,'operator_key_issue')
            """),
                {"tenant": tenant, "actor": str(key_id)},
            )
        return key_id, token

    def authenticate(self, token: str) -> Principal | None:
        if not 32 <= len(token) <= 256:
            return None
        with self.repo._transaction() as conn:
            conn.execute(
                text("SELECT id FROM api_keys WHERE token_hash=:hash FOR SHARE"),
                {"hash": self.digest(token)},
            )
            row = (
                conn.execute(
                    text("""
                SELECT id,tenant_id,role FROM api_keys WHERE token_hash=:hash
                    AND revoked_at IS NULL AND expires_at>clock_timestamp()
            """),
                    {"hash": self.digest(token)},
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            count: int = conn.execute(
                text("""
                INSERT INTO api_rate_windows(api_key_id,window_start,requests)
                VALUES (:id,date_trunc('minute',clock_timestamp()),1)
                ON CONFLICT(api_key_id,window_start) DO UPDATE
                    SET requests=LEAST(api_rate_windows.requests+1,:ceiling) RETURNING requests
            """),
                {"id": row["id"], "ceiling": self.limit + 1},
            ).scalar_one()
            limited = count > self.limit
            principal = Principal(row["id"], row["tenant_id"], row["role"])
        # Commit the counter even when rejecting. No process-local limiter race.
        if limited:
            raise RateLimited()
        return principal
