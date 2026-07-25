from __future__ import annotations

import re

from ..normalization import NormalizedArticle
from .models import SelectedEvidence

TITLE_TERM_WEIGHT = 2


def _terms(text: str) -> set[str]:
    terms = set(re.findall(r"[A-Za-z0-9_-]{2,}", text.casefold()))
    for value in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        terms.add(value)
        terms.update(value[index : index + 2] for index in range(len(value) - 1))
    return terms


def filter_evidence(
    topic: str, items: list[NormalizedArticle], limit: int = 20
) -> list[SelectedEvidence]:
    topic_terms = _terms(topic)
    ranked: list[tuple[int, int, float, NormalizedArticle]] = []
    seen_urls: set[str] = set()

    for position, item in enumerate(items):
        if item.source_url in seen_urls:
            continue
        seen_urls.add(item.source_url)
        title_score = len(topic_terms & _terms(item.title))
        content_score = len(topic_terms & _terms(item.content))
        score = TITLE_TERM_WEIGHT * title_score + content_score
        if score:
            maximum_score = (TITLE_TERM_WEIGHT + 1) * len(topic_terms)
            relevance_score = round(score / maximum_score, 4)
            ranked.append((score, -position, relevance_score, item))

    ranked.sort(key=lambda value: (value[0], value[1]), reverse=True)
    return [
        SelectedEvidence(article=item, evidence_id=evidence_id, relevance_score=relevance_score)
        for evidence_id, (_, _, relevance_score, item) in enumerate(ranked[:limit], start=1)
    ]
