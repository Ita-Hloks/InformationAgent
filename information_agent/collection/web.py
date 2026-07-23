"""Web article fetching. Fetches full text for RSS summary items."""

from __future__ import annotations

from dataclasses import replace
from urllib.error import URLError
from urllib.request import Request, urlopen

import trafilatura

from ..contracts import ContentType, Evidence
from .normalization import normalize_url

MAX_PAGE_BYTES = 2 * 1024 * 1024
MIN_CONTENT_CHARS = 20


def _guess_encoding(response) -> str | None:
    content_type = response.headers.get("Content-Type", "")
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("charset="):
            return part.removeprefix("charset=").strip()
    return None


def _extract_text(html: str) -> str | None:
    return trafilatura.extract(html)


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

    guessed = _guess_encoding(response)
    for encoding in (guessed, "utf-8", "gbk", "gb2312", "latin-1"):
        if encoding is None:
            continue
        try:
            html = payload.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        return None

    text = _extract_text(html)
    if text is None or len(text) < MIN_CONTENT_CHARS:
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
