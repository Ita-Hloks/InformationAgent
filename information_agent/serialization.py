from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from .contracts import PROJECT_TIMEZONE, CollectionReport, Report


def format_json_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("JSON 时间必须包含时区")
    return value.astimezone(PROJECT_TIMEZONE).isoformat(timespec="minutes")


def report_to_payload(report: Report) -> dict[str, Any]:
    payload = asdict(report)
    payload["status"] = report.status.value
    _serialize_articles(payload["evidence"])
    return payload


def collection_report_to_payload(report: CollectionReport) -> dict[str, Any]:
    payload = asdict(report)
    payload["status"] = report.status.value
    _serialize_articles(payload["articles"])
    return payload


def _serialize_articles(articles: list[dict[str, Any]]) -> None:
    for item in articles:
        item["collected_at"] = format_json_datetime(item["collected_at"])
        if item["published_at"] is not None:
            item["published_at"] = format_json_datetime(item["published_at"])
