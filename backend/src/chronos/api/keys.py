"""Trusted local credential bootstrap. Never print bearer tokens to logs/chat."""

import argparse
import os
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import make_url

from chronos.api.security import ApiKeys
from chronos.api.settings import ApiSettings
from chronos.engine.repository import QueueRepository, create_queue_engine


def main() -> None:
    parser = argparse.ArgumentParser(description="Issue a tenant-scoped recovery API credential")
    parser.add_argument("--tenant", type=UUID, required=True)
    parser.add_argument("--role", choices=["viewer", "operator", "admin"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ttl", type=int, default=3600)
    args = parser.parse_args()
    settings = ApiSettings()
    if not settings.database_url or not settings.auth_pepper:
        parser.error("set CHRONOS_DATABASE_URL and CHRONOS_API_AUTH_PEPPER")
    engine = create_queue_engine(
        make_url(settings.database_url.get_secret_value()).set(drivername="postgresql+psycopg")
    )
    try:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            key_id, token = ApiKeys(
                QueueRepository(engine), settings.auth_pepper.get_secret_value()
            ).issue(
                args.tenant,
                cast(Literal["viewer", "operator", "admin"], args.role),
                ttl_seconds=args.ttl,
            )
            try:
                stream.write(token + "\n")
                stream.flush()
            except OSError:
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE api_keys SET revoked_at=clock_timestamp() WHERE id=:id"),
                        {"id": key_id},
                    )
                raise
        print(f"Credential {key_id} written to the requested private file; token omitted.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
