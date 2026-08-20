from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ..investigation import OPINION_PLATFORM, OPINION_WINDOW_HOURS, OpinionPlan

PRODUCT_NAME = "哔哩哔哩公开评论样本分析"
STANCE_KEYS = ("support", "oppose", "mixed", "unclear")
MAX_COMMENT_LIMIT = 200
MAX_REPRESENTATIVE_COMMENTS = 5


class OpinionStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class Stance(StrEnum):
    SUPPORT = "support"
    OPPOSE = "oppose"
    MIXED = "mixed"
    UNCLEAR = "unclear"


class ClassificationStatus(StrEnum):
    CLASSIFIED = "classified"
    UNCLASSIFIED = "unclassified"


class OpinionErrorCode(StrEnum):
    ARTICLE_NOT_FOUND = "article_not_found"
    ARTICLE_SNAPSHOT_MISMATCH = "article_snapshot_mismatch"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_TARGET = "unsupported_target"
    PLANNING_RESPONSE_INVALID = "planning_response_invalid"
    BILIBILI_RESPONSE_INVALID = "bilibili_response_invalid"
    COMMENT_COLLECTION_FAILED = "comment_collection_failed"
    ANALYSIS_RESPONSE_INVALID = "analysis_response_invalid"
    CLASSIFICATION_FAILED = "classification_failed"
    TIMEOUT = "timeout"
    RETRY_EXHAUSTED = "retry_exhausted"
    STALE_RUNNING = "stale_running"
    STORAGE_FAILED = "storage_failed"


_STATUS_REASONS: dict[OpinionStatus, frozenset[str]] = {
    OpinionStatus.NOT_REQUESTED: frozenset({"not_requested"}),
    OpinionStatus.RUNNING: frozenset({"running"}),
    OpinionStatus.COMPLETED: frozenset({"completed", "no_controversy_points", "sample_empty"}),
    OpinionStatus.PARTIAL: frozenset(
        {"partial_collection", "partial_classification", "timeout", "retry_exhausted"}
    ),
    OpinionStatus.FAILED: frozenset({"timeout", "retry_exhausted", "stale_running", "failed"}),
}


@dataclass(frozen=True, slots=True)
class BilibiliComment:
    comment_id: str
    source_url: str
    author: str
    content: str
    likes: int
    published_at: datetime | None

    def __post_init__(self) -> None:
        for name in ("comment_id", "source_url", "author", "content"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} 不能为空")
        if type(self.likes) is not int or self.likes < 0:
            raise ValueError("likes 必须是非负整数")
        if self.published_at is not None and self.published_at.tzinfo is None:
            raise ValueError("published_at 必须包含时区")


@dataclass(frozen=True, slots=True)
class Classification:
    run_id: str
    evidence_id: int
    comment_id: str
    classification_status: ClassificationStatus
    stance: Stance | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id 不能为空")
        if type(self.evidence_id) is not int or self.evidence_id <= 0:
            raise ValueError("evidence_id 必须是正整数")
        if not isinstance(self.comment_id, str) or not self.comment_id.strip():
            raise ValueError("comment_id 不能为空")
        try:
            status = ClassificationStatus(self.classification_status)
        except (TypeError, ValueError) as exc:
            raise ValueError("classification_status 不是支持的分类状态") from exc
        object.__setattr__(self, "classification_status", status)

        if status is ClassificationStatus.CLASSIFIED:
            if self.stance is None:
                raise ValueError("classified 分类必须包含 stance")
            try:
                stance = Stance(self.stance)
            except (TypeError, ValueError) as exc:
                raise ValueError("stance 不是支持的立场") from exc
            object.__setattr__(self, "stance", stance)
            if self.error_code is not None:
                raise ValueError("classified 分类不能包含 error_code")
            return

        if self.stance is not None:
            raise ValueError("unclassified 分类不能包含 stance")
        if not isinstance(self.error_code, str) or not self.error_code.strip():
            raise ValueError("unclassified 分类必须包含 error_code")
        try:
            OpinionErrorCode(self.error_code)
        except ValueError as exc:
            raise ValueError("unclassified 分类的 error_code 无效") from exc


@dataclass(frozen=True, slots=True)
class OpinionPoint:
    evidence_id: int
    question: str
    summary: str
    stance_counts: dict[str, int]
    representative_comment_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.evidence_id) is not int or self.evidence_id <= 0:
            raise ValueError("evidence_id 必须是正整数")
        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError("question 不能为空")
        if not isinstance(self.summary, str):
            raise ValueError("summary 必须是字符串")
        counts = dict(self.stance_counts)
        if set(counts) != set(STANCE_KEYS):
            raise ValueError("stance_counts 必须包含四种固定立场")
        for key in STANCE_KEYS:
            value = counts[key]
            if type(value) is not int or value < 0:
                raise ValueError("stance_counts 的值必须是非负整数")
        object.__setattr__(self, "stance_counts", counts)
        representatives = tuple(self.representative_comment_ids)
        if len(representatives) > MAX_REPRESENTATIVE_COMMENTS:
            raise ValueError("代表评论数量超出限制")
        if len(set(representatives)) != len(representatives) or any(
            not isinstance(value, str) or not value.strip() for value in representatives
        ):
            raise ValueError("代表评论编号必须唯一且不能为空")
        object.__setattr__(self, "representative_comment_ids", representatives)


@dataclass(frozen=True, slots=True)
class OpinionError:
    code: str
    stage: str
    message: str
    retryable: bool
    attempt: int | None = None

    def __post_init__(self) -> None:
        for name in ("code", "stage", "message"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} 不能为空")
        if type(self.retryable) is not bool:
            raise ValueError("retryable 必须是布尔值")
        if self.attempt is not None and (type(self.attempt) is not int or self.attempt < 1):
            raise ValueError("attempt 必须是正整数或 null")


@dataclass(frozen=True, slots=True)
class Attempt:
    stage: str
    attempt: int
    started_at: str
    finished_at: str
    outcome: str
    error_code: str | None = None
    error_summary: str | None = None

    def __post_init__(self) -> None:
        if self.stage not in {
            "opinion_planning",
            "aid_resolution",
            "comment_collection",
            "opinion_analysis",
            "classification",
        }:
            raise ValueError("stage 不是支持的尝试阶段")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt 必须是正整数")
        if not isinstance(self.started_at, str) or not self.started_at.strip():
            raise ValueError("started_at 不能为空")
        if not isinstance(self.finished_at, str) or not self.finished_at.strip():
            raise ValueError("finished_at 不能为空")
        if self.outcome not in {"succeeded", "failed", "timed_out", "skipped"}:
            raise ValueError("outcome 不是支持的尝试结果")


@dataclass(frozen=True, slots=True)
class CommentAnalysisResult:
    """评论分析边界归一化后的结果，计数和代表评论由代码重新计算。"""

    summary: str
    classifications: tuple[Classification, ...]
    uncertainties: tuple[str, ...]
    point_summaries: dict[int, str] = field(default_factory=dict)
    representative_comment_ids: dict[int, tuple[str, ...]] = field(default_factory=dict)

    def __iter__(self):
        yield self.summary
        yield list(self.classifications)
        yield list(self.uncertainties)


@dataclass(frozen=True, slots=True)
class OpinionReport:
    article_id: str
    source_url: str
    status: OpinionStatus
    product_name: str = PRODUCT_NAME
    article_snapshot_id: str | None = None
    content_hash: str | None = None
    platform: str = OPINION_PLATFORM
    window_hours: int = OPINION_WINDOW_HOURS
    requested_limit: int | None = None
    collected_count: int = 0
    analyzed_count: int = 0
    classification_total: int = 0
    classified_count: int = 0
    unclassified_count: int = 0
    status_reason: str = "not_requested"
    run_id: str | None = None
    requested_at: str | None = None
    finished_at: str | None = None
    last_heartbeat_at: str | None = None
    controversy_points: tuple[OpinionPlan, ...] = field(default_factory=tuple)
    comments: tuple[BilibiliComment, ...] = field(default_factory=tuple)
    classifications: tuple[Classification, ...] = field(default_factory=tuple)
    summary: str = ""
    points: tuple[OpinionPoint, ...] = field(default_factory=tuple)
    uncertainties: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[OpinionError, ...] = field(default_factory=tuple)
    attempts: tuple[Attempt, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        try:
            status = OpinionStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError("status 不是支持的舆情运行状态") from exc
        object.__setattr__(self, "status", status)
        if self.product_name != PRODUCT_NAME:
            raise ValueError(f"产品名称固定为 {PRODUCT_NAME}")
        if self.platform != OPINION_PLATFORM:
            raise ValueError(f"舆情平台固定为 {OPINION_PLATFORM}")
        if self.window_hours != OPINION_WINDOW_HOURS:
            raise ValueError(f"舆情时间窗固定为 {OPINION_WINDOW_HOURS} 小时")
        if not isinstance(self.article_id, str) or not self.article_id.strip():
            raise ValueError("article_id 不能为空")
        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise ValueError("source_url 不能为空")
        if (self.article_snapshot_id is None) != (self.content_hash is None):
            raise ValueError("article_snapshot_id 和 content_hash 必须同时存在")
        if self.requested_limit is not None and (
            type(self.requested_limit) is not int
            or not 1 <= self.requested_limit <= MAX_COMMENT_LIMIT
        ):
            raise ValueError("requested_limit 必须在 1 到 200 之间")
        counts = (
            self.collected_count,
            self.analyzed_count,
            self.classification_total,
            self.classified_count,
            self.unclassified_count,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("舆情数量必须是非负整数")
        if self.requested_limit is not None and self.collected_count > self.requested_limit:
            raise ValueError("collected_count 不能超过 requested_limit")
        if self.analyzed_count > self.collected_count:
            raise ValueError("analyzed_count 不能超过 collected_count")
        if self.classification_total != self.classified_count + self.unclassified_count:
            raise ValueError("分类总数必须等于已分类数与未分类数之和")
        if self.classification_total != len(self.classifications):
            raise ValueError("classification_total 必须等于 classifications 长度")
        actual_classified = sum(
            item.classification_status is ClassificationStatus.CLASSIFIED
            for item in self.classifications
        )
        if actual_classified != self.classified_count:
            raise ValueError("classified_count 与逐条分类不一致")
        if len(self.comments) != self.collected_count:
            raise ValueError("collected_count 必须等于 comments 长度")
        comment_ids = [item.comment_id for item in self.comments]
        if len(set(comment_ids)) != len(comment_ids):
            raise ValueError("同一运行内 comment_id 不能重复")
        plan_ids = [item.evidence_id for item in self.controversy_points]
        if len(set(plan_ids)) != len(plan_ids):
            raise ValueError("争议点编号不能重复")
        if self.classifications:
            if self.run_id is not None and any(
                item.run_id != self.run_id for item in self.classifications
            ):
                raise ValueError("逐条分类必须属于当前运行")
            comment_id_set = set(comment_ids)
            plan_id_set = set(plan_ids)
            if any(
                item.comment_id not in comment_id_set or item.evidence_id not in plan_id_set
                for item in self.classifications
            ):
                raise ValueError("逐条分类引用了当前运行之外的对象")
            relation_keys = [
                (item.run_id, item.evidence_id, item.comment_id) for item in self.classifications
            ]
            if len(set(relation_keys)) != len(relation_keys):
                raise ValueError("同一争议点-评论关系不能重复")
        if self.points and self.classifications:
            _validate_points_against_classifications(
                self.points, self.classifications, self.controversy_points
            )
        allowed_reasons = _STATUS_REASONS[status]
        if self.status_reason not in allowed_reasons:
            raise ValueError(f"{status.value} 不允许使用状态原因 {self.status_reason}")
        if status is OpinionStatus.NOT_REQUESTED:
            if any(value != 0 for value in counts) or self.requested_limit is not None:
                raise ValueError("not_requested 状态不能包含运行数量")
            if any(
                value is not None
                for value in (
                    self.run_id,
                    self.requested_at,
                    self.finished_at,
                    self.last_heartbeat_at,
                )
            ):
                raise ValueError("not_requested 状态不能包含运行时间")


def aggregate_opinion_points(
    controversy_points: Sequence[OpinionPlan],
    classifications: Sequence[Classification],
    *,
    point_summaries: Mapping[int, str] | None = None,
    representative_comment_ids: Mapping[int, Sequence[str]] | None = None,
) -> tuple[OpinionPoint, ...]:
    """仅从已确认的逐条分类关系计算争议点聚合结果。"""

    plans_by_id = {plan.evidence_id: plan for plan in controversy_points}
    if len(plans_by_id) != len(controversy_points):
        raise ValueError("争议点编号不能重复")
    seen_relations: set[tuple[str, int, str]] = set()
    run_ids: set[str] = set()
    relationships_by_point: dict[int, list[Classification]] = {key: [] for key in plans_by_id}
    for item in classifications:
        if item.evidence_id not in plans_by_id:
            raise ValueError("分类关系引用了不存在的争议点")
        relation_key = (item.run_id, item.evidence_id, item.comment_id)
        run_ids.add(item.run_id)
        if relation_key in seen_relations:
            raise ValueError("同一争议点-评论关系不能重复")
        seen_relations.add(relation_key)
        relationships_by_point[item.evidence_id].append(item)
    if len(run_ids) > 1:
        raise ValueError("分类关系不能混用多个运行")

    summaries = point_summaries or {}
    representatives_by_point = representative_comment_ids or {}
    points: list[OpinionPoint] = []
    for evidence_id, plan in plans_by_id.items():
        relations = relationships_by_point[evidence_id]
        counts = {key: 0 for key in STANCE_KEYS}
        classified_ids: list[str] = []
        for relation in relations:
            if relation.classification_status is ClassificationStatus.CLASSIFIED:
                assert relation.stance is not None
                counts[relation.stance.value] += 1
                classified_ids.append(relation.comment_id)

        raw_representatives = representatives_by_point.get(evidence_id)
        if raw_representatives is None:
            selected_representatives = tuple(dict.fromkeys(classified_ids))[
                :MAX_REPRESENTATIVE_COMMENTS
            ]
        else:
            selected_representatives = tuple(raw_representatives)
            if len(selected_representatives) > MAX_REPRESENTATIVE_COMMENTS:
                raise ValueError("代表评论数量超出限制")
            if len(set(selected_representatives)) != len(selected_representatives):
                raise ValueError("代表评论不能重复")
            classified_id_set = set(classified_ids)
            if any(comment_id not in classified_id_set for comment_id in selected_representatives):
                raise ValueError("代表评论必须属于对应争议点的已分类关系")
        points.append(
            OpinionPoint(
                evidence_id=evidence_id,
                question=plan.question,
                summary=str(summaries.get(evidence_id, "")),
                stance_counts=counts,
                representative_comment_ids=selected_representatives,
            )
        )
    return tuple(points)


def _validate_points_against_classifications(
    points: Sequence[OpinionPoint],
    classifications: Sequence[Classification],
    controversy_points: Sequence[OpinionPlan],
) -> None:
    expected = aggregate_opinion_points(controversy_points, classifications)
    expected_by_id = {item.evidence_id: item for item in expected}
    seen: set[int] = set()
    for point in points:
        if point.evidence_id in seen or point.evidence_id not in expected_by_id:
            raise ValueError("聚合结果引用了重复或不存在的争议点")
        seen.add(point.evidence_id)
        if point.stance_counts != expected_by_id[point.evidence_id].stance_counts:
            raise ValueError("争议点立场计数必须由逐条分类计算")
        valid_representatives = {
            item.comment_id
            for item in classifications
            if item.evidence_id == point.evidence_id
            and item.classification_status is ClassificationStatus.CLASSIFIED
        }
        if any(item not in valid_representatives for item in point.representative_comment_ids):
            raise ValueError("代表评论必须属于对应争议点的已分类关系")
