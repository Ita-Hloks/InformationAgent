from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..collection import fetch_feed
from ..common import DEFAULT_LLM_TIMEOUT_SECONDS
from ..contracts import RunStatus
from ..investigation import QuestionPlanner, SearchPlan
from ..search import HostedSearchAnswerer, SearchAnswer, SearchAnswerer
from ..selection import SelectedEvidence
from .collection import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_WORKERS,
    DEFAULT_SOURCE_TIMEOUT_SECONDS,
    Collector,
)
from .planning import MAX_PLANNING_ARTICLES, plan


@dataclass(slots=True)
class SearchReport:
    topic: str
    status: RunStatus
    articles: list[SelectedEvidence]
    plans: list[SearchPlan]
    answers: list[SearchAnswer]
    errors: list[str] = field(default_factory=list)


def search(
    topic: str,
    feeds: list[str],
    *,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    limit: int = MAX_PLANNING_ARTICLES,
    collector: Collector = fetch_feed,
    planner: QuestionPlanner | None = None,
    answerer: SearchAnswerer | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    source_timeout_seconds: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
) -> SearchReport:
    deadline = time.monotonic() + timeout_seconds
    planning_report = plan(
        topic,
        feeds,
        timeout_seconds=timeout_seconds,
        limit=limit,
        collector=collector,
        planner=planner,
        max_workers=max_workers,
        max_attempts=max_attempts,
        source_timeout_seconds=source_timeout_seconds,
    )
    errors = list(planning_report.errors)
    answers: list[SearchAnswer] = []
    if not planning_report.plans:
        return SearchReport(
            topic,
            planning_report.status,
            planning_report.articles,
            planning_report.plans,
            answers,
            errors,
        )

    for item in planning_report.plans:
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            errors.append("任务在联网搜索前超时")
            break
        try:
            active_answerer = answerer or HostedSearchAnswerer()
            answers.append(active_answerer.answer(item, remaining))
        except Exception as exc:
            errors.append(f"搜索回答失败：{exc}")

    status = RunStatus.PARTIAL if errors else planning_report.status
    return SearchReport(
        topic,
        status,
        planning_report.articles,
        planning_report.plans,
        answers,
        errors,
    )
