from datetime import datetime

import pytest
from chronos.engine.models import JobSpec
from pydantic import ValidationError


@pytest.mark.parametrize(
    "changes",
    [
        {"priority": 101},
        {"max_attempts": 0},
        {"priority": True},
        {"execution_timeout_s": 901},
        {"scheduled_at": datetime(2026, 1, 1)},
        {"payload": {"value": "x" * 65536}},
        {"payload": {"value": float("nan")}},
    ],
)
def test_invalid_submission_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        JobSpec.model_validate({"job_type": "test"} | changes)
