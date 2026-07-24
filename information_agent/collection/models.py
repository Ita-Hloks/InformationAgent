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
    collected_at: datetime = field(default_factory=project_now)
