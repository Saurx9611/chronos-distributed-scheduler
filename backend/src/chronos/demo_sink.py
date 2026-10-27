"""Opt-in private demonstration destination, committed independently of Chronos DB."""

import os
import secrets
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class Effect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effect_key: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class Gate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: int = Field(ge=1, le=10)
    stage: str = Field(pattern=r"^(after_claim|during_execute|before_ack)$")


def create_sink(
    path: Path, token: str | None, *, faults: bool = False, unsafe: bool = False
) -> FastAPI:
    @contextmanager
    def database() -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(path, timeout=2)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if token:
            path.parent.mkdir(parents=True, exist_ok=True)
            with database() as conn:
                conn.executescript("""
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS effects (
                        id INTEGER PRIMARY KEY, effect_key TEXT NOT NULL,
                        fingerprint TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS deduplication (
                        effect_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                        effect_id INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS requests (
                        id INTEGER PRIMARY KEY, effect_key TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS gates (
                        job_id TEXT NOT NULL, generation INTEGER NOT NULL, stage TEXT NOT NULL,
                        arrived INTEGER NOT NULL DEFAULT 0, released INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(job_id,generation,stage));
                """)
        yield

    app = FastAPI(title="Chronos private effect demo", lifespan=lifespan)

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        if not token:
            raise HTTPException(503, "demo_disabled")
        if not authorization or not secrets.compare_digest(
            authorization.encode(), ("Bearer " + token).encode()
        ):
            raise HTTPException(401, "authentication_required")

    @app.get("/health/live")
    def live() -> dict[str, bool]:
        return {"ready": bool(token)}

    def commit_effect(effect: Effect, deduplicate: bool) -> dict[str, int]:
        key = str(effect.effect_key)
        with database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO requests(effect_key) VALUES (?)", (key,))
            if deduplicate:
                prior = conn.execute(
                    "SELECT fingerprint,effect_id FROM deduplication WHERE effect_key=?", (key,)
                ).fetchone()
                if prior:
                    if prior[0] != effect.fingerprint:
                        raise HTTPException(409, "effect_key_conflict")
                    return {"effect_id": prior[1]}
            effect_id = conn.execute(
                "INSERT INTO effects(effect_key,fingerprint) VALUES (?,?)",
                (key, effect.fingerprint),
            ).lastrowid
            assert effect_id is not None
            if deduplicate:
                conn.execute(
                    "INSERT INTO deduplication(effect_key,fingerprint,effect_id) VALUES (?,?,?)",
                    (key, effect.fingerprint, effect_id),
                )
        return {"effect_id": effect_id}

    @app.post("/effects", dependencies=[Depends(authorize)])
    def deliver(effect: Effect) -> dict[str, int]:
        return commit_effect(effect, True)

    @app.post("/unsafe-effects", dependencies=[Depends(authorize)])
    def unsafe_delivery(effect: Effect) -> dict[str, int]:
        if not unsafe:
            raise HTTPException(403, "unsafe_demo_disabled")
        return commit_effect(effect, False)

    @app.get("/stats/{key}", dependencies=[Depends(authorize)])
    def stats(key: UUID) -> dict[str, int]:
        with database() as conn:
            effects = conn.execute(
                "SELECT count(*) FROM effects WHERE effect_key=?", (str(key),)
            ).fetchone()
            requests = conn.execute(
                "SELECT count(*) FROM requests WHERE effect_key=?", (str(key),)
            ).fetchone()
        return {"effects": effects[0], "requests": requests[0]}

    @app.post("/gates/{job_id}/{action}", dependencies=[Depends(authorize)])
    def gate(job_id: UUID, action: str, gate: Gate) -> dict[str, bool]:
        if not faults or action not in {"arrive", "status", "release"}:
            raise HTTPException(403, "fault_injection_disabled")
        values = (str(job_id), gate.generation, gate.stage)
        with database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO gates(job_id,generation,stage) VALUES (?,?,?)", values
            )
            if action == "arrive":
                conn.execute(
                    "UPDATE gates SET arrived=1 WHERE job_id=? AND generation=? AND stage=?", values
                )
            if action == "release":
                conn.execute(
                    "UPDATE gates SET released=1 WHERE job_id=? AND generation=? AND stage=?",
                    values,
                )
            row = conn.execute(
                "SELECT arrived,released FROM gates WHERE job_id=? AND generation=? AND stage=?",
                values,
            ).fetchone()
        return {"arrived": bool(row[0]), "released": bool(row[1])}

    return app


app = create_sink(
    Path(os.environ.get("CHRONOS_DEMO_SINK_PATH", "/data/effects.sqlite3")),
    os.environ.get("CHRONOS_DEMO_SINK_TOKEN"),
    faults=os.environ.get("CHRONOS_FAULT_INJECTION") == "1",
    unsafe=os.environ.get("CHRONOS_DEMO_ALLOW_UNSAFE") == "1",
)
