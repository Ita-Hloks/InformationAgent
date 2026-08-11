from __future__ import annotations

import re

_CODE_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"`+[^`\n]+`+")
CONTENT_BATCH_CHARS = 2_000


def llm_safe_text(value: str) -> str:
    """Remove explicit code notation before text is sent to an LLM."""

    lines: list[str] = []
    fence_character: str | None = None
    for line in value.splitlines():
        fence = _CODE_FENCE.match(line)
        if fence is not None:
            marker = fence.group(1)
            if fence_character is None:
                fence_character = marker[0]
            elif marker[0] == fence_character:
                fence_character = None
            continue
        if fence_character is not None:
            continue
        lines.append(_INLINE_CODE.sub("", line))

    return "\n".join(line for line in lines if line.strip()).strip()


def split_content(content: str, batch_chars: int) -> list[str]:
    if batch_chars <= 0:
        raise ValueError("batch_chars must be positive")

    chunks: list[str] = []
    start = 0
    while start < len(content):
        end = min(start + batch_chars, len(content))
        if end < len(content):
            boundary = _last_natural_boundary(content, start, end)
            if boundary > start + batch_chars // 2:
                end = boundary
        chunks.append(content[start:end])
        start = end
    return chunks


def _last_natural_boundary(content: str, start: int, end: int) -> int:
    positions = [
        content.rfind(marker, start + 1, end)
        for marker in ("\n", "。", "！", "？", ".", "!", "?", ";", "；")
    ]
    position = max(positions)
    return position + 1 if position >= 0 else end
