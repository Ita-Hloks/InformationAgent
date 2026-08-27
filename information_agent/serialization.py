from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .common import content_blocks_to_payload
from .contracts import PROJECT_TIMEZONE, CollectionReport, Report
from .selection import SelectedEvidence

if TYPE_CHECKING:
    from .agent import AgentReport
    from .investigation import PlanningReport
    from .opinion.references import ReferenceDiscoveryResult
    from .orchestration.search_workflow import SearchReport
    from .search import SearchAnswer
    from .storage import PersistedPlanning


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
    payload = {
        "topic": report.topic,
        "status": report.status.value,
        "articles": [_selected_evidence_to_payload(item) for item in report.articles],
        "plans": [_search_plan_to_payload(item) for item in report.plans],
        "errors": report.errors,
    }
    if report.opinion_plans:
        payload["opinion_plans"] = [_opinion_plan_to_payload(item) for item in report.opinion_plans]
    return payload


def persisted_planning_to_payload(result: PersistedPlanning) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "planning_run_id": result.planning_run_id,
        **planning_report_to_payload(result.report),
    }


def agent_report_to_payload(report: AgentReport) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "analysis_run_id": report.analysis_run_id,
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
    payload = {
        "topic": report.topic,
        "status": report.status.value,
        "articles": [_selected_evidence_to_payload(item) for item in report.articles],
        "plans": [_search_plan_to_payload(item) for item in report.plans],
        "answers": [search_answer_to_payload(item) for item in report.answers],
        "errors": report.errors,
    }
    if report.opinion_plans:
        payload["opinion_plans"] = [_opinion_plan_to_payload(item) for item in report.opinion_plans]
    return payload


def opinion_references_to_payload(result: ReferenceDiscoveryResult) -> dict[str, Any]:
    return {
        "article_id": result.article_id,
        "snapshot_id": result.snapshot_id,
        "content_hash": result.content_hash,
        "status": result.status.value,
        "status_reason": result.status_reason,
        "queries": [
            {
                "evidence_id": plan.evidence_id,
                "trigger_quote": plan.trigger_quote,
                "question": plan.question,
                "query": query.query,
                "purpose": query.purpose,
            }
            for plan in result.plans
            for query in plan.queries
        ],
        "candidates": [
            {
                "video_id": candidate.video_id,
                "bvid": candidate.bvid,
                "url": candidate.url,
                "title": candidate.title,
                "search_query": candidate.search_query,
                "snippet": candidate.snippet,
                "site_name": candidate.site_name,
                "published_at": candidate.published_at,
                "reference": candidate.reference,
                "author": candidate.author,
                "tag": candidate.tag,
            }
            for candidate in result.candidates
        ],
        "errors": list(result.errors),
    }


def opinion_report_to_payload(report: Any) -> dict[str, Any]:
    return {
        "product_name": report.product_name,
        "article_id": report.article_id,
        "article_snapshot_id": report.article_snapshot_id,
        "content_hash": report.content_hash,
        "source_url": report.source_url,
        "status": report.status.value,
        "platform": report.platform,
        "window_hours": report.window_hours,
        "requested_limit": report.requested_limit,
        "collected_count": report.collected_count,
        "analyzed_count": report.analyzed_count,
        "classification_total": report.classification_total,
        "classified_count": report.classified_count,
        "unclassified_count": report.unclassified_count,
        "status_reason": report.status_reason,
        "run_id": report.run_id,
        "requested_at": report.requested_at,
        "finished_at": report.finished_at,
        "last_heartbeat_at": report.last_heartbeat_at,
        "controversy_points": [
            _opinion_plan_to_payload(item) for item in report.controversy_points
        ],
        "comments": [
            {
                "comment_id": item.comment_id,
                "source_url": item.source_url,
                "author": item.author,
                "content": item.content,
                "likes": item.likes,
                "published_at": item.published_at.isoformat() if item.published_at else None,
            }
            for item in report.comments
        ],
        "classifications": [
            {
                "run_id": item.run_id,
                "evidence_id": item.evidence_id,
                "comment_id": item.comment_id,
                "classification_status": item.classification_status.value,
                "stance": item.stance.value if item.stance else None,
                "error_code": item.error_code,
            }
            for item in report.classifications
        ],
        "summary": report.summary,
        "points": [
            {
                "evidence_id": item.evidence_id,
                "question": item.question,
                "summary": item.summary,
                "stance_counts": item.stance_counts,
                "representative_comment_ids": list(item.representative_comment_ids),
            }
            for item in report.points
        ],
        "uncertainties": list(report.uncertainties),
        "errors": [
            {
                "code": item.code,
                "stage": item.stage,
                "message": item.message,
                "retryable": item.retryable,
                "attempt": item.attempt,
            }
            for item in report.errors
        ],
        "attempts": [
            {
                "stage": item.stage,
                "attempt": item.attempt,
                "started_at": item.started_at,
                "finished_at": item.finished_at,
                "outcome": item.outcome,
                "error_code": item.error_code,
                "error_summary": item.error_summary,
            }
            for item in report.attempts
        ],
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


def _opinion_plan_to_payload(item: Any) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "trigger_quote": item.trigger_quote,
        "question": item.question,
        "platform": item.platform,
        "window_hours": item.window_hours,
        "queries": [{"query": query.query, "purpose": query.purpose} for query in item.queries],
    }


def _selected_evidence_to_payload(item: SelectedEvidence) -> dict[str, Any]:
    payload = asdict(item.article)
    payload["categories"] = list(item.article.categories)
    payload["content_chunks"] = list(item.article.content_chunks)
    payload["content_blocks"] = content_blocks_to_payload(item.article.content_blocks)
    payload["processing_warnings"] = list(item.article.processing_warnings)
    payload["id"] = item.evidence_id
    payload["collected_at"] = format_json_datetime(item.article.collected_at)
    if item.article.published_at is not None:
        payload["published_at"] = format_json_datetime(item.article.published_at)
    return payload
