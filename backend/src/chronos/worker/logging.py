"""JSON lifecycle events: never format exception text, payloads, results or tokens."""

import json
from datetime import UTC, datetime
from typing import Any


def emit(event: str, **fields: Any) -> None:
    print(
        json.dumps(
            {"timestamp": datetime.now(UTC).isoformat(), "event": event, **fields},
            default=str,
            allow_nan=False,
        ),
        flush=True,
    )
