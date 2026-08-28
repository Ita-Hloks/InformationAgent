"""Web article fetching. Fetches full text for RSS summary items."""

from __future__ import annotations

import math
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import trafilatura
from lxml import etree
from lxml import html as lxml_html

from ..common import (
    ContentBlock,
    content_blocks_to_text,
    first_image_candidate,
    normalize_url,
    parse_content_blocks,
)
from ..contracts import ContentType
from ._http import content_length_exceeds_limit
from .images import extract_image_url_from_html, image_url_is_accessible
from .models import RawFeedEntry

MAX_PAGE_BYTES = 2 * 1024 * 1024
MIN_CONTENT_CHARS = 20
DEFAULT_MAX_WORKERS = 6
DEFAULT_REQUESTS_PER_SECOND = 3
WINDOW_SECONDS = 1.0


class DomainRateLimiter:
    def __init__(self, requests_per_second: int = DEFAULT_REQUESTS_PER_SECOND):
        self._requests_per_second = requests_per_second
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def wait_if_needed(self, domain: str) -> None:
        now = time.monotonic()
        with self._lock:
            timestamps = self._windows[domain]
            cutoff = now - WINDOW_SECONDS
            timestamps[:] = [t for t in timestamps if t > cutoff]
            if len(timestamps) >= self._requests_per_second:
                sleep_for = timestamps[0] + WINDOW_SECONDS - now
                if sleep_for > 0:
                    time.sleep(sleep_for)
            self._windows[domain].append(time.monotonic())


_rate_limiter = DomainRateLimiter()


def _guess_encoding(response) -> str | None:
    content_type = response.headers.get("Content-Type", "")
    for part in content_type.split(";"):
        name, separator, value = part.partition("=")
        if separator and name.strip().casefold() == "charset":
            encoding = value.strip()
            if len(encoding) >= 2 and encoding[0] == encoding[-1] and encoding[0] in "\"'":
                encoding = encoding[1:-1].strip()
            return encoding
    return None


@dataclass(frozen=True, slots=True)
class ArticleFetchResult:
    content: str
    content_blocks: tuple[ContentBlock, ...]
    image_url: str | None


def fetch_article(
    article_url: str,
    *,
    timeout: float = 15,
    _return_details: bool = False,
) -> str | ArticleFetchResult | None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")

    page = _fetch_page_html(article_url, timeout)
    if page is None:
        return None

    normalized_url, html = page
    result = _extract_article_result(html, normalized_url)
    if result is None:
        return None
    return result if _return_details else result.content


def _augment_item(item: RawFeedEntry, timeout: float) -> RawFeedEntry:
    fetched = fetch_article(item.source_url, timeout=timeout, _return_details=True)
    if fetched is None:
        return replace(
            item,
            image_url=_first_accessible_image_url((item.image_url,), timeout=timeout),
        )
    if isinstance(fetched, ArticleFetchResult):
        return replace(
            item,
            content=fetched.content,
            content_type=ContentType.RSS_CONTENT,
            content_blocks=fetched.content_blocks,
            image_url=_first_accessible_image_url(
                (fetched.image_url, item.image_url),
                timeout=timeout,
            ),
        )
    return replace(
        item,
        content=fetched,
        content_type=ContentType.RSS_CONTENT,
        image_url=_first_accessible_image_url((item.image_url,), timeout=timeout),
    )


def _first_accessible_image_url(
    candidates: tuple[str | None, ...],
    *,
    timeout: float,
) -> str | None:
    seen: set[str] = set()
    for candidate in candidates:
        normalized_url = normalize_url(candidate or "")
        if normalized_url is None or normalized_url in seen:
            continue
        seen.add(normalized_url)
        if image_url_is_accessible(normalized_url, timeout=timeout):
            return normalized_url
    return None


def _extract_article_result(html: str, normalized_url: str) -> ArticleFetchResult | None:
    try:
        document = trafilatura.bare_extraction(
            _prepare_html_for_extraction(html),
            url=normalized_url,
            include_images=True,
        )
    except Exception:
        return None
    if document is None or document.body is None:
        return None

    extracted_html = etree.tostring(document.body, encoding="unicode")
    blocks = parse_content_blocks(extracted_html, normalized_url)
    blocks = _restore_image_captions(blocks, parse_content_blocks(html, normalized_url))
    text = content_blocks_to_text(blocks)
    if len(text) < MIN_CONTENT_CHARS:
        return None
    return ArticleFetchResult(
        content=text,
        content_blocks=blocks,
        image_url=extract_image_url_from_html(html, normalized_url)
        or next((block.url for block in blocks if block.type == "image"), None),
    )


def _prepare_html_for_extraction(html: str) -> str:
    try:
        tree = lxml_html.fromstring(html)
    except (etree.ParserError, ValueError):
        return html
    for image in tree.xpath(".//img"):
        source = image.get("src")
        if source and source.strip():
            continue
        attributes = {name.casefold(): value for name, value in image.attrib.items()}
        candidate = first_image_candidate(attributes)
        if candidate:
            image.set("src", candidate)
    return etree.tostring(tree, encoding="unicode")


def _restore_image_captions(
    extracted_blocks: tuple[ContentBlock, ...],
    source_blocks: tuple[ContentBlock, ...],
) -> tuple[ContentBlock, ...]:
    captions = {
        block.url: block.caption
        for block in source_blocks
        if block.type == "image" and block.url and block.caption
    }
    if not captions:
        return extracted_blocks
    return tuple(
        replace(block, caption=captions.get(block.url))
        if block.type == "image" and not block.caption and block.url in captions
        else block
        for block in extracted_blocks
    )


def _fetch_page_html(article_url: str, timeout: float) -> tuple[str, str] | None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")

    normalized_url = normalize_url(article_url)
    if normalized_url is None:
        return None

    try:
        domain = normalized_url.split("/")[2]
    except IndexError:
        domain = "unknown"
    _rate_limiter.wait_if_needed(domain)

    request = Request(
        normalized_url,
        headers={"User-Agent": "InformationAgent/0.1 Web-Extractor"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length_exceeds_limit(content_length, MAX_PAGE_BYTES):
                return None
            payload = response.read(MAX_PAGE_BYTES + 1)
            guessed = _guess_encoding(response)
    except HTTPError:
        return None
    except (URLError, OSError, ValueError):
        return None

    if len(payload) > MAX_PAGE_BYTES:
        return None

    for encoding in (guessed, "utf-8", "gbk", "gb2312", "latin-1"):
        if encoding is None:
            continue
        try:
            html = payload.decode(encoding)
            return normalized_url, html
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def augment_evidence(
    items: list[RawFeedEntry],
    *,
    timeout: float = 15,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[RawFeedEntry]:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")

    to_fetch: list[tuple[int, RawFeedEntry]] = []
    results: dict[int, RawFeedEntry] = {}

    for i, item in enumerate(items):
        if item.content_type != ContentType.RSS_SUMMARY:
            results[i] = replace(
                item,
                image_url=_first_accessible_image_url((item.image_url,), timeout=timeout),
            )
        else:
            to_fetch.append((i, item))

    if not to_fetch:
        return [results[i] for i in range(len(items))]

    worker_count = min(max_workers, len(to_fetch))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {executor.submit(_augment_item, item, timeout): i for i, item in to_fetch}
        for future in as_completed(future_map):
            i = future_map[future]
            results[i] = future.result()

    return [results[i] for i in range(len(items))]
