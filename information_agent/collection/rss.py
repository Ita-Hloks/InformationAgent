from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import aiohttp
import feedparser

from ..common import (
    ContentBlock,
    content_blocks_to_text,
    normalize_url,
    parse_content_blocks,
)
from ..contracts import ContentType
from ._http import content_length_exceeds_limit
from .images import extract_image_url_from_html, resolve_image_url
from .models import FeedFetchResult, RawFeedEntry

MAX_FEED_BYTES = 5 * 1024 * 1024


def _validate_timeout(timeout: float) -> None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")


def _plain_text(value: str) -> str:
    return content_blocks_to_text(parse_content_blocks(value))


def fetch_feed(feed_url: str, timeout: float = 15) -> list[RawFeedEntry]:
    return fetch_feed_with_cache(feed_url, timeout).entries


def fetch_feed_with_cache(
    feed_url: str,
    timeout: float = 15,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FeedFetchResult:
    _validate_timeout(timeout)
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
            if content_length_exceeds_limit(content_length, MAX_FEED_BYTES):
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
    _validate_timeout(timeout)
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
        if content_length_exceeds_limit(content_length, MAX_FEED_BYTES):
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
        content, content_blocks, content_type = _entry_content_data(entry, source_url)
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
                image_url=_entry_image_url(entry, source_url),
                content_blocks=content_blocks,
            )
        )
    return items


def _entry_content(entry: dict[str, Any]) -> tuple[str, ContentType]:
    content, _, content_type = _entry_content_data(entry, None)
    return content, content_type


def _entry_content_data(
    entry: dict[str, Any],
    base_url: str | None,
) -> tuple[str, tuple[ContentBlock, ...], ContentType]:
    content_blocks = entry.get("content") or []
    for content_block in content_blocks:
        html = str(content_block.get("value", ""))
        blocks = parse_content_blocks(html, base_url)
        content = content_blocks_to_text(blocks)
        if content:
            return content, blocks, ContentType.RSS_CONTENT
    html = str(entry.get("summary") or entry.get("description") or "")
    blocks = parse_content_blocks(html, base_url)
    return content_blocks_to_text(blocks), blocks, ContentType.RSS_SUMMARY


def _entry_image_url(entry: Mapping[str, Any], base_url: str) -> str | None:
    for candidate in _structured_image_candidates(entry):
        image_url = resolve_image_url(candidate, base_url)
        if image_url is not None:
            return image_url

    for block in _entry_html_blocks(entry):
        image_url = extract_image_url_from_html(block, base_url)
        if image_url is not None:
            return image_url
    return None


def _structured_image_candidates(entry: Mapping[str, Any]):
    for field in ("media_content", "media_thumbnail"):
        values = entry.get(field) or []
        if isinstance(values, Mapping):
            values = [values]
        for value in values:
            if not isinstance(value, Mapping) or not _is_image_media(value):
                continue
            candidate = value.get("url") or value.get("href")
            if isinstance(candidate, str):
                yield candidate

    image = entry.get("image")
    if isinstance(image, Mapping):
        candidate = image.get("url") or image.get("href") or image.get("link")
        if isinstance(candidate, str):
            yield candidate

    for field in ("enclosures", "links"):
        values = entry.get(field) or []
        if isinstance(values, Mapping):
            values = [values]
        for value in values:
            if not isinstance(value, Mapping):
                continue
            if field == "links" and str(value.get("rel") or "").casefold() != "enclosure":
                continue
            if not _is_image_media(value):
                continue
            candidate = value.get("href") or value.get("url")
            if isinstance(candidate, str):
                yield candidate


def _is_image_media(value: Mapping[str, Any]) -> bool:
    medium = str(value.get("medium") or "").casefold()
    media_type = str(value.get("type") or "").casefold()
    if medium and medium != "image":
        return False
    if media_type and not media_type.startswith("image/"):
        return False
    return True


def _entry_html_blocks(entry: Mapping[str, Any]):
    for field in ("content", "summary", "description"):
        value = entry.get(field)
        values = value if field == "content" and isinstance(value, list) else [value]
        for block in values:
            if isinstance(block, Mapping):
                block = block.get("value")
            if isinstance(block, str) and block:
                yield block


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
