from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from .contracts import PROJECT_TIMEZONE, CollectionReport, Report
from .selection import SelectedEvidence


def format_json_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("JSON 时间必须包含时区")
    return value.astimezone(PROJECT_TIMEZONE).isoformat(timespec="minutes")


def report_to_payload(report: Report) -> dict[str, Any]:
    payload = asdict(report)
    payload["status"] = report.status.value
    payload["evidence"] = [_selected_evidence_to_payload(item) for item in report.evidence]
    return payload


def collection_report_to_payload(report: CollectionReport) -> dict[str, Any]:
    payload = asdict(report)
    payload["status"] = report.status.value
    payload["articles"] = [_selected_evidence_to_payload(item) for item in report.articles]
    return payload


def _selected_evidence_to_payload(item: SelectedEvidence) -> dict[str, Any]:
    payload = asdict(item.article)
    payload["categories"] = list(item.article.categories)
    payload["content_chunks"] = list(item.article.content_chunks)
    payload["processing_warnings"] = list(item.article.processing_warnings)
    payload["relevance_score"] = item.relevance_score
    payload["id"] = item.evidence_id
    payload["collected_at"] = format_json_datetime(item.article.collected_at)
    if item.article.published_at is not None:
        payload["published_at"] = format_json_datetime(item.article.published_at)
    return payload
