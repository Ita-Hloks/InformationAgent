from __future__ import annotations

import math

from ..common import DEFAULT_LLM_TIMEOUT_SECONDS
from ..normalization import NormalizedArticle
from .llm import LLMRelevanceSelector
from .models import RelevanceSelector, SelectedEvidence


def select_evidence(
    topic: str,
    items: list[NormalizedArticle],
    *,
    limit: int = 20,
    timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    selector: RelevanceSelector | None = None,
) -> list[SelectedEvidence]:
    _validate_input(topic, limit, timeout)
    unique_items = _deduplicate(items)
    if not unique_items:
        return []

    active_selector = selector or LLMRelevanceSelector()
    selected = active_selector.select(
        topic,
        unique_items,
        limit=limit,
        timeout=timeout,
    )
    return _validate_selection(selected, unique_items, limit)


def _deduplicate(items: list[NormalizedArticle]) -> list[NormalizedArticle]:
    unique: list[NormalizedArticle] = []
    seen_urls: set[str] = set()
    for item in items:
        if item.source_url in seen_urls:
            continue
        seen_urls.add(item.source_url)
        unique.append(item)
    return unique


def _validate_selection(
    selected: list[SelectedEvidence],
    items: list[NormalizedArticle],
    limit: int,
) -> list[SelectedEvidence]:
    if len(selected) > limit:
        raise ValueError("语义筛选器返回的文章数超过上限")

    items_by_url = {item.source_url: item for item in items}
    seen_urls: set[str] = set()
    validated: list[SelectedEvidence] = []
    for item in selected:
        source_url = item.source_url
        if source_url not in items_by_url:
            raise ValueError("语义筛选器返回了未知文章")
        base_article = items_by_url[source_url]
        if item.article != base_article:
            raise ValueError("语义筛选器返回了输入文章之外的内容")
        if source_url in seen_urls:
            raise ValueError("语义筛选器重复返回文章")
        seen_urls.add(source_url)
        validated.append(
            SelectedEvidence(
                article=base_article,
                evidence_id=len(validated) + 1,
            )
        )
    return validated


def _validate_input(topic: str, limit: int, timeout: float) -> None:
    if not topic.strip():
        raise ValueError("研究主题不能为空")
    if limit <= 0:
        raise ValueError("证据数量上限必须大于 0")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("语义筛选时限必须大于 0 秒")
