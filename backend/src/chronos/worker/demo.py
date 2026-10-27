"""Trusted operator CLI and reproducible, harmless workload data."""

import argparse
import base64
import json
import struct
import zlib
from uuid import uuid4

from pydantic import JsonValue
from sqlalchemy import text

from chronos.engine.models import JobSpec
from chronos.engine.repository import QueueRepository, create_queue_engine
from chronos.worker.settings import WorkerSettings


def sample_png() -> str:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    raw = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x10\x20\x30"))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(raw).decode()


def workloads(delay_s: float = 0, queue: str = "default") -> list[JobSpec]:
    payloads: list[tuple[str, dict[str, JsonValue]]] = [
        ("image_metadata", {"png_base64": sample_png(), "delay_s": delay_s}),
        ("csv_transform", {"csv_text": "name,amount\nalice,1.235\nbob,2.50\n", "delay_s": delay_s}),
        ("synthetic_report", {"rows": 1000, "seed": 7, "delay_s": delay_s}),
        ("notification", {"recipient": "demo@example.invalid", "delay_s": delay_s}),
    ]
    return [JobSpec(job_type=kind, payload=data, queue=queue) for kind, data in payloads]


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit bounded local Chronos demo workloads")
    parser.add_argument("action", choices=["seed", "status"])
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--delay", type=float, default=2)
    args = parser.parse_args()
    if not 1 <= args.count <= 100 or not 0 <= args.delay <= 60:
        parser.error("count must be 1..100 and delay 0..60")
    settings = WorkerSettings()
    engine = create_queue_engine(settings.connection_url)
    try:
        if args.action == "seed":
            repo = QueueRepository(engine)
            tenant = repo.create_tenant("demo-" + uuid4().hex[:8])
            specs = workloads(args.delay, settings.queue)
            jobs = [
                str(repo.submit(tenant, str(i), specs[i % len(specs)])) for i in range(args.count)
            ]
            print(json.dumps({"tenant_id": str(tenant), "job_ids": jobs}))
        else:
            with engine.connect() as conn:
                rows = (
                    conn.execute(
                        text("""
                    SELECT j.state,count(*) AS jobs FROM jobs j GROUP BY j.state ORDER BY j.state
                """)
                    )
                    .mappings()
                    .all()
                )
                print(json.dumps([dict(row) for row in rows]))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
