from __future__ import annotations

import re

from ..contracts import Evidence


def _terms(text: str) -> set[str]:
    terms = set(re.findall(r"[A-Za-z0-9_-]{2,}", text.casefold()))
    for value in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        terms.add(value)
        terms.update(value[index : index + 2] for index in range(len(value) - 1))
    return terms


def filter_evidence(topic: str, items: list[Evidence], limit: int = 20) -> list[Evidence]:
    topic_terms = _terms(topic)
    ranked: list[tuple[int, int, Evidence]] = []
    seen_urls: set[str] = set()

    for position, item in enumerate(items):
        if item.source_url in seen_urls:
            continue
        seen_urls.add(item.source_url)
        score = len(topic_terms & _terms(f"{item.title} {item.content}"))
        if score:
            ranked.append((score, -position, item))

    ranked.sort(key=lambda value: (value[0], value[1]), reverse=True)
    selected = [item for _, _, item in ranked[:limit]]
    for evidence_id, item in enumerate(selected, start=1):
        item.id = evidence_id
    return selected
