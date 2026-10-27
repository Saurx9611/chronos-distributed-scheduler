import io
import json
from uuid import uuid4

import pytest
from chronos.worker.demo import sample_png
from chronos.worker.execute import execute
from chronos.worker.handlers import (
    HANDLERS,
    Context,
    PermanentFailure,
    RetryableFailure,
    csv_transform,
    image_metadata,
    notification,
    synthetic_report,
)
from chronos.worker.runner import Reply
from chronos.worker.settings import WorkerSettings
from pydantic import ValidationError


@pytest.mark.asyncio
async def test_actual_handler_outputs() -> None:
    context = Context(uuid4(), 1)
    metadata = await image_metadata({"png_base64": sample_png()}, context)
    assert metadata["width"] == metadata["height"] == 1
    assert metadata["format"] == "PNG"
    result = await csv_transform({"csv_text": "name,amount\na,1.235\nb,2.50\n"}, context)
    assert result["amount_total"] == "3.74"
    assert result["csv_text"] == "name,amount\na,1.24\nb,2.50\n"
    report = await synthetic_report({"rows": 100, "seed": 1}, context)
    assert report == await synthetic_report({"rows": 100, "seed": 1}, context)
    delivered = await notification({"recipient": "local@example.invalid"}, context)
    assert delivered["simulated"] is True
    assert delivered == await notification(
        {"recipient": "local@example.invalid"}, Context(context.job_id, 2)
    )


@pytest.mark.asyncio
async def test_invalid_and_transient_payloads() -> None:
    context = Context(uuid4(), 1)
    with pytest.raises(PermanentFailure):
        await image_metadata({"png_base64": "bad"}, context)
    with pytest.raises(ValidationError):
        await synthetic_report({"code": "malicious", "command": "anything"}, context)
    with pytest.raises(PermanentFailure):
        await csv_transform({"csv_text": "name,amount\na,NaN\n"}, context)
    with pytest.raises(RetryableFailure):
        await notification({"recipient": "local", "transient_until_attempt": 1}, context)
    with pytest.raises(PermanentFailure):
        await notification({"recipient": "local", "disposition": "reject"}, context)


@pytest.mark.parametrize(
    "values",
    [
        {"allowed_handlers": "os.system"},
        {"concurrency": 0},
        {"renew_interval_s": 11},
        {"db_call_timeout_s": 5, "lease_seconds": 3},
    ],
)
def test_unsafe_worker_configuration_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WorkerSettings.model_validate(
            {"CHRONOS_DATABASE_URL": "postgresql://test@localhost/test"} | values
        )


@pytest.mark.parametrize(
    "reply",
    [
        {"status": "ok"},
        {"status": "retry", "result": {"rows": 1}},
    ],
)
def test_child_protocol_cannot_report_success_without_a_result(reply: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Reply.model_validate(reply)


@pytest.mark.asyncio
async def test_handler_exception_is_retryable_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(payload: dict[str, object], context: Context) -> dict[str, object]:
        raise RuntimeError("SECRET_EXCEPTION_MUST_NOT_LEAVE_CHILD")

    request = {
        "job_id": str(uuid4()),
        "generation": 1,
        "job_type": "synthetic_report",
        "payload": {},
    }
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(json.dumps(request).encode())))
    monkeypatch.setitem(HANDLERS, "synthetic_report", broken)
    assert await execute() == {"status": "retry", "error_code": "handler_exception"}
