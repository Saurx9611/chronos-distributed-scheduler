"""Phase 1 liveness surface; no job or administrative routes are exposed."""

from fastapi import FastAPI
from pydantic import BaseModel


class Liveness(BaseModel):
    status: str


def create_app() -> FastAPI:
    app = FastAPI(title="Chronos", version="0.1.0")

    @app.get("/health/live", response_model=Liveness, tags=["health"])
    def live() -> Liveness:
        return Liveness(status="ok")

    return app


app = create_app()
