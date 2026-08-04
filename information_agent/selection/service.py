from __future__ import annotations

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
    seen_segments: set[tuple[str, str]] = set()
    validated: list[SelectedEvidence] = []
    for item in selected:
        source_url = item.source_url
        if source_url not in items_by_url:
            raise ValueError("语义筛选器返回了未知文章")
        base_article = items_by_url[source_url]
        if item.article.article_id != base_article.article_id:
            raise ValueError("语义筛选器返回了未知文章身份")
        if item.article.content not in base_article.content:
            raise ValueError("语义筛选器返回了输入文章之外的正文")
        segment_key = (source_url, item.article.content)
        if segment_key in seen_segments:
            raise ValueError("语义筛选器重复返回文章片段")
        seen_segments.add(segment_key)
        validated.append(
            SelectedEvidence(
                article=item.article,
                evidence_id=len(validated) + 1,
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
