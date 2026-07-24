"""流水线各阶段共享的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .selection import SelectedEvidence

PROJECT_TIMEZONE = timezone(timedelta(hours=8), name="UTC+08:00")


def project_now() -> datetime:
    return datetime.now(PROJECT_TIMEZONE).replace(microsecond=0)


class RunStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ContentType(StrEnum):
    RSS_CONTENT = "rss_content"
    RSS_SUMMARY = "rss_summary"
    UNKNOWN = "unknown"


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
class CollectionReport:
    topic: str
    status: RunStatus
    articles: list[SelectedEvidence]
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Report:
    topic: str
    status: RunStatus
    analysis: Analysis
    evidence: list[SelectedEvidence]
    evaluation: Evaluation
    errors: list[str] = field(default_factory=list)
