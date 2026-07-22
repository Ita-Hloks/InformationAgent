"""网页正文抓取。与 rss.py 配合：RSS 只给摘要时，用此模块补全文。"""

from __future__ import annotations

import re
from dataclasses import replace
from html import unescape
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..contracts import ContentType, Evidence
from .normalization import normalize_url

MAX_PAGE_BYTES = 2 * 1024 * 1024
MIN_CONTENT_CHARS = 20


def _extract_text(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_article(
    article_url: str,
    *,
    timeout: float = 15,
) -> str | None:
    normalized_url = normalize_url(article_url)
    if normalized_url is None:
        return None

    request = Request(
        normalized_url,
        headers={"User-Agent": "InformationAgent/0.1 Web-Extractor"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_PAGE_BYTES:
                return None
            payload = response.read(MAX_PAGE_BYTES + 1)
    except (URLError, OSError, ValueError):
        return None

    if len(payload) > MAX_PAGE_BYTES:
        return None

    for encoding in ("utf-8", "gbk", "gb2312", "latin-1"):
        try:
            html = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None

    text = _extract_text(html)
    if len(text) < MIN_CONTENT_CHARS:
        return None
    return text


def augment_evidence(
    items: list[Evidence],
    *,
    timeout: float = 15,
) -> list[Evidence]:
    augmented: list[Evidence] = []
    for item in items:
        if item.content_type != ContentType.RSS_SUMMARY:
            augmented.append(item)
            continue

        content = fetch_article(item.source_url, timeout=timeout)
        if content is None:
            augmented.append(item)
            continue

        augmented.append(
            replace(item, content=content, content_type=ContentType.RSS_CONTENT)
        )
    return augmented
