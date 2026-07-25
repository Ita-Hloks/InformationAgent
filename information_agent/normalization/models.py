from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..contracts import ContentType


@dataclass(frozen=True, slots=True)
class NormalizedArticle:
    """Validated article data ready for deterministic selection."""

    article_id: str
    source_url: str
    title: str
    content: str
    feed_url: str | None
    site_url: str | None
    source_type: str
    author: str | None
    categories: tuple[str, ...]
    language: str | None
    content_type: ContentType
    content_chunks: tuple[str, ...]
    published_at: datetime | None
    collected_at: datetime
    processing_warnings: tuple[str, ...] = field(default_factory=tuple)
