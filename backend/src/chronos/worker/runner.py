"""Bounded IPC with a fixed executable/module; forcibly reap children on cancellation."""

import asyncio
import json
import os
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from chronos.engine.models import Claim
from chronos.worker.logging import emit


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok", "retry", "permanent"]
    result: dict[str, JsonValue] | None = None
    error_code: str = Field(default="handler_exception", pattern=r"^[a-z_]{1,100}$")

    @model_validator(mode="after")
    def valid_result(self) -> Reply:
        if (self.status == "ok") != (self.result is not None):
            raise ValueError("success requires an object result; errors must omit results")
        return self


async def run_handler(claim: Claim) -> Reply:
    # Never forward database credentials or arbitrary environment secrets to handlers.
    child_env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "PYTHONPATH",
            "VIRTUAL_ENV",
            "TMP",
            "TEMP",
            "LANG",
            "HOME",
            "CHRONOS_DEMO_SINK_URL",
            "CHRONOS_DEMO_SINK_TOKEN",
            "CHRONOS_ENABLE_EFFECT_DEMO",
            "CHRONOS_DEMO_ALLOW_UNSAFE",
            "CHRONOS_FAULT_INJECTION",
            "CHRONOS_FAULT_POINT",
            "CHRONOS_FAULT_WORKER",
            "CHRONOS_WORKER_NAME",
        }
    }
    child_env["CHRONOS_EXPECTED_PARENT_PID"] = str(os.getpid())
    child_env["PYTHONUNBUFFERED"] = "1"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "chronos.worker.execute",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=child_env,
        limit=65536,
    )
    emit(
        "handler_spawned",
        job_id=str(claim.job_id),
        worker_id=str(claim.worker_id),
        generation=claim.generation,
        child_pid=process.pid,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        request = json.dumps(
            {
                "job_id": str(claim.job_id),
                "generation": claim.generation,
                "job_type": claim.job_type,
                "payload": claim.payload,
                "effect_key": str(claim.effect_key or claim.job_id),
            },
            allow_nan=False,
            ensure_ascii=False,
        ).encode()
        process.stdin.write(request)
        await process.stdin.drain()
        process.stdin.close()
        output = bytearray()
        while chunk := await process.stdout.read(8192):
            output.extend(chunk)
            if len(output) > 65536:
                raise ValueError("bounded child protocol exceeded")
        code = await process.wait()
        if code != 0:
            return Reply(status="retry", error_code="handler_process_failed")
        return Reply.model_validate_json(output)
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
