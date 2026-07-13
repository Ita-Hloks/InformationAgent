from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import feedparser

from ..contracts import Evidence

MAX_FEED_BYTES = 5 * 1024 * 1024


def _plain_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(without_tags)).strip()


def fetch_feed(url: str, timeout: float = 15) -> list[Evidence]:
    if urlparse(url).scheme not in {"http", "https"}:
        raise ValueError("RSS 地址必须使用 http 或 https")

    request = Request(url, headers={"User-Agent": "InformationAgent/0.1 RSS-MVP"})
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
    for entry in feed.entries:
        source_url = str(entry.get("link") or entry.get("id") or "").strip()
        if not source_url:
            continue
        items.append(
            Evidence(
                source_url=source_url,
                title=_plain_text(str(entry.get("title") or "无标题")),
                content=_entry_content(entry),
                published_at=entry.get("published") or entry.get("updated"),
            )
        )
    return items


def _entry_content(entry: dict[str, Any]) -> str:
    content_blocks = entry.get("content") or []
    if content_blocks:
        return _plain_text(str(content_blocks[0].get("value", "")))
    return _plain_text(str(entry.get("summary") or entry.get("description") or ""))
