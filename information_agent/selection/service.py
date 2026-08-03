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


def filter_evidence(
    topic: str,
    items: list[NormalizedArticle],
    limit: int = 20,
    *,
    timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    selector: RelevanceSelector | None = None,
) -> list[SelectedEvidence]:
    """兼容旧名称；实际筛选始终经过语义选择器。"""

    return select_evidence(
        topic,
        items,
        limit=limit,
        timeout=timeout,
        selector=selector,
    )


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
        if source_url in seen_urls:
            raise ValueError("语义筛选器重复返回文章")
        if not math.isfinite(item.relevance_score) or not 0 <= item.relevance_score <= 1:
            raise ValueError("语义筛选器返回了无效相关性分数")
        seen_urls.add(source_url)
        validated.append(
            SelectedEvidence(
                article=items_by_url[source_url],
                evidence_id=len(validated) + 1,
                relevance_score=round(item.relevance_score, 4),
            )
        )
    return validated


def _validate_input(topic: str, limit: int, timeout: float) -> None:
    if not topic.strip():
        raise ValueError("研究主题不能为空")
    if limit <= 0:
        raise ValueError("证据数量上限必须大于 0")
    if timeout <= 0:
        raise ValueError("语义筛选时限必须大于 0 秒")
