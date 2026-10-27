"""Fixed stale-owner probe used only by explicitly enabled fault tests."""

import json
import os
import sys
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from chronos.engine.models import Claim, OwnershipLost
from chronos.engine.repository import QueueRepository, create_queue_engine
from chronos.worker.settings import WorkerSettings


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    job_id: UUID
    tenant_id: UUID
    worker_id: UUID
    token: UUID
    generation: int = Field(ge=1, le=10)


def main() -> None:
    if os.environ.get("CHRONOS_FAULT_INJECTION") != "1":
        raise SystemExit("fault injection disabled")
    report = Report.model_validate_json(sys.stdin.buffer.read(4096))
    engine = create_queue_engine(WorkerSettings().connection_url, pool_size=1)
    try:
        claim = Claim(
            report.job_id,
            report.tenant_id,
            report.worker_id,
            report.token,
            report.generation,
            "demo_effect",
            {},
            datetime.now(UTC),
        )
        try:
            QueueRepository(engine).acknowledge(claim, result={"stale_probe": True})
        except OwnershipLost:
            QueueRepository(engine).record_ownership_rejection(claim)
            print(json.dumps({"accepted": False, "reason": "ownership_lost"}))
        else:
            print(json.dumps({"accepted": True}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"accepted": False, "reason": "probe_failed"}))
        sys.exit(1)
