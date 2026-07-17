from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..contracts import Evidence

MIN_CONTENT_CHARS = 20
MAX_CONTENT_CHARS = 500
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
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0)


def normalize_evidence(
    items: list[Evidence],
    *,
    min_content_chars: int = MIN_CONTENT_CHARS,
    max_content_chars: int = MAX_CONTENT_CHARS,
) -> list[Evidence]:
    if min_content_chars <= 0 or max_content_chars < min_content_chars:
        raise ValueError("正文长度限制无效")

    normalized: list[Evidence] = []
    for item in items:
        source_url = normalize_url(item.source_url)
        if source_url is None:
            continue

        title = _normalize_text(item.title)
        content = _normalize_text(item.content)
        if len(content) < min_content_chars:
            continue

        content_truncated = item.content_truncated or len(content) > max_content_chars
        if len(content) > max_content_chars:
            content = content[:max_content_chars].rstrip()

        normalized.append(
            replace(
                item,
                source_url=source_url,
                title=title,
                content=content,
                published_at=parse_published_at(item.published_at),
                content_truncated=content_truncated,
            )
        )
    return normalized


def _is_tracking_key(key: str) -> bool:
    normalized = key.casefold()
    return normalized.startswith("utm_") or normalized in TRACKING_QUERY_KEYS


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
