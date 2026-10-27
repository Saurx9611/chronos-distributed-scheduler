"""Four data-only, bounded workloads. No filesystem, network or dynamic imports."""

import asyncio
import base64
import binascii
import csv
import hashlib
import io
import json
import random
import struct
import zlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class PermanentFailure(Exception):
    pass


class RetryableFailure(Exception):
    pass


@dataclass(frozen=True)
class Context:
    job_id: UUID
    generation: int


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delay_s: float = Field(default=0, ge=0, le=60, allow_inf_nan=False)


class ImagePayload(Payload):
    png_base64: str = Field(max_length=60000)


class CsvPayload(Payload):
    csv_text: str = Field(max_length=40000)
    amount_column: str = Field(default="amount", min_length=1, max_length=100)


class ReportPayload(Payload):
    rows: int = Field(default=1000, ge=1, le=10000, strict=True)
    seed: int = Field(default=1, ge=0, le=2147483647, strict=True)


class NotificationPayload(Payload):
    recipient: str = Field(min_length=1, max_length=200)
    transient_until_attempt: int = Field(default=0, ge=0, le=3, strict=True)
    disposition: Literal["deliver", "reject"] = "deliver"


async def image_metadata(payload: dict[str, JsonValue], context: Context) -> dict[str, JsonValue]:
    data = ImagePayload.model_validate(payload)
    await asyncio.sleep(data.delay_s)
    try:
        raw = base64.b64decode(data.png_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PermanentFailure("invalid_png") from exc
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise PermanentFailure("invalid_png")
    offset = 8
    width = height = depth = color = 0
    chunks = 0
    has_data = False
    while offset + 12 <= len(raw):
        size = struct.unpack(">I", raw[offset : offset + 4])[0]
        kind = raw[offset + 4 : offset + 8]
        end = offset + 12 + size
        if end > len(raw) or chunks > 1000:
            raise PermanentFailure("invalid_png")
        body = raw[offset + 8 : offset + 8 + size]
        checksum = struct.unpack(">I", raw[end - 4 : end])[0]
        if zlib.crc32(kind + body) != checksum:
            raise PermanentFailure("invalid_png")
        if chunks == 0:
            if kind != b"IHDR" or size != 13:
                raise PermanentFailure("invalid_png")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", body
            )
            if not (0 < width <= 16384 and 0 < height <= 16384):
                raise PermanentFailure("image_dimensions")
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                depth not in valid_depths.get(color, set())
                or compression
                or filtering
                or interlace > 1
            ):
                raise PermanentFailure("invalid_png")
        has_data |= kind == b"IDAT"
        chunks += 1
        offset = end
        if kind == b"IEND":
            if size != 0 or offset != len(raw) or not has_data:
                raise PermanentFailure("invalid_png")
            return {
                "format": "PNG",
                "width": width,
                "height": height,
                "bit_depth": depth,
                "color_type": color,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
    raise PermanentFailure("invalid_png")


async def csv_transform(payload: dict[str, JsonValue], context: Context) -> dict[str, JsonValue]:
    data = CsvPayload.model_validate(payload)
    await asyncio.sleep(data.delay_s)
    csv.field_size_limit(4096)
    source = csv.DictReader(io.StringIO(data.csv_text), strict=True)
    fields = source.fieldnames
    if (
        not fields
        or len(fields) > 50
        or len(set(fields)) != len(fields)
        or data.amount_column not in fields
    ):
        raise PermanentFailure("csv_columns")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    total = Decimal(0)
    count = 0
    try:
        for row in source:
            count += 1
            if count > 2000 or None in row or any(value is None for value in row.values()):
                raise PermanentFailure("csv_shape")
            amount = Decimal(row[data.amount_column])
            if not amount.is_finite() or abs(amount) > Decimal("1e12"):
                raise PermanentFailure("csv_amount")
            amount = amount.quantize(Decimal("0.01"))
            total += amount
            row[data.amount_column] = str(amount)
            writer.writerow(row)
    except (InvalidOperation, csv.Error) as exc:
        raise PermanentFailure("csv_invalid") from exc
    return {"rows": count, "amount_total": str(total), "csv_text": output.getvalue()}


async def synthetic_report(payload: dict[str, JsonValue], context: Context) -> dict[str, JsonValue]:
    data = ReportPayload.model_validate(payload)
    await asyncio.sleep(data.delay_s)
    generator = random.Random(data.seed)
    counts = [0] * 10
    total = 0
    digest = hashlib.sha256()
    for _ in range(data.rows):
        value = generator.randrange(10000)
        counts[value // 1000] += 1
        total += value
        digest.update(struct.pack(">I", value))
    return {
        "rows": data.rows,
        "total": total,
        "buckets": list(counts),
        "sha256": digest.hexdigest(),
    }


async def notification(payload: dict[str, JsonValue], context: Context) -> dict[str, JsonValue]:
    data = NotificationPayload.model_validate(payload)
    await asyncio.sleep(data.delay_s)
    if data.disposition == "reject":
        raise PermanentFailure("notification_rejected")
    if context.generation <= data.transient_until_attempt:
        raise RetryableFailure("notification_transient")
    return {
        "simulated": True,
        "delivery_key": str(context.job_id),
        "receipt": hashlib.sha256(str(context.job_id).encode()).hexdigest(),
    }


HANDLERS = {
    "image_metadata": image_metadata,
    "csv_transform": csv_transform,
    "synthetic_report": synthetic_report,
    "notification": notification,
}


def encode_result(result: dict[str, JsonValue]) -> str:
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 60000:
        raise PermanentFailure("result_too_large")
    return encoded
