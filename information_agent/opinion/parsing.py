from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ..investigation import OpinionPlan, SearchQuery
from .models import (
    Attempt,
    BilibiliComment,
    Classification,
    OpinionError,
    OpinionPoint,
    OpinionReport,
    OpinionStatus,
    aggregate_opinion_points,
)


def parse_persisted_opinion_report(
    payload: object,
    *,
    article_id: str | None = None,
    source_url: str | None = None,
    status: str | OpinionStatus | None = None,
    run_id: str | None = None,
    requested_at: str | None = None,
    finished_at: str | None = None,
) -> OpinionReport:
    """将 SQLite 结果 JSON 一次性恢复为已确认的 `OpinionReport`。"""

    if not isinstance(payload, Mapping):
        raise ValueError("舆情结果必须是 JSON 对象")
    resolved_article_id = _required_string(payload.get("article_id", article_id), "article_id")
    resolved_source_url = _required_string(payload.get("source_url", source_url), "source_url")
    resolved_status = payload.get("status", status)
    if resolved_status is None:
        raise ValueError("舆情结果缺少 status")
    try:
        report_status = OpinionStatus(resolved_status)
    except (TypeError, ValueError) as exc:
        raise ValueError("舆情结果的 status 无效") from exc

    plans = tuple(_parse_plan(item) for item in _required_list(payload, "controversy_points"))
    comments = tuple(_parse_comment(item) for item in _required_list(payload, "comments"))
    classifications = tuple(
        _parse_classification(item) for item in _required_list(payload, "classifications")
    )
    raw_points = tuple(_parse_point(item) for item in _required_list(payload, "points"))
    if classifications:
        point_summaries = {item.evidence_id: item.summary for item in raw_points}
        representative_comment_ids = {
            item.evidence_id: item.representative_comment_ids for item in raw_points
        }
        try:
            points = aggregate_opinion_points(
                plans,
                classifications,
                point_summaries=point_summaries,
                representative_comment_ids=representative_comment_ids,
            )
        except ValueError as exc:
            raise ValueError(f"持久化争议点聚合结果无效：{exc}") from exc
    else:
        # 没有关系行时不保留模型提供的立场数量或代表评论。
        points = ()
    errors = tuple(_parse_error(item) for item in _required_list(payload, "errors"))
    attempts = tuple(_parse_attempt(item) for item in _required_list(payload, "attempts"))
    uncertainties = tuple(
        _required_string(item, "uncertainty") for item in _required_list(payload, "uncertainties")
    )

    raw_summary = payload.get("summary", "")
    if not isinstance(raw_summary, str):
        raise ValueError("summary 必须是字符串")
    summary = raw_summary.strip()
    if report_status is OpinionStatus.COMPLETED and not summary:
        raise ValueError("completed 舆情结果必须包含 summary")

    classified_count = sum(
        item.classification_status.value == "classified" for item in classifications
    )
    unclassified_count = len(classifications) - classified_count
    return OpinionReport(
        article_id=resolved_article_id,
        source_url=resolved_source_url,
        status=report_status,
        product_name=str(payload.get("product_name", "哔哩哔哩公开评论样本分析")),
        article_snapshot_id=_optional_string(payload.get("article_snapshot_id")),
        content_hash=_optional_string(payload.get("content_hash")),
        platform=str(payload.get("platform", "bilibili")),
        window_hours=int(payload.get("window_hours", 72)),
        requested_limit=_optional_int(payload.get("requested_limit")),
        collected_count=_nonnegative_int(
            payload.get("collected_count", len(comments)), "collected_count"
        ),
        analyzed_count=_nonnegative_int(
            payload.get("analyzed_count", len(comments)), "analyzed_count"
        ),
        classification_total=_nonnegative_int(
            payload.get("classification_total", len(classifications)), "classification_total"
        ),
        classified_count=_nonnegative_int(
            payload.get("classified_count", classified_count), "classified_count"
        ),
        unclassified_count=_nonnegative_int(
            payload.get("unclassified_count", unclassified_count), "unclassified_count"
        ),
        status_reason=str(payload.get("status_reason", _default_status_reason(report_status))),
        run_id=_optional_string(payload.get("run_id", run_id)),
        requested_at=_optional_string(payload.get("requested_at", requested_at)),
        finished_at=_optional_string(payload.get("finished_at", finished_at)),
        last_heartbeat_at=_optional_string(payload.get("last_heartbeat_at")),
        controversy_points=plans,
        comments=comments,
        classifications=classifications,
        summary=summary,
        points=points,
        uncertainties=uncertainties,
        errors=errors,
        attempts=attempts,
    )


def _parse_plan(value: object) -> OpinionPlan:
    if not isinstance(value, Mapping):
        raise ValueError("controversy_points 中存在无效项目")
    queries = value.get("queries")
    if not isinstance(queries, list):
        raise ValueError("OpinionPlan 的 queries 必须是数组")
    parsed_queries = tuple(
        SearchQuery(
            _required_string(item.get("query") if isinstance(item, Mapping) else None, "query"),
            _required_string(item.get("purpose") if isinstance(item, Mapping) else None, "purpose"),
        )
        for item in queries
    )
    try:
        return OpinionPlan(
            evidence_id=_required_int(value.get("evidence_id"), "evidence_id"),
            trigger_quote=_required_string(value.get("trigger_quote"), "trigger_quote"),
            question=_required_string(value.get("question"), "question"),
            queries=parsed_queries,
            platform=_required_string(value.get("platform", "bilibili"), "platform"),
            window_hours=_required_int(value.get("window_hours", 72), "window_hours"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"OpinionPlan 无效：{exc}") from exc


def _parse_comment(value: object) -> BilibiliComment:
    if not isinstance(value, Mapping):
        raise ValueError("comments 中存在无效项目")
    published_at = value.get("published_at")
    parsed_published_at = None
    if published_at is not None:
        if not isinstance(published_at, str):
            raise ValueError("published_at 必须是 ISO 8601 字符串或 null")
        try:
            parsed_published_at = datetime.fromisoformat(published_at)
        except ValueError as exc:
            raise ValueError("published_at 不是合法的 ISO 8601 时间") from exc
    return BilibiliComment(
        comment_id=_required_string(value.get("comment_id"), "comment_id"),
        source_url=_required_string(value.get("source_url"), "source_url"),
        author=_required_string(value.get("author"), "author"),
        content=_required_string(value.get("content"), "content"),
        likes=_required_int(value.get("likes"), "likes"),
        published_at=parsed_published_at,
    )


def _parse_classification(value: object) -> Classification:
    if not isinstance(value, Mapping):
        raise ValueError("classifications 中存在无效项目")
    try:
        return Classification(
            run_id=_required_string(value.get("run_id"), "run_id"),
            evidence_id=_required_int(value.get("evidence_id"), "evidence_id"),
            comment_id=_required_string(value.get("comment_id"), "comment_id"),
            classification_status=_required_string(
                value.get("classification_status"), "classification_status"
            ),
            stance=_optional_string(value.get("stance")),
            error_code=_optional_string(value.get("error_code")),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Classification 无效：{exc}") from exc


def _parse_point(value: object) -> OpinionPoint:
    if not isinstance(value, Mapping):
        raise ValueError("points 中存在无效项目")
    representatives = value.get("representative_comment_ids")
    if not isinstance(representatives, list):
        raise ValueError("representative_comment_ids 必须是数组")
    counts = value.get("stance_counts")
    if not isinstance(counts, Mapping):
        raise ValueError("stance_counts 必须是对象")
    return OpinionPoint(
        evidence_id=_required_int(value.get("evidence_id"), "evidence_id"),
        question=_required_string(value.get("question"), "question"),
        summary=_required_string(value.get("summary"), "summary"),
        stance_counts={
            str(key): _required_int(item, "stance_count") for key, item in counts.items()
        },
        representative_comment_ids=tuple(
            _required_string(item, "representative_comment_id") for item in representatives
        ),
    )


def _parse_error(value: object) -> OpinionError:
    if isinstance(value, str):
        return OpinionError("failed", "unknown", value, False)
    if not isinstance(value, Mapping):
        raise ValueError("errors 中存在无效项目")
    return OpinionError(
        code=_required_string(value.get("code", "failed"), "error code"),
        stage=_required_string(value.get("stage", "unknown"), "error stage"),
        message=_required_string(value.get("message"), "error message"),
        retryable=value.get("retryable", False),
        attempt=_optional_int(value.get("attempt")),
    )


def _parse_attempt(value: object) -> Attempt:
    if not isinstance(value, Mapping):
        raise ValueError("attempts 中存在无效项目")
    return Attempt(
        stage=_required_string(value.get("stage"), "attempt stage"),
        attempt=_required_int(value.get("attempt"), "attempt"),
        started_at=_required_string(value.get("started_at"), "started_at"),
        finished_at=_required_string(value.get("finished_at"), "finished_at"),
        outcome=_required_string(value.get("outcome"), "outcome"),
        error_code=_optional_string(value.get("error_code")),
        error_summary=_optional_string(value.get("error_summary")),
    )


def _required_list(payload: Mapping[str, Any], key: str) -> list[object]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"舆情结果的 {key} 必须是数组")
    return value


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value.strip()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _required_string(value, "string")


def _required_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} 必须是整数")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    parsed = _required_int(value, name)
    if parsed < 0:
        raise ValueError(f"{name} 必须是非负整数")
    return parsed


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _required_int(value, "integer")


def _default_status_reason(status: OpinionStatus) -> str:
    if status is OpinionStatus.NOT_REQUESTED:
        return "not_requested"
    if status is OpinionStatus.RUNNING:
        return "running"
    if status is OpinionStatus.FAILED:
        return "failed"
    return status.value
