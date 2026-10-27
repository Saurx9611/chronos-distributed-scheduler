from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

BUILTIN_HANDLERS = frozenset(
    {"image_metadata", "csv_transform", "synthetic_report", "notification", "demo_effect"}
)


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHRONOS_WORKER_", extra="ignore", hide_input_in_errors=True
    )
    database_url: SecretStr | None = Field(default=None, validation_alias="CHRONOS_DATABASE_URL")
    postgres_host: str = Field(default="localhost", validation_alias="PGHOST")
    postgres_port: int = Field(default=5432, validation_alias="PGPORT", ge=1, le=65535)
    postgres_user: str = Field(default="chronos", validation_alias="PGUSER")
    postgres_db: str = Field(default="chronos", validation_alias="PGDATABASE")
    postgres_password: SecretStr | None = Field(default=None, validation_alias="PGPASSWORD")
    name: str = Field(default="chronos-worker", min_length=1, max_length=200)
    queue: str = Field(default="default", min_length=1, max_length=100)
    allowed_handlers: str = "image_metadata,csv_transform,synthetic_report,notification"
    concurrency: int = Field(default=4, ge=1, le=32)
    lease_seconds: int = Field(default=30, ge=3, le=300)
    renew_interval_s: float = Field(default=5, ge=0.1, le=60)
    heartbeat_interval_s: float = Field(default=5, ge=0.1, le=60)
    worker_ttl_seconds: int = Field(default=60, ge=3, le=3600)
    poll_interval_s: float = Field(default=0.5, ge=0.05, le=10)
    recovery_interval_s: float = Field(default=2, ge=0.1, le=30)
    shutdown_grace_s: float = Field(default=20, ge=0, le=120)
    db_call_timeout_s: float = Field(default=2, ge=0.1, le=5)

    @property
    def allowlist(self) -> frozenset[str]:
        return frozenset(part.strip() for part in self.allowed_handlers.split(",") if part.strip())

    @property
    def connection_url(self) -> URL:
        if self.database_url is not None:
            url = make_url(self.database_url.get_secret_value())
            if url.get_backend_name() != "postgresql":
                raise ValueError("worker requires PostgreSQL")
            return url.set(drivername="postgresql+psycopg")
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value() if self.postgres_password else None,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @model_validator(mode="after")
    def safe_intervals(self) -> WorkerSettings:
        if self.database_url is None and self.postgres_password is None:
            raise ValueError("set CHRONOS_DATABASE_URL or PostgreSQL connection environment")
        if not self.allowlist or not self.allowlist <= BUILTIN_HANDLERS:
            raise ValueError("allowlist must contain only built-in handlers")
        if self.renew_interval_s > self.lease_seconds / 3:
            raise ValueError("renew interval must be <= one third of lease")
        if self.heartbeat_interval_s > self.worker_ttl_seconds / 3:
            raise ValueError("heartbeat interval must be <= one third of registration TTL")
        if self.db_call_timeout_s >= self.lease_seconds - 0.5:
            raise ValueError("DB call timeout must leave lease safety margin")
        return self
