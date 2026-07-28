from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..contracts import ContentType, project_now


@dataclass(frozen=True, slots=True)
class RawFeedEntry:
    """Unnormalized article data produced by a feed collector."""

    source_url: str
    title: str
    content: str
    feed_url: str | None = None
    site_url: str | None = None
    source_type: str = "rss"
    author: str | None = None
    categories: tuple[str, ...] = field(default_factory=tuple)
    language: str | None = None
    content_type: ContentType = ContentType.UNKNOWN
    published_at: str | datetime | None = None
    entry_id: str | None = None
    updated_at: str | datetime | None = None
    collected_at: datetime = field(default_factory=project_now)


@dataclass(frozen=True, slots=True)
class FeedFetchResult:
    """RSS 请求结果及其 HTTP 缓存元数据。"""

    feed_url: str
    entries: list[RawFeedEntry]
    etag: str | None
    last_modified: str | None
    not_modified: bool = False
