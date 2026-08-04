from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ..collection import RawFeedEntry
from ..contracts import CollectionReport

if TYPE_CHECKING:
    from ..investigation import PlanningReport


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
