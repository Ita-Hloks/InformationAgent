from __future__ import annotations

import hashlib
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

from ..collection import RawFeedEntry
from ..common import CONTENT_BATCH_CHARS, normalize_url, split_content
from ..contracts import PROJECT_TIMEZONE
from .models import NormalizedArticle

MIN_CONTENT_CHARS = 20


def parse_published_at(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        parsed = value
    else:
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, OverflowError):
                return None

    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=PROJECT_TIMEZONE)
    return parsed.astimezone(PROJECT_TIMEZONE).replace(microsecond=0)


def normalize_evidence(
    items: list[RawFeedEntry],
    *,
    min_content_chars: int = MIN_CONTENT_CHARS,
    content_batch_chars: int = CONTENT_BATCH_CHARS,
) -> list[NormalizedArticle]:
    if min_content_chars <= 0 or content_batch_chars <= 0:
        raise ValueError("正文长度限制无效")

    normalized: list[NormalizedArticle] = []
    for item in items:
        source_url = normalize_url(item.source_url)
        if source_url is None:
            continue
        feed_url = normalize_url(item.feed_url) if item.feed_url else None
        site_url = normalize_url(item.site_url) if item.site_url else None

        title = _normalize_text(item.title)
        content = _normalize_text(item.content)
        if len(content) < min_content_chars:
            continue

        content_chunks = split_content(content, content_batch_chars)
        processing_warnings: list[str] = []
        if len(content_chunks) > 1:
            processing_warnings.append(
                f"正文已拆分为 {len(content_chunks)} 个批次，每批最多 {content_batch_chars} 字"
            )

        normalized.append(
            NormalizedArticle(
                article_id=_article_id(source_url),
                source_url=source_url,
                feed_url=feed_url,
                site_url=site_url,
                source_type=item.source_type.strip().casefold() or "rss",
                title=title,
                content=content,
                author=item.author,
                categories=tuple(item.categories),
                language=item.language,
                content_type=item.content_type,
                content_chunks=tuple(content_chunks),
                published_at=parse_published_at(item.published_at),
                collected_at=parse_published_at(item.collected_at) or item.collected_at,
                processing_warnings=tuple(processing_warnings),
                image_url=normalize_url(item.image_url) if item.image_url else None,
            )
        )
    return normalized


def _normalize_text(value: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _article_id(source_url: str) -> str:
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    return f"article-{digest}"
