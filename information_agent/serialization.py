from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .contracts import PROJECT_TIMEZONE, CollectionReport, Report
from .selection import SelectedEvidence

if TYPE_CHECKING:
    from .agent import AgentReport
    from .investigation import PlanningReport
    from .orchestration.search_workflow import SearchReport
    from .search import SearchAnswer
    from .storage import PersistedCollection, PersistedPlanning, ResearchRunSummary


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


def persisted_collection_to_payload(result: PersistedCollection) -> dict[str, Any]:
    return {"run_id": result.run_id, **collection_report_to_payload(result.report)}


def planning_report_to_payload(report: PlanningReport) -> dict[str, Any]:
    return {
        "topic": report.topic,
        "status": report.status.value,
        "articles": [_selected_evidence_to_payload(item) for item in report.articles],
        "plans": [_search_plan_to_payload(item) for item in report.plans],
        "errors": report.errors,
    }


def persisted_planning_to_payload(result: PersistedPlanning) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "planning_run_id": result.planning_run_id,
        **planning_report_to_payload(result.report),
    }


def research_run_summaries_to_payload(
    runs: list[ResearchRunSummary],
) -> dict[str, list[dict[str, Any]]]:
    return {"runs": [_research_run_summary_to_payload(run) for run in runs]}


def agent_report_to_payload(report: AgentReport) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "topic": report.topic,
        "status": report.status.value,
        "articles": [_selected_evidence_to_payload(item) for item in report.articles],
        "plans": [_search_plan_to_payload(item) for item in report.plans],
        "answers": [search_answer_to_payload(item) for item in report.answers],
        "final_answer": report.final_answer,
        "evidence_ids": list(report.evidence_ids),
        "citations": [asdict(citation) for citation in report.citations],
        "uncertainties": list(report.uncertainties),
        "steps": report.steps,
        "stop_reason": report.stop_reason.value,
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
    payload["id"] = item.evidence_id
    payload["collected_at"] = format_json_datetime(item.article.collected_at)
    if item.article.published_at is not None:
        payload["published_at"] = format_json_datetime(item.article.published_at)
    return payload


def _research_run_summary_to_payload(run: ResearchRunSummary) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run.run_id,
        "topic": run.topic,
        "status": run.status,
        "started_at": format_json_datetime(run.started_at),
        "feed_count": run.feed_count,
        "snapshot_count": run.snapshot_count,
        "selected_evidence_count": run.selected_evidence_count,
        "collection_error_count": run.collection_error_count,
    }
    if run.finished_at is not None:
        payload["finished_at"] = format_json_datetime(run.finished_at)
    return payload
