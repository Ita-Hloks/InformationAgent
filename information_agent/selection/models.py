from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..normalization import NormalizedArticle


class RelevanceSelector(Protocol):
    """为规范化文章提供语义相关性判断。"""

    def select(
        self,
        topic: str,
        items: list[NormalizedArticle],
        *,
        limit: int,
        timeout: float,
    ) -> list[SelectedEvidence]: ...


@dataclass(frozen=True, slots=True)
class SelectedEvidence:
    """A normalized article selected and numbered for analysis."""

    article: NormalizedArticle
    evidence_id: int

    @property
    def id(self) -> int:
        return self.evidence_id

    @property
    def source_url(self) -> str:
        return self.article.source_url

    @property
    def title(self) -> str:
        return self.article.title

    @property
    def content(self) -> str:
        return self.article.content

    @property
    def content_chunks(self) -> tuple[str, ...]:
        return self.article.content_chunks
