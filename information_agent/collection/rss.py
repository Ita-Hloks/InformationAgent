from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.request import Request, urlopen

import feedparser

from ..contracts import ContentType, Evidence
from .normalization import normalize_url, parse_published_at

MAX_FEED_BYTES = 5 * 1024 * 1024


def _plain_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(without_tags)).strip()


def fetch_feed(feed_url: str, timeout: float = 15) -> list[Evidence]:
    normalized_feed_url = normalize_url(feed_url)
    if normalized_feed_url is None:
        raise ValueError("RSS 地址必须使用 http 或 https")

    request = Request(
        normalized_feed_url,
        headers={"User-Agent": "InformationAgent/0.1 RSS-MVP"},
    )
    with urlopen(request, timeout=timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_FEED_BYTES:
            raise ValueError("RSS 响应超过 5 MiB 限制")
        payload = response.read(MAX_FEED_BYTES + 1)
    if len(payload) > MAX_FEED_BYTES:
        raise ValueError("RSS 响应超过 5 MiB 限制")

    feed = feedparser.parse(payload)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise ValueError(f"RSS 解析失败：{feed.bozo_exception}")

    items: list[Evidence] = []
    site_url = normalize_url(str(feed.feed.get("link") or ""))
    feed_language = _normalize_language(str(feed.feed.get("language") or ""))
    for entry in feed.entries:
        source_url = normalize_url(str(entry.get("link") or entry.get("id") or ""))
        if source_url is None:
            continue
        content, content_type = _entry_content(entry)
        items.append(
            Evidence(
                source_url=source_url,
                title=_plain_text(str(entry.get("title") or "无标题")),
                content=content,
                feed_url=normalized_feed_url,
                site_url=site_url,
                author=_optional_text(entry.get("author") or entry.get("dc_creator")),
                categories=_entry_categories(entry),
                language=_normalize_language(str(entry.get("language") or "")) or feed_language,
                content_type=content_type,
                published_at=parse_published_at(entry.get("published") or entry.get("updated")),
            )
        )
    return items


def _entry_content(entry: dict[str, Any]) -> tuple[str, ContentType]:
    content_blocks = entry.get("content") or []
    if content_blocks:
        content = _plain_text(str(content_blocks[0].get("value", "")))
        if content:
            return content, ContentType.RSS_CONTENT
    summary = _plain_text(str(entry.get("summary") or entry.get("description") or ""))
    return summary, ContentType.RSS_SUMMARY


def _entry_categories(entry: dict[str, Any]) -> list[str]:
    categories = []
    for tag in entry.get("tags") or []:
        value = _plain_text(str(tag.get("term") or ""))
        if value and value not in categories:
            categories.append(value)
    return categories


def _normalize_language(value: str) -> str | None:
    normalized = value.strip().replace("_", "-").casefold()
    return normalized or None


def _optional_text(value: Any) -> str | None:
    normalized = _plain_text(str(value or ""))
    return normalized or None
