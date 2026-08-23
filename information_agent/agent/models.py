from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

from ..contracts import RunStatus
from ..investigation import SearchPlan
from ..search import SearchAnswer
from ..selection import SelectedEvidence


class FinishReason(StrEnum):
    EVIDENCE_SUFFICIENT = "evidence_sufficient"
    NO_MATERIAL_GAP = "no_material_gap"
    INSUFFICIENT_AFTER_SEARCH = "insufficient_after_search"


@dataclass(frozen=True, slots=True)
class ConclusionCitation:
    claim: str
    evidence_ids: tuple[int, ...]
    source_urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FinishDecision:
    reason: FinishReason
    citations: tuple[ConclusionCitation, ...]
    uncertainties: tuple[str, ...] = field(default_factory=tuple)

    @property
    def answer(self) -> str:
        lines = []
        for citation in self.citations:
            references = [f"原始文章[{value}]" for value in citation.evidence_ids]
            references.extend(citation.source_urls)
            lines.append(f"{citation.claim}（来源：{'；'.join(references)}）")
        return "\n".join(lines)

    @property
    def evidence_ids(self) -> tuple[int, ...]:
        return tuple(
            dict.fromkeys(
                evidence_id for citation in self.citations for evidence_id in citation.evidence_ids
            )
        )


@dataclass(frozen=True, slots=True)
class SearchDecision:
    plan: SearchPlan


AgentDecision: TypeAlias = FinishDecision | SearchDecision


@dataclass(frozen=True, slots=True)
class AgentObservation:
    plan: SearchPlan
    answer: SearchAnswer


class AgentStopReason(StrEnum):
    FINISHED = "finished"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_EVIDENCE = "no_evidence"
    MAX_STEPS = "max_steps"
    TIMEOUT = "timeout"
    ERROR = "error"
    REPEATED_QUERY = "repeated_query"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class AgentReport:
    run_id: str
    topic: str
    status: RunStatus
    articles: list[SelectedEvidence]
    plans: list[SearchPlan]
    answers: list[SearchAnswer]
    final_answer: str | None
    evidence_ids: tuple[int, ...]
    uncertainties: tuple[str, ...]
    steps: int
    stop_reason: AgentStopReason
    errors: list[str] = field(default_factory=list)
    citations: tuple[ConclusionCitation, ...] = field(default_factory=tuple)
    analysis_run_id: str | None = None
