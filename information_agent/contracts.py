"""流水线各阶段共享的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(slots=True)
class Evidence:
    source_url: str
    title: str
    content: str
    published_at: str | None = None
    collected_at: datetime = field(default_factory=utc_now)
    id: int | None = None


@dataclass(slots=True)
class Claim:
    text: str
    evidence_ids: list[int]


@dataclass(slots=True)
class Analysis:
    summary: str
    claims: list[Claim]
    uncertainties: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Evaluation:
    citation_coverage: float
    citation_validity: float
    lexical_support: float
    issues: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Report:
    topic: str
    status: RunStatus
    analysis: Analysis
    evidence: list[Evidence]
    evaluation: Evaluation
    errors: list[str] = field(default_factory=list)
