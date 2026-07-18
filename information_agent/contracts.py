"""流水线各阶段共享的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum

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
class Evidence:
    source_url: str
    title: str
    content: str
    article_id: str = ""
    feed_url: str | None = None
    site_url: str | None = None
    source_type: str = "rss"
    author: str | None = None
    categories: list[str] = field(default_factory=list)
    language: str | None = None
    content_type: ContentType = ContentType.UNKNOWN
    relevance_score: float = 0.0
    processing_warnings: list[str] = field(default_factory=list)
    content_chunks: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    collected_at: datetime = field(default_factory=project_now)
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
