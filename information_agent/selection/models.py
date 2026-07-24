from __future__ import annotations

from dataclasses import dataclass

from ..normalization import NormalizedArticle


@dataclass(frozen=True, slots=True)
class SelectedEvidence:
    """A normalized article selected and numbered for analysis."""

    article: NormalizedArticle
    evidence_id: int
    relevance_score: float

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
