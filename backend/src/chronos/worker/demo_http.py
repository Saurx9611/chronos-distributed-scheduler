"""Fixed operator-configured demo destination; never accept a URL from a job."""

import asyncio
import json
import os
import urllib.request
from typing import Any
from uuid import UUID

from chronos.engine.models import Claim


def request(path: str, body: dict[str, object]) -> dict[str, Any]:
    url = os.environ.get("CHRONOS_DEMO_SINK_URL")
    token = os.environ.get("CHRONOS_DEMO_SINK_TOKEN")
    if not url or not token or os.environ.get("CHRONOS_ENABLE_EFFECT_DEMO") != "1":
        raise RuntimeError("effect demo disabled")
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(
            self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
        ) -> None:
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=1) as response:
        raw = response.read(16385)
        if len(raw) > 16384:
            raise ValueError("demo response bound exceeded")
        value: dict[str, Any] = json.loads(raw)
        return value


async def gate(stage: str, job_id: UUID, generation: int) -> None:
    if os.environ.get("CHRONOS_FAULT_INJECTION") != "1":
        return
    if os.environ.get("CHRONOS_FAULT_POINT") != stage:
        return
    if os.environ.get("CHRONOS_WORKER_NAME") != os.environ.get("CHRONOS_FAULT_WORKER", "worker-1"):
        return
    while True:
        result = await asyncio.to_thread(
            request, f"/gates/{job_id}/arrive", {"generation": generation, "stage": stage}
        )
        if result.get("released"):
            return
        await asyncio.sleep(0.05)


async def claim_gate(claim: Claim) -> None:
    await gate("after_claim", claim.job_id, claim.generation)
