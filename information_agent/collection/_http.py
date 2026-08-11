"""Shared HTTP response validation helpers."""

from __future__ import annotations


def content_length_exceeds_limit(value: object, limit: int) -> bool:
    """Return whether an ASCII-decimal Content-Length is above ``limit``.

    Content-Length is an advisory header, so malformed values remain unknown.
    Comparing normalized decimal strings avoids converting an untrusted,
    arbitrarily long header value to an integer.
    """
    if not isinstance(value, str) or not value or any(char < "0" or char > "9" for char in value):
        return False

    normalized = value.lstrip("0") or "0"
    limit_text = str(limit)
    return len(normalized) > len(limit_text) or (
        len(normalized) == len(limit_text) and normalized > limit_text
    )
