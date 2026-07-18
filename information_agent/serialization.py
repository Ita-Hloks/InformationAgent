from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from .contracts import Report


def format_json_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("JSON 时间必须包含时区")
    return value.astimezone(UTC).isoformat(timespec="minutes")


def report_to_payload(report: Report) -> dict[str, Any]:
    payload = asdict(report)
    payload["status"] = report.status.value
    for item in payload["evidence"]:
        item["collected_at"] = format_json_datetime(item["collected_at"])
        if item["published_at"] is not None:
            item["published_at"] = format_json_datetime(item["published_at"])
    return payload
