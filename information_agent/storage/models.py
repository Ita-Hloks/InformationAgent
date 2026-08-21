from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..collection import RawFeedEntry
from ..contracts import CollectionReport

if TYPE_CHECKING:
    from ..investigation import PlanningReport
    from ..normalization import NormalizedArticle


class ArticleSnapshotMismatchError(ValueError):
    """历史结果或规划不属于当前文章正文快照。"""


class ResearchRunNotFoundError(ValueError):
    """研究运行不存在。"""


class ResearchRunNotReadyError(ValueError):
    """研究运行尚未产生可供下游分析的结果。"""


@dataclass(frozen=True, slots=True)
class PersistedCollection:
    """已提交到数据库的一次粗处理结果。"""

    run_id: str
    report: CollectionReport


@dataclass(frozen=True, slots=True)
class PersistedPlanning:
    """已写入数据库的一次问题规划结果。"""

    run_id: str
    planning_run_id: str
    report: PlanningReport


class AnalysisRunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class AnalysisAttemptStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    id: str
    research_run_id: str
    analysis_type: str
    status: AnalysisRunStatus
    current_step_key: str | None
    config: dict[str, Any]
    idempotency_key: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    errors: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class AnalysisStep:
    id: str
    analysis_run_id: str
    position: int
    step_key: str
    status: AnalysisStepStatus
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    error: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class AnalysisAttempt:
    id: str
    analysis_step_id: str
    attempt_no: int
    operation: str
    idempotency_key: str
    request_hash: str
    status: AnalysisAttemptStatus
    started_at: str
    finished_at: str | None
    error: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class AnalysisArtifact:
    id: str
    analysis_run_id: str
    artifact_key: str
    step_id: str | None
    attempt_id: str | None
    kind: str
    content_type: str
    payload: Any
    metadata: dict[str, Any]
    content_hash: str
    created_at: str


@dataclass(frozen=True, slots=True)
class AnalysisState:
    run: AnalysisRun
    steps: tuple[AnalysisStep, ...]
    attempts: tuple[AnalysisAttempt, ...]
    artifacts: tuple[AnalysisArtifact, ...]


@dataclass(frozen=True, slots=True)
class FeedState:
    feed_id: str
    feed_url: str
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True, slots=True)
class FeedObservation:
    state: FeedState
    etag: str | None
    last_modified: str | None
    not_modified: bool
    new_entries: list[RawFeedEntry]


@dataclass(frozen=True, slots=True)
class FeedSubscription:
    feed_id: str
    feed_url: str
    title: str
    site_url: str | None
    subscribed_at: str
    last_refreshed_at: str | None
    last_error: str | None
    article_count: int
    unread_count: int


@dataclass(frozen=True, slots=True)
class ReaderArticleState:
    article_id: str
    is_read: bool
    is_saved: bool
    read_at: str | None
    saved_at: str | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class ReaderArticle:
    feed_id: str
    article: NormalizedArticle
    is_read: bool = False
    is_saved: bool = False
    snapshot_id: str | None = None
    content_hash: str | None = None


@dataclass(frozen=True, slots=True)
class OpinionRunRecord:
    id: str
    article_id: str
    platform: str
    window_hours: int
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    errors: tuple[dict[str, Any], ...]
    result_payload: dict[str, Any] | None
    last_heartbeat_at: str | None = None
    timeout_seconds: float = 300.0
    article_snapshot_id: str | None = None
    content_hash: str | None = None
    requested_limit: int | None = None
    collected_count: int = 0
    analyzed_count: int = 0
    classification_total: int = 0
    classified_count: int = 0
    unclassified_count: int = 0
    status_reason: str = "failed"
    attempts: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class ResearchRunSummary:
    """Safe, aggregate-only view of a persisted research run."""

    run_id: str
    topic: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    feed_count: int
    snapshot_count: int
    selected_evidence_count: int
    collection_error_count: int
