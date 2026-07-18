from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..contracts import PROJECT_TIMEZONE, Evidence

MIN_CONTENT_CHARS = 20
CONTENT_BATCH_CHARS = 500
TRACKING_QUERY_KEYS = {
    "dclid",
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "msclkid",
}


def normalize_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return None

    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None

    hostname = parsed.hostname.casefold()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        hostname = f"{hostname}:{port}"

    query = urlencode(
        [
            (key, query_value)
            for key, query_value in parse_qsl(parsed.query, keep_blank_values=True)
            if not _is_tracking_key(key)
        ],
        doseq=True,
    )
    return urlunsplit((scheme, hostname, parsed.path or "/", query, ""))


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
    items: list[Evidence],
    *,
    min_content_chars: int = MIN_CONTENT_CHARS,
    content_batch_chars: int = CONTENT_BATCH_CHARS,
) -> list[Evidence]:
    if min_content_chars <= 0 or content_batch_chars <= 0:
        raise ValueError("正文长度限制无效")

    normalized: list[Evidence] = []
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

        content_chunks = _split_content(content, content_batch_chars)
        processing_warnings = list(item.processing_warnings)
        if len(content_chunks) > 1:
            batch_warning = (
                f"正文已拆分为 {len(content_chunks)} 个批次，每批最多 {content_batch_chars} 字"
            )
            if batch_warning not in processing_warnings:
                processing_warnings.append(batch_warning)

        normalized.append(
            replace(
                item,
                article_id=item.article_id or _article_id(source_url),
                source_url=source_url,
                feed_url=feed_url,
                site_url=site_url,
                source_type=item.source_type.strip().casefold() or "rss",
                title=title,
                content=content,
                content_chunks=content_chunks,
                published_at=parse_published_at(item.published_at),
                collected_at=parse_published_at(item.collected_at) or item.collected_at,
                processing_warnings=processing_warnings,
            )
        )
    return normalized


def _is_tracking_key(key: str) -> bool:
    normalized = key.casefold()
    return normalized.startswith("utm_") or normalized in TRACKING_QUERY_KEYS


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _split_content(content: str, batch_chars: int) -> list[str]:
    return [content[index : index + batch_chars] for index in range(0, len(content), batch_chars)]


def _article_id(source_url: str) -> str:
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    return f"article-{digest}"
