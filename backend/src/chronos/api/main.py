"""Scoped recovery commands; no unauthenticated administrative operations."""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from chronos.api.security import ApiKeys, Principal, RateLimited
from chronos.api.settings import ApiSettings
from chronos.engine.commands import CommandOptions, InvalidJobCommand, JobCommands, JobNotFound
from chronos.engine.models import IdempotencyConflict
from chronos.engine.repository import QueueRepository, create_queue_engine


class Liveness(BaseModel):
    status: str


class CommandAccepted(BaseModel):
    job_id: UUID
    source_job_id: UUID
    action: Literal["retry", "replay"]


def create_app(settings: ApiSettings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or ApiSettings()
    owned = engine is None
    if engine is None and settings.database_url:
        engine = create_queue_engine(
            make_url(settings.database_url.get_secret_value()).set(drivername="postgresql+psycopg")
        )
    repo = QueueRepository(engine) if engine else None
    keys = (
        ApiKeys(repo, settings.auth_pepper.get_secret_value(), settings.rate_limit)
        if (repo and settings.auth_pepper)
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if owned and engine:
            engine.dispose()

    app = FastAPI(title="Chronos", version="0.4.0", lifespan=lifespan)
    bearer = HTTPBearer(auto_error=False)

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def error(
        request: Request, status: int, code: str, headers: Mapping[str, str] | None = None
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": code.replace("_", " "),
                    "request_id": request.state.request_id,
                }
            },
            headers=headers,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error(request, exc.status_code, str(exc.detail), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error(request, 422, "invalid_request")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        return error(request, 503, "database_unavailable")

    def principal(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Principal:
        if credentials is None:
            raise HTTPException(
                401, "authentication_required", headers={"WWW-Authenticate": "Bearer"}
            )
        if keys is None:
            raise HTTPException(503, "api_not_configured")
        try:
            identity = keys.authenticate(credentials.credentials)
        except RateLimited:
            raise HTTPException(429, "rate_limited", headers={"Retry-After": "60"}) from None
        if identity is None:
            raise HTTPException(401, "invalid_credentials", headers={"WWW-Authenticate": "Bearer"})
        return identity

    def operator(identity: Annotated[Principal, Depends(principal)]) -> Principal:
        if identity.role not in {"operator", "admin"}:
            raise HTTPException(403, "insufficient_role")
        return identity

    @app.get("/health/live", response_model=Liveness, tags=["health"])
    def live() -> Liveness:
        return Liveness(status="ok")

    def command(
        source: UUID,
        action: Literal["retry", "replay"],
        key: str,
        options: CommandOptions,
        identity: Principal,
    ) -> CommandAccepted:
        assert repo is not None
        try:
            new_id = JobCommands(repo).create(
                identity.tenant_id, source, action, key, options, actor=str(identity.key_id)
            )
        except JobNotFound:
            raise HTTPException(404, "job_not_found") from None
        except InvalidJobCommand:
            raise HTTPException(409, "invalid_job_state") from None
        except IdempotencyConflict:
            raise HTTPException(409, "idempotency_conflict") from None
        except ValueError:
            raise HTTPException(422, "invalid_retry_policy") from None
        return CommandAccepted(job_id=new_id, source_job_id=source, action=action)

    @app.post("/v1/jobs/{job_id}/retry", response_model=CommandAccepted, status_code=201)
    def retry(
        job_id: UUID,
        options: CommandOptions,
        identity: Annotated[Principal, Depends(operator)],
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    ) -> CommandAccepted:
        return command(job_id, "retry", key, options, identity)

    @app.post("/v1/jobs/{job_id}/replay", response_model=CommandAccepted, status_code=201)
    def replay(
        job_id: UUID,
        options: CommandOptions,
        identity: Annotated[Principal, Depends(operator)],
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    ) -> CommandAccepted:
        return command(job_id, "replay", key, options, identity)

    @app.get("/v1/workers")
    def workers(identity: Annotated[Principal, Depends(principal)]) -> list[dict[str, object]]:
        if identity.role != "admin":
            raise HTTPException(403, "insufficient_role")
        assert repo is not None
        with repo._transaction() as conn:
            rows = (
                conn.execute(
                    text("""
                SELECT id,name,state,heartbeat_at,heartbeat_expires_at,
                    state IN ('active','draining')
                        AND heartbeat_expires_at>clock_timestamp() AS is_alive
                FROM worker_registrations ORDER BY registered_at DESC,id LIMIT 100
            """)
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    return app


app = create_app()
