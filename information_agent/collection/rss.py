from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import aiohttp
import feedparser

from ..common import normalize_url
from ..contracts import ContentType
from .models import FeedFetchResult, RawFeedEntry

MAX_FEED_BYTES = 5 * 1024 * 1024


def _plain_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(without_tags)).strip()


def fetch_feed(feed_url: str, timeout: float = 15) -> list[RawFeedEntry]:
    return fetch_feed_with_cache(feed_url, timeout).entries


def fetch_feed_with_cache(
    feed_url: str,
    timeout: float = 15,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FeedFetchResult:
    normalized_feed_url = normalize_url(feed_url)
    if normalized_feed_url is None:
        raise ValueError("RSS 地址必须使用 http 或 https")

    headers = {"User-Agent": "InformationAgent/0.1 RSS-MVP"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    request = Request(
        normalized_feed_url,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_FEED_BYTES:
                raise ValueError("RSS 响应超过 5 MiB 限制")
            payload = response.read(MAX_FEED_BYTES + 1)
            response_etag = _header(response.headers, "ETag")
            response_last_modified = _header(response.headers, "Last-Modified")
    except HTTPError as error:
        if error.code == 304:
            return FeedFetchResult(
                normalized_feed_url,
                [],
                etag,
                last_modified,
                not_modified=True,
            )
        raise
    if len(payload) > MAX_FEED_BYTES:
        raise ValueError("RSS 响应超过 5 MiB 限制")

    return FeedFetchResult(
        normalized_feed_url,
        _parse_feed(payload, normalized_feed_url),
        response_etag,
        response_last_modified,
    )


async def fetch_feed_async(
    feed_url: str,
    timeout: float,
    *,
    session: aiohttp.ClientSession,
) -> list[RawFeedEntry]:
    """Fetch one RSS feed with an aiohttp total request timeout."""
    normalized_feed_url = normalize_url(feed_url)
    if normalized_feed_url is None:
        raise ValueError("RSS 地址必须使用 http 或 https")

    request_timeout = aiohttp.ClientTimeout(total=timeout)
    async with session.get(
        normalized_feed_url,
        headers={"User-Agent": "InformationAgent/0.1 RSS-MVP"},
        timeout=request_timeout,
    ) as response:
        response.raise_for_status()
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_FEED_BYTES:
            raise ValueError("RSS 响应超过 5 MiB 限制")
        payload = await _read_feed_payload(response)

    return _parse_feed(payload, normalized_feed_url)


async def _read_feed_payload(response: aiohttp.ClientResponse) -> bytes:
    payload = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        payload.extend(chunk)
        if len(payload) > MAX_FEED_BYTES:
            raise ValueError("RSS 响应超过 5 MiB 限制")
    return bytes(payload)


def _parse_feed(payload: bytes, normalized_feed_url: str) -> list[RawFeedEntry]:
    feed = feedparser.parse(payload)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise ValueError(f"RSS 解析失败：{feed.bozo_exception}")

    items: list[RawFeedEntry] = []
    site_url = normalize_url(str(feed.feed.get("link") or ""))
    feed_language = _normalize_language(str(feed.feed.get("language") or ""))
    for entry in feed.entries:
        source_url = normalize_url(str(entry.get("link") or entry.get("id") or ""))
        if source_url is None:
            continue
        content, content_type = _entry_content(entry)
        items.append(
            RawFeedEntry(
                source_url=source_url,
                title=_plain_text(str(entry.get("title") or "无标题")),
                content=content,
                feed_url=normalized_feed_url,
                site_url=site_url,
                author=_optional_text(entry.get("author") or entry.get("dc_creator")),
                categories=tuple(_entry_categories(entry)),
                language=_normalize_language(str(entry.get("language") or "")) or feed_language,
                content_type=content_type,
                published_at=entry.get("published") or entry.get("updated"),
                entry_id=_entry_id(entry),
                updated_at=entry.get("updated"),
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


def _entry_id(entry: dict[str, Any]) -> str | None:
    value = str(entry.get("id") or entry.get("guid") or "").strip()
    return value or None


def _header(headers: Any, name: str) -> str | None:
    value = headers.get(name)
    return str(value).strip() if value else None
