from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin

from .url import normalize_url

ContentBlockType = Literal["text", "image"]


@dataclass(frozen=True, slots=True)
class ContentBlock:
    type: ContentBlockType
    text: str | None = None
    url: str | None = None
    alt: str | None = None
    caption: str | None = None


class _ContentBlockParser(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
    _IGNORED_TAGS = {
        "code",
        "kbd",
        "noscript",
        "pre",
        "samp",
        "script",
        "style",
        "template",
    }

    def __init__(self, base_url: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.blocks: list[ContentBlock] = []
        self._text_parts: list[str] = []
        self._ignored_depth = 0
        self._figure_stack: list[_FigureContext] = []
        self._seen_image_urls: dict[str, int] = {}

    def handle_data(self, data: str) -> None:
        if self._ignored_depth or not data:
            return
        if self._figure_stack and self._figure_stack[-1].caption_depth:
            self._figure_stack[-1].caption_parts.append(data)
        else:
            self._text_parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in self._IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return

        attributes = {name.casefold(): value for name, value in attrs if value is not None}
        if normalized_tag == "figure":
            self._flush_text()
            self._figure_stack.append(_FigureContext())
            return
        if normalized_tag == "figcaption":
            self._flush_text()
            if self._figure_stack:
                self._figure_stack[-1].caption_depth += 1
            return
        if normalized_tag in {"img", "graphic"}:
            self._append_image(attributes)
            return
        if normalized_tag in self._BLOCK_TAGS:
            self._text_parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in self._IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if normalized_tag == "figcaption":
            self._finish_caption()
            return
        if normalized_tag == "figure":
            self._finish_figure()
            return
        if normalized_tag in self._BLOCK_TAGS:
            self._text_parts.append("\n")

    def finish(self) -> tuple[ContentBlock, ...]:
        while self._figure_stack:
            self._finish_figure()
        self._flush_text()
        return tuple(self.blocks)

    def _append_image(self, attributes: dict[str, str | None]) -> None:
        self._flush_text()
        candidate = first_image_candidate(attributes)
        image_url = _resolve_image_url(candidate, self.base_url)
        if image_url is None:
            return

        image_index = self._seen_image_urls.get(image_url)
        if image_index is None:
            image_index = len(self.blocks)
            self.blocks.append(
                ContentBlock(
                    type="image",
                    url=image_url,
                    alt=_normalize_inline_text(attributes.get("alt")),
                )
            )
            self._seen_image_urls[image_url] = image_index
        if self._figure_stack:
            self._figure_stack[-1].image_index = image_index

    def _finish_caption(self) -> None:
        if not self._figure_stack:
            return
        context = self._figure_stack[-1]
        context.caption_depth = max(0, context.caption_depth - 1)
        if context.caption_depth:
            return
        caption = _normalize_inline_text("".join(context.caption_parts))
        context.caption_parts.clear()
        if not caption:
            return
        if context.image_index is None:
            context.pending_caption = caption
            return
        self.blocks[context.image_index] = replace(
            self.blocks[context.image_index],
            caption=caption,
        )

    def _finish_figure(self) -> None:
        if not self._figure_stack:
            return
        context = self._figure_stack[-1]
        while context.caption_depth:
            self._finish_caption()
        context = self._figure_stack.pop()
        if context.pending_caption and context.image_index is None:
            self._text_parts.append(context.pending_caption)

    def _flush_text(self) -> None:
        if not self._text_parts:
            return
        text = "".join(self._text_parts)
        self._text_parts.clear()
        lines = [_normalize_inline_text(line) for line in text.splitlines()]
        self.blocks.extend(ContentBlock(type="text", text=line) for line in lines if line)


@dataclass(slots=True)
class _FigureContext:
    image_index: int | None = None
    caption_depth: int = 0
    caption_parts: list[str] | None = None
    pending_caption: str | None = None

    def __post_init__(self) -> None:
        if self.caption_parts is None:
            self.caption_parts = []


def parse_content_blocks(value: str, base_url: str | None = None) -> tuple[ContentBlock, ...]:
    parser = _ContentBlockParser(base_url)
    parser.feed(value)
    parser.close()
    return parser.finish()


def content_blocks_to_text(blocks: tuple[ContentBlock, ...] | list[ContentBlock]) -> str:
    parts = [
        block.text if block.type == "text" else block.caption
        for block in blocks
        if (block.type == "text" and block.text) or (block.type == "image" and block.caption)
    ]
    return "\n".join(part for part in parts if part)


def content_block_to_payload(block: ContentBlock) -> dict[str, str | None]:
    if block.type == "text":
        return {"type": "text", "text": block.text or ""}
    return {
        "type": "image",
        "url": block.url or "",
        "alt": block.alt,
        "caption": block.caption,
    }


def content_blocks_to_payload(
    blocks: tuple[ContentBlock, ...] | list[ContentBlock],
) -> list[dict[str, str | None]]:
    return [content_block_to_payload(block) for block in blocks]


def content_blocks_from_payload(value: object, content: str) -> tuple[ContentBlock, ...]:
    if not isinstance(value, list):
        return _fallback_content_blocks(content)

    blocks: list[ContentBlock] = []
    seen_image_urls: set[str] = set()
    for raw_block in value:
        if not isinstance(raw_block, dict):
            continue
        block_type = raw_block.get("type")
        if block_type == "text":
            text = _normalize_inline_text(raw_block.get("text"))
            if text:
                blocks.append(ContentBlock(type="text", text=text))
        elif block_type == "image":
            url = raw_block.get("url")
            normalized_url = normalize_url(str(url).strip()) if isinstance(url, str) else None
            if normalized_url is None or normalized_url in seen_image_urls:
                continue
            seen_image_urls.add(normalized_url)
            blocks.append(
                ContentBlock(
                    type="image",
                    url=normalized_url,
                    alt=_normalize_inline_text(raw_block.get("alt")),
                    caption=_normalize_inline_text(raw_block.get("caption")),
                )
            )
    return tuple(blocks) or _fallback_content_blocks(content)


def _fallback_content_blocks(content: str) -> tuple[ContentBlock, ...]:
    return (ContentBlock(type="text", text=content),) if content else ()


def first_image_candidate(attributes: Mapping[str, str | None]) -> str | None:
    for name in ("src", "data-src", "data-original", "data-lazy-src"):
        value = attributes.get(name)
        if value and value.strip():
            return value
    return _first_srcset_url(attributes.get("srcset") or attributes.get("data-srcset"))


def _first_srcset_url(value: str | None) -> str | None:
    if not value:
        return None
    for candidate in value.split(","):
        parts = candidate.strip().split(maxsplit=1)
        if parts and parts[0]:
            return parts[0]
    return None


def _resolve_image_url(value: str | None, base_url: str | None) -> str | None:
    if not value or not base_url:
        return None
    return normalize_url(urljoin(base_url, value.strip()))


def _normalize_inline_text(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()
