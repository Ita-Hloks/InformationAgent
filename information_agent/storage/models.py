from __future__ import annotations

from dataclasses import dataclass

from ..collection import RawFeedEntry
from ..contracts import CollectionReport


@dataclass(frozen=True, slots=True)
class PersistedCollection:
    """已提交到数据库的一次粗处理结果。"""

    run_id: str
    report: CollectionReport


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
