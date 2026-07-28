from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .contracts import PROJECT_TIMEZONE, CollectionReport, Report
from .selection import SelectedEvidence

if TYPE_CHECKING:
    from .investigation import PlanningReport
    from .orchestration.search_workflow import SearchReport
    from .search import SearchAnswer


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


def planning_report_to_payload(report: PlanningReport) -> dict[str, Any]:
    return {
        "topic": report.topic,
        "status": report.status.value,
        "articles": [_selected_evidence_to_payload(item) for item in report.articles],
        "plans": [_search_plan_to_payload(item) for item in report.plans],
        "errors": report.errors,
    }


def search_answer_to_payload(answer: SearchAnswer) -> dict[str, Any]:
    payload = asdict(answer)
    payload["status"] = answer.status.value
    payload["sources"] = [asdict(source) for source in answer.sources]
    return payload


def search_report_to_payload(report: SearchReport) -> dict[str, Any]:
    return {
        "topic": report.topic,
        "status": report.status.value,
        "articles": [_selected_evidence_to_payload(item) for item in report.articles],
        "plans": [_search_plan_to_payload(item) for item in report.plans],
        "answers": [search_answer_to_payload(item) for item in report.answers],
        "errors": report.errors,
    }


def _search_plan_to_payload(item: Any) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "trigger_quote": item.trigger_quote,
        "question": item.question,
        "kind": item.kind.value,
        "priority": item.priority,
        "queries": [{"query": query.query, "purpose": query.purpose} for query in item.queries],
    }


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
