"""Fixed child entry point. JSON data is never interpreted as code."""

import asyncio
import ctypes
import json
import os
import signal
import sys
import threading
import time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from chronos.worker.handlers import (
    HANDLERS,
    Context,
    PermanentFailure,
    RetryableFailure,
    encode_result,
)


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    generation: int
    job_type: Literal[
        "image_metadata", "csv_transform", "synthetic_report", "notification", "demo_effect"
    ]
    payload: dict[str, JsonValue]
    effect_key: UUID | None = None


def protect_parent_lifetime() -> None:
    parent = int(os.environ["CHRONOS_EXPECTED_PARENT_PID"])
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL) != 0 or os.getppid() != parent:
            os._exit(70)
    elif sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x00100000, False, parent)
        if not handle:
            os._exit(70)
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]

        def monitor() -> None:
            if kernel.WaitForSingleObject(handle, 0xFFFFFFFF) == 0:
                os._exit(70)

        threading.Thread(target=monitor, daemon=True).start()
    else:

        def monitor() -> None:
            while os.getppid() == parent:
                time.sleep(0.1)
            os._exit(70)

        threading.Thread(target=monitor, daemon=True).start()


async def execute() -> dict[str, JsonValue]:
    try:
        request = Request.model_validate_json(sys.stdin.buffer.read(131073))
        result = await HANDLERS[request.job_type](
            request.payload, Context(request.job_id, request.generation, request.effect_key)
        )
        encode_result(result)
        return {"status": "ok", "result": result}
    except ValidationError:
        return {"status": "permanent", "error_code": "payload_invalid"}
    except PermanentFailure as exc:
        return {"status": "permanent", "error_code": str(exc)}
    except RetryableFailure as exc:
        return {"status": "retry", "error_code": str(exc)}
    except Exception:
        return {"status": "retry", "error_code": "handler_exception"}


if __name__ == "__main__":
    protect_parent_lifetime()
    print(json.dumps(asyncio.run(execute()), ensure_ascii=False, allow_nan=False), flush=True)
