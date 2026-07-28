from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SearchAnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class SearchSource:
    title: str
    url: str
    site_name: str | None = None
    published_at: str | None = None
    snippet: str | None = None
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class SearchAnswer:
    evidence_id: int
    question: str
    answer: str
    status: SearchAnswerStatus
    sources: tuple[SearchSource, ...] = field(default_factory=tuple)
