from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..contracts import RunStatus
from ..selection import SelectedEvidence


class QuestionKind(StrEnum):
    QUANTITATIVE_CLAIM = "quantitative_claim"
    CAUSAL_CLAIM = "causal_claim"
    ATTRIBUTION_CLAIM = "attribution_claim"
    TIME_SENSITIVE_CLAIM = "time_sensitive_claim"


@dataclass(frozen=True, slots=True)
class SearchQuery:
    query: str
    purpose: str


@dataclass(frozen=True, slots=True)
class SearchPlan:
    evidence_id: int
    trigger_quote: str
    question: str
    kind: QuestionKind
    priority: int
    queries: tuple[SearchQuery, ...]


@dataclass(slots=True)
class PlanningReport:
    topic: str
    status: RunStatus
    articles: list[SelectedEvidence]
    plans: list[SearchPlan]
    errors: list[str] = field(default_factory=list)
