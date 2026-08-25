"""Web article fetching. Fetches full text for RSS summary items."""

from __future__ import annotations

import math
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import trafilatura

from ..common import normalize_url
from ..contracts import ContentType
from ._http import content_length_exceeds_limit
from .images import extract_image_url_from_html
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


def _extract_text(html: str, **kwargs) -> str | None:
    try:
        return trafilatura.extract(html, **kwargs)
    except Exception:
        return None


def fetch_article(
    article_url: str,
    *,
    timeout: float = 15,
) -> str | None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")

    page = _fetch_page_html(article_url, timeout)
    if page is None:
        return None

    _, html = page
    text = _extract_text(html)
    if text is None or len(text) < MIN_CONTENT_CHARS:
        return None
    return text


def fetch_article_image(
    article_url: str,
    *,
    timeout: float = 15,
) -> str | None:
    page = _fetch_page_html(article_url, timeout)
    if page is None:
        return None
    normalized_url, html = page
    return extract_image_url_from_html(html, normalized_url)


def augment_images(
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
        if item.image_url:
            results[i] = item
        else:
            to_fetch.append((i, item))

    if not to_fetch:
        return items

    worker_count = min(max_workers, len(to_fetch))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(fetch_article_image, item.source_url, timeout=timeout): i
            for i, item in to_fetch
        }
        for future in as_completed(future_map):
            i = future_map[future]
            image_url = future.result()
            results[i] = replace(items[i], image_url=image_url) if image_url else items[i]

    return [results[i] for i in range(len(items))]


def _augment_item(item: RawFeedEntry, timeout: float) -> RawFeedEntry:
    content = fetch_article(item.source_url, timeout=timeout)
    if content is None:
        return item
    return replace(item, content=content, content_type=ContentType.RSS_CONTENT)


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
            results[i] = item
        else:
            to_fetch.append((i, item))

    if not to_fetch:
        return items

    worker_count = min(max_workers, len(to_fetch))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {executor.submit(_augment_item, item, timeout): i for i, item in to_fetch}
        for future in as_completed(future_map):
            i = future_map[future]
            results[i] = future.result()

    return [results[i] for i in range(len(items))]
