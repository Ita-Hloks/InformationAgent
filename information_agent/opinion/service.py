from __future__ import annotations

import inspect
import math
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ..contracts import project_now
from ..investigation import OPINION_WINDOW_HOURS, ArticleSnapshotIdentity, OpinionPlan
from ..storage import (
    ArticleSnapshotMismatchError,
    OpinionRunRecord,
    SQLiteCollectionStore,
    default_database_path,
)
from .bilibili import BilibiliCommentCollector, parse_bilibili_target
from .llm import (
    MAX_OPINION_COMMENTS,
    LLMOpinionAnalyzer,
    OpinionAnalyzer,
    OpinionResponseError,
)
from .models import (
    Attempt,
    BilibiliComment,
    Classification,
    ClassificationStatus,
    OpinionPoint,
    OpinionReport,
    OpinionStatus,
    aggregate_opinion_points,
)
from .parsing import parse_persisted_opinion_report
from .runtime import (
    OpinionRetryExhaustedError,
    OpinionTimeoutError,
    error_summary,
    remaining_time,
)

Clock = Callable[[], float]
STALE_RUNNING_GRACE_SECONDS = 30.0


class OpinionArticleNotFoundError(LookupError):
    pass


class OpinionSnapshotMismatchError(ValueError):
    """当前文章快照不能复用旧的规划或运行。"""

    code = "article_snapshot_mismatch"


class CommentCollector(Protocol):
    def collect(
        self,
        source_url: str,
        *,
        window_hours: int,
        limit: int,
        timeout: float,
        deadline: float | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> list[BilibiliComment]: ...


class OpinionAnalysisService:
    """只在显式 request 调用时执行文章争议点和评论分析。"""

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        store: SQLiteCollectionStore | None = None,
        analyzer: OpinionAnalyzer | None = None,
        collector: CommentCollector | None = None,
        timeout_seconds: float = 300,
        comment_limit: int = 100,
        clock: Clock = time.monotonic,
        stale_running_grace_seconds: float = STALE_RUNNING_GRACE_SECONDS,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive finite number")
        if not 1 <= comment_limit <= 200:
            raise ValueError("comment_limit must be between 1 and 200")
        if not math.isfinite(stale_running_grace_seconds) or stale_running_grace_seconds < 0:
            raise ValueError("stale_running_grace_seconds must be a non-negative finite number")
        self.store = store or SQLiteCollectionStore(database_path or default_database_path())
        self.analyzer = analyzer
        self.collector = collector or BilibiliCommentCollector(
            cookie=os.getenv("BILIBILI_COOKIE"),
            clock=clock,
        )
        self.timeout_seconds = timeout_seconds
        self.comment_limit = comment_limit
        self.clock = clock
        self.stale_running_grace_seconds = stale_running_grace_seconds

    def get_status(self, article_id: str) -> OpinionReport:
        article = self.store.get_reader_article(article_id)
        if article is None:
            raise OpinionArticleNotFoundError(f"不存在的文章：{article_id}")
        identity = _article_identity(
            article.article.article_id, article.snapshot_id, article.content_hash
        )
        record = self.store.get_latest_opinion_run(article_id)
        if record is None:
            return OpinionReport(
                article_id=identity.article_id,
                article_snapshot_id=identity.article_snapshot_id,
                content_hash=identity.content_hash,
                source_url=article.article.source_url,
                status=OpinionStatus.NOT_REQUESTED,
            )
        if not self._record_matches_snapshot(record, identity):
            raise OpinionSnapshotMismatchError("文章运行与当前文章快照不一致")
        return self._report_from_record(record, article)

    def request(self, article_id: str, *, force_refresh: bool = False) -> OpinionReport:
        article = self.store.get_reader_article(article_id)
        if article is None:
            raise OpinionArticleNotFoundError(f"不存在的文章：{article_id}")
        identity = _article_identity(
            article.article.article_id, article.snapshot_id, article.content_hash
        )

        parse_bilibili_target(article.article.source_url)

        latest = self.store.get_latest_opinion_run(article_id)
        if latest is not None and latest.status in {
            OpinionStatus.COMPLETED.value,
            OpinionStatus.PARTIAL.value,
        }:
            if not self._record_matches_snapshot(latest, identity):
                raise OpinionSnapshotMismatchError("文章运行与当前文章快照不一致")
            if not force_refresh and self._record_matches(latest, identity):
                return self._report_from_record(latest, article)

        try:
            stored_plans = self.store.load_opinion_plans_for_article(
                article_id,
                article_snapshot_id=identity.article_snapshot_id,
                content_hash=identity.content_hash,
            )
        except ArticleSnapshotMismatchError as exc:
            raise OpinionSnapshotMismatchError(str(exc)) from exc

        run, created = self.store.acquire_opinion_run(
            article_id,
            article_snapshot_id=identity.article_snapshot_id,
            content_hash=identity.content_hash,
            requested_limit=self.comment_limit,
            timeout_seconds=self.timeout_seconds,
            stale_grace_seconds=self.stale_running_grace_seconds,
        )
        if not self._record_matches_snapshot(run, identity):
            raise OpinionSnapshotMismatchError("文章运行与当前文章快照不一致")
        if not created:
            return self._report_from_record(run, article)

        deadline = self.clock() + self.timeout_seconds
        controversy_points: list[OpinionPlan] = list(stored_plans)
        comments: list[BilibiliComment] = []
        points: list[OpinionPoint] = []
        classifications: list[Classification] = []
        uncertainties: list[str] = []
        errors: list[dict[str, object]] = []
        attempts: list[Attempt] = []
        summary = ""
        analyzed_count = 0
        status = OpinionStatus.COMPLETED
        status_reason = "completed"
        current_stage = "persistence"

        try:
            self._heartbeat(run.id, attempts)
            analyzer = self.analyzer
            if not controversy_points:
                current_stage = "opinion_planning"
                if self.analyzer is None:
                    analyzer = LLMOpinionAnalyzer(clock=self.clock)
                controversy_points = _detect_controversies(
                    analyzer,
                    article.article,
                    self._remaining(deadline),
                    deadline=deadline,
                    clock=self.clock,
                    heartbeat=lambda: self._heartbeat(run.id, attempts),
                    attempts=attempts,
                )

            if not controversy_points:
                summary = "文章中未识别出值得查看的争议点。"
                uncertainties.append("没有形成可分析的争议点，因此未抓取评论。")
                status_reason = "no_controversy_points"
            else:
                current_stage = "comment_collection"
                comments = _collect_comments(
                    self.collector,
                    article.article.source_url,
                    window_hours=OPINION_WINDOW_HOURS,
                    limit=self.comment_limit,
                    timeout=self._remaining(deadline),
                    deadline=deadline,
                    clock=self.clock,
                    heartbeat=lambda: self._heartbeat(run.id, attempts),
                    attempts=attempts,
                )
                if not comments:
                    summary = "最近 72 小时未获取到可分析的哔哩哔哩评论。"
                    uncertainties.append("样本为空，不能代表总体民意。")
                    status_reason = "sample_empty"
                else:
                    current_stage = "opinion_analysis"
                    analyzable_comments = comments[:MAX_OPINION_COMMENTS]
                    analyzed_count = len(analyzable_comments)
                    if self.analyzer is None:
                        analyzer = LLMOpinionAnalyzer(clock=self.clock)
                    summary, points, analyzer_uncertainties = _analyze_comments(
                        analyzer,
                        article.article,
                        controversy_points,
                        analyzable_comments,
                        self._remaining(deadline),
                        run.id,
                        deadline=deadline,
                        clock=self.clock,
                        heartbeat=lambda: self._heartbeat(run.id, attempts),
                        attempts=attempts,
                    )
                    uncertainties.extend(analyzer_uncertainties)
                    maybe_classifications = getattr(analyzer, "last_classifications", ())
                    classifications = list(maybe_classifications)
                    _validate_analysis_result(
                        summary,
                        points,
                        classifications,
                        controversy_points,
                        analyzable_comments,
                        run.id,
                    )
                    if any(
                        item.classification_status is ClassificationStatus.UNCLASSIFIED
                        for item in classifications
                    ):
                        status = OpinionStatus.PARTIAL
                        status_reason = "partial_classification"
        except Exception as exc:
            error_stage = str(getattr(exc, "stage", "") or current_stage or "unknown")
            errors.append(_error_payload_from_exception(exc, fallback_stage=error_stage))
            collector_comments = getattr(self.collector, "last_comments", ())
            if not comments and collector_comments:
                comments = list(collector_comments)
            has_partial_result = bool(comments or classifications)
            status_reason = _status_reason_from_exception(
                exc,
                has_partial_result=has_partial_result,
                fallback_stage=error_stage,
            )
            status = (
                OpinionStatus.PARTIAL
                if has_partial_result and status_reason != "failed"
                else OpinionStatus.FAILED
            )
            uncertainties.append("本次运行未形成完整的评论分析结论。")

        result_payload = _result_payload(
            article_id=identity.article_id,
            source_url=article.article.source_url,
            identity=identity,
            requested_limit=self.comment_limit,
            status=status,
            status_reason=status_reason,
            run_id=run.id,
            summary=summary,
            controversy_points=controversy_points,
            comments=comments,
            analyzed_count=analyzed_count,
            classifications=classifications,
            points=points,
            uncertainties=uncertainties,
            errors=errors,
            attempts=attempts,
        )
        record = self.store.complete_opinion_run(
            run.id,
            status=status.value,
            result_payload=result_payload,
            comments=[_comment_payload(comment) for comment in comments],
            errors=errors,
            classifications=[_classification_payload(item) for item in classifications],
            attempts=[_attempt_payload(item) for item in attempts],
        )
        return self._report_from_record(record, article)

    def _remaining(self, deadline: float) -> float:
        remaining = remaining_time(deadline, clock=self.clock)
        if remaining <= 0:
            raise OpinionTimeoutError("opinion")
        return remaining

    def _heartbeat(self, run_id: str, attempts: list[Attempt]) -> None:
        self.store.heartbeat_opinion_run(
            run_id,
            attempts=[_attempt_payload(item) for item in attempts],
        )

    def _record_matches(self, record: OpinionRunRecord, identity: ArticleSnapshotIdentity) -> bool:
        return self._record_matches_snapshot(record, identity) and (
            record.requested_limit == self.comment_limit
        )

    def _record_matches_snapshot(
        self, record: OpinionRunRecord, identity: ArticleSnapshotIdentity
    ) -> bool:
        return (
            record.article_snapshot_id == identity.article_snapshot_id
            and record.content_hash == identity.content_hash
            and record.platform == "bilibili"
            and record.window_hours == OPINION_WINDOW_HOURS
        )

    def _report_from_record(self, record: OpinionRunRecord, article) -> OpinionReport:
        identity = _article_identity(
            article.article.article_id, article.snapshot_id, article.content_hash
        )
        if record.article_id != identity.article_id or not self._record_matches_snapshot(
            record, identity
        ):
            raise OpinionSnapshotMismatchError("文章运行与当前文章快照不一致")

        payload = dict(record.result_payload or {})
        expected_fields = {
            "article_id": identity.article_id,
            "article_snapshot_id": identity.article_snapshot_id,
            "content_hash": identity.content_hash,
            "source_url": article.article.source_url,
        }
        for field, expected in expected_fields.items():
            if field in payload and payload[field] != expected:
                raise OpinionSnapshotMismatchError("舆情结果与当前文章快照不一致")
            payload[field] = expected
        payload.setdefault("status", record.status)
        if not payload.get("run_id"):
            payload["run_id"] = record.id
        if not payload.get("requested_at"):
            payload["requested_at"] = record.created_at
        if payload.get("finished_at") is None:
            payload["finished_at"] = record.finished_at
        if payload.get("last_heartbeat_at") is None:
            payload["last_heartbeat_at"] = record.last_heartbeat_at
        payload.setdefault("platform", record.platform)
        payload.setdefault("window_hours", record.window_hours)
        if record.article_snapshot_id is not None:
            payload["article_snapshot_id"] = record.article_snapshot_id
        if record.content_hash is not None:
            payload["content_hash"] = record.content_hash
        if record.requested_limit is not None or record.status == OpinionStatus.NOT_REQUESTED.value:
            payload["requested_limit"] = record.requested_limit
        payload["collected_count"] = record.collected_count
        payload["analyzed_count"] = record.analyzed_count
        payload["classification_total"] = record.classification_total
        payload["classified_count"] = record.classified_count
        payload["unclassified_count"] = record.unclassified_count
        payload["status_reason"] = record.status_reason
        payload.setdefault("controversy_points", [])
        stored_comments = self.store.load_opinion_comments(record.id)
        if stored_comments or not payload.get("comments"):
            payload["comments"] = stored_comments
        stored_classifications = self.store.load_opinion_classifications(record.id)
        if stored_classifications or not payload.get("classifications"):
            payload["classifications"] = stored_classifications
        stored_attempts = self.store.load_opinion_attempts(record.id)
        if stored_attempts or not payload.get("attempts"):
            payload["attempts"] = stored_attempts
        payload.setdefault("points", [])
        payload.setdefault("uncertainties", [])
        payload.setdefault("errors", _record_error_payloads(record))
        report = parse_persisted_opinion_report(payload)
        if any(
            plan.trigger_quote not in article.article.content for plan in report.controversy_points
        ):
            raise OpinionSnapshotMismatchError("舆情结果的争议锚点不属于当前文章")
        if any(
            comment.source_url.split("#", 1)[0] != article.article.source_url
            for comment in report.comments
        ):
            raise OpinionSnapshotMismatchError("舆情结果的评论不属于当前文章")
        return report


def _validate_analysis_result(
    summary: object,
    points: object,
    classifications: object,
    controversy_points: list[OpinionPlan],
    comments: list[BilibiliComment],
    run_id: str,
) -> None:
    if not isinstance(summary, str) or not summary.strip():
        raise OpinionResponseError("评论分析缺少摘要", "", code="analysis_response_invalid")
    if not isinstance(classifications, (list, tuple)) or not classifications:
        raise OpinionResponseError(
            "评论分析未返回任何争议点-评论关系",
            "",
            code="analysis_response_invalid",
        )
    if not all(isinstance(item, Classification) for item in classifications):
        raise OpinionResponseError(
            "评论分析包含无法确认的分类关系",
            "",
            code="analysis_response_invalid",
        )

    comment_ids = {item.comment_id for item in comments}
    plan_ids = {item.evidence_id for item in controversy_points}
    relation_keys: set[tuple[str, int, str]] = set()
    for item in classifications:
        if item.run_id != run_id:
            raise OpinionResponseError(
                "评论分析包含不属于当前运行的分类关系",
                "",
                code="analysis_response_invalid",
            )
        relation_key = (item.run_id, item.evidence_id, item.comment_id)
        if (
            item.evidence_id not in plan_ids
            or item.comment_id not in comment_ids
            or relation_key in relation_keys
        ):
            raise OpinionResponseError(
                "评论分析包含无法确认归属的分类关系",
                "",
                code="analysis_response_invalid",
            )
        relation_keys.add(relation_key)

    if not isinstance(points, (list, tuple)) or len(points) != len(controversy_points):
        raise OpinionResponseError(
            "评论分析必须为每个争议点提供摘要",
            "",
            code="analysis_response_invalid",
        )
    if not all(isinstance(item, OpinionPoint) and item.summary.strip() for item in points):
        raise OpinionResponseError(
            "评论分析缺少争议点摘要",
            "",
            code="analysis_response_invalid",
        )
    points_by_id = {item.evidence_id: item for item in points}
    if set(points_by_id) != plan_ids:
        raise OpinionResponseError(
            "评论分析的争议点摘要归属无法确认",
            "",
            code="analysis_response_invalid",
        )
    expected = aggregate_opinion_points(
        controversy_points,
        classifications,
        point_summaries={item.evidence_id: item.summary for item in points},
        representative_comment_ids={
            item.evidence_id: item.representative_comment_ids for item in points
        },
    )
    if any(points_by_id[item.evidence_id] != item for item in expected):
        raise OpinionResponseError(
            "评论分析的聚合结果与分类关系不一致",
            "",
            code="analysis_response_invalid",
        )


def _detect_controversies(
    analyzer: OpinionAnalyzer,
    article,
    timeout: float,
    *,
    deadline: float,
    clock: Clock,
    heartbeat: Callable[[], None],
    attempts: list[Attempt],
) -> list[OpinionPlan]:
    method = analyzer.detect_controversies
    kwargs = _supported_keywords(method, deadline=deadline, heartbeat=heartbeat)
    return _invoke_stage(
        analyzer,
        "opinion_planning",
        deadline=deadline,
        clock=clock,
        timeout=timeout,
        attempts=attempts,
        call=lambda stage_timeout: method(article, stage_timeout, **kwargs),
    )


def _collect_comments(
    collector: CommentCollector,
    source_url: str,
    *,
    window_hours: int,
    limit: int,
    timeout: float,
    deadline: float,
    clock: Clock,
    heartbeat: Callable[[], None],
    attempts: list[Attempt],
) -> list[BilibiliComment]:
    method = collector.collect
    kwargs = _supported_keywords(method, deadline=deadline, heartbeat=heartbeat)
    return _invoke_stage(
        collector,
        "comment_collection",
        deadline=deadline,
        clock=clock,
        timeout=timeout,
        attempts=attempts,
        call=lambda stage_timeout: method(
            source_url,
            window_hours=window_hours,
            limit=limit,
            timeout=stage_timeout,
            **kwargs,
        ),
    )


def _analyze_comments(
    analyzer: OpinionAnalyzer,
    article,
    controversy_points: list[OpinionPlan],
    comments: list[BilibiliComment],
    timeout: float,
    run_id: str,
    *,
    deadline: float,
    clock: Clock,
    heartbeat: Callable[[], None],
    attempts: list[Attempt],
) -> tuple[str, list[OpinionPoint], list[str]]:
    method = analyzer.analyze_comments
    kwargs = _supported_keywords(
        method,
        run_id=run_id,
        deadline=deadline,
        heartbeat=heartbeat,
    )
    return _invoke_stage(
        analyzer,
        "opinion_analysis",
        deadline=deadline,
        clock=clock,
        timeout=timeout,
        attempts=attempts,
        call=lambda stage_timeout: method(
            article,
            controversy_points,
            comments,
            stage_timeout,
            **kwargs,
        ),
    )


def _supported_keywords(method: Callable[..., object], **values: object) -> dict[str, object]:
    parameters = inspect.signature(method).parameters
    accepts_any = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    return {key: value for key, value in values.items() if accepts_any or key in parameters}


def _invoke_stage(
    component: object,
    stage: str,
    *,
    deadline: float,
    clock: Clock,
    timeout: float,
    attempts: list[Attempt],
    call: Callable[[float], object],
) -> object:
    before_count = len(attempts)
    started_at = project_now().isoformat()
    try:
        result = call(timeout)
        if remaining_time(deadline, clock=clock) <= 0:
            raise OpinionTimeoutError(stage)
    except Exception as exc:
        _append_component_attempts(component, attempts)
        if len(attempts) == before_count:
            attempts.append(
                Attempt(
                    stage=stage,
                    attempt=1,
                    started_at=started_at,
                    finished_at=project_now().isoformat(),
                    outcome="timed_out" if isinstance(exc, TimeoutError) else "failed",
                    error_code=str(getattr(exc, "code", "failed")),
                    error_summary=error_summary(exc),
                )
            )
        raise
    _append_component_attempts(component, attempts)
    if len(attempts) == before_count:
        attempts.append(
            Attempt(
                stage=stage,
                attempt=1,
                started_at=started_at,
                finished_at=project_now().isoformat(),
                outcome="succeeded",
            )
        )
    return result


def _append_component_attempts(component: object, attempts: list[Attempt]) -> None:
    component_attempts = getattr(component, "last_attempts", ())
    if not isinstance(component_attempts, (list, tuple)):
        return
    known = {id(item) for item in attempts}
    attempts.extend(
        item for item in component_attempts if isinstance(item, Attempt) and id(item) not in known
    )


def _article_identity(
    article_id: str,
    snapshot_id: str | None,
    content_hash: str | None,
) -> ArticleSnapshotIdentity:
    if snapshot_id is None or content_hash is None:
        raise OpinionSnapshotMismatchError("文章缺少可复用的快照身份")
    return ArticleSnapshotIdentity(article_id, snapshot_id, content_hash)


def _result_payload(
    *,
    article_id: str,
    source_url: str,
    identity: ArticleSnapshotIdentity,
    requested_limit: int,
    status: OpinionStatus,
    status_reason: str,
    run_id: str,
    summary: str,
    controversy_points: list[OpinionPlan],
    comments: list[BilibiliComment],
    analyzed_count: int,
    classifications: list[Classification],
    points: list[OpinionPoint],
    uncertainties: list[str],
    errors: list[dict[str, object]],
    attempts: list[Attempt],
) -> dict[str, object]:
    classified_count = sum(
        item.classification_status is ClassificationStatus.CLASSIFIED for item in classifications
    )
    return {
        "product_name": "哔哩哔哩公开评论样本分析",
        "article_id": article_id,
        "article_snapshot_id": identity.article_snapshot_id,
        "content_hash": identity.content_hash,
        "source_url": source_url,
        "platform": "bilibili",
        "window_hours": OPINION_WINDOW_HOURS,
        "requested_limit": requested_limit,
        "collected_count": len(comments),
        "analyzed_count": analyzed_count,
        "classification_total": len(classifications),
        "classified_count": classified_count,
        "unclassified_count": len(classifications) - classified_count,
        "status": status.value,
        "status_reason": status_reason,
        "run_id": run_id,
        "requested_at": None,
        "finished_at": None,
        "last_heartbeat_at": None,
        "controversy_points": [_plan_payload(item) for item in controversy_points],
        "comments": [_comment_payload(item) for item in comments],
        "classifications": [_classification_payload(item) for item in classifications],
        "summary": summary,
        "points": [_point_payload(item) for item in points],
        "uncertainties": uncertainties,
        "errors": errors,
        "attempts": [_attempt_payload(item) for item in attempts],
    }


def _plan_payload(item: OpinionPlan) -> dict[str, object]:
    return {
        "evidence_id": item.evidence_id,
        "trigger_quote": item.trigger_quote,
        "question": item.question,
        "platform": item.platform,
        "window_hours": item.window_hours,
        "queries": [{"query": query.query, "purpose": query.purpose} for query in item.queries],
    }


def _comment_payload(comment: BilibiliComment) -> dict[str, object]:
    return {
        "comment_id": comment.comment_id,
        "source_url": comment.source_url,
        "author": comment.author,
        "content": comment.content,
        "likes": comment.likes,
        "published_at": comment.published_at.isoformat() if comment.published_at else None,
    }


def _classification_payload(item: Classification) -> dict[str, object]:
    return {
        "run_id": item.run_id,
        "evidence_id": item.evidence_id,
        "comment_id": item.comment_id,
        "classification_status": item.classification_status.value,
        "stance": item.stance.value if item.stance else None,
        "error_code": item.error_code,
    }


def _point_payload(item: OpinionPoint) -> dict[str, object]:
    return {
        "evidence_id": item.evidence_id,
        "question": item.question,
        "summary": item.summary,
        "stance_counts": item.stance_counts,
        "representative_comment_ids": list(item.representative_comment_ids),
    }


def _attempt_payload(item: Attempt) -> dict[str, object]:
    return {
        "stage": item.stage,
        "attempt": item.attempt,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
        "outcome": item.outcome,
        "error_code": item.error_code,
        "error_summary": _redact_cookie(item.error_summary),
    }


def _redact_cookie(value: str | None) -> str | None:
    if value is None:
        return None
    cookie = os.getenv("BILIBILI_COOKIE") or ""
    if cookie:
        value = value.replace(cookie, "[REDACTED]")
    return value[:500]


def _error_payload_from_exception(
    error: BaseException,
    *,
    fallback_stage: str = "unknown",
) -> dict[str, object]:
    code = str(getattr(error, "code", "timeout" if isinstance(error, TimeoutError) else "failed"))
    stage = str(getattr(error, "stage", "") or fallback_stage or "unknown")
    retryable = bool(getattr(error, "retryable", code in {"retry_exhausted", "timeout"}))
    attempt = getattr(error, "attempt", None)
    if type(attempt) is not int or attempt < 1:
        attempt = None
    return {
        "code": code,
        "stage": stage,
        "message": error_summary(
            error,
            secrets=((os.getenv("BILIBILI_COOKIE") or ""),),
        ),
        "retryable": retryable,
        "attempt": attempt,
    }


def _status_reason_from_exception(
    error: BaseException,
    *,
    has_partial_result: bool,
    fallback_stage: str = "unknown",
) -> str:
    code = str(getattr(error, "code", "failed"))
    if isinstance(error, TimeoutError) or code == "timeout":
        return "timeout"
    if isinstance(error, OpinionRetryExhaustedError) or code == "retry_exhausted":
        return "retry_exhausted"
    if not has_partial_result:
        return "failed"
    stage = str(getattr(error, "stage", "") or fallback_stage or "unknown")
    if stage in {"comment_collection", "aid_resolution"}:
        return "partial_collection"
    if stage in {"opinion_planning", "opinion_analysis", "classification"}:
        return "partial_classification"
    return "failed"


def _record_error_payloads(record: OpinionRunRecord) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for item in record.errors:
        if not isinstance(item, dict) or not item.get("message"):
            continue
        payloads.append(
            {
                "code": str(item.get("code", "failed")),
                "stage": str(item.get("stage", "persistence")),
                "message": error_summary(
                    RuntimeError(str(item["message"])),
                    secrets=((os.getenv("BILIBILI_COOKIE") or ""),),
                ),
                "retryable": bool(item.get("retryable", False)),
                "attempt": item.get("attempt"),
            }
        )
    return payloads


def _default_status_reason(status: str) -> str:
    if status == OpinionStatus.NOT_REQUESTED.value:
        return "not_requested"
    if status == OpinionStatus.RUNNING.value:
        return "running"
    if status == OpinionStatus.FAILED.value:
        return "failed"
    return status
