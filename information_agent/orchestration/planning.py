from __future__ import annotations

from ..collection import fetch_feed
from ..common import DEFAULT_LLM_TIMEOUT_SECONDS
from ..investigation import PlanningReport, QuestionPlanner
from .collection import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_WORKERS,
    DEFAULT_SOURCE_TIMEOUT_SECONDS,
    Collector,
)
from .runner import WorkflowRunner

MAX_PLANNING_ARTICLES = 5


def plan(
    topic: str,
    feeds: list[str],
    *,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    limit: int = MAX_PLANNING_ARTICLES,
    collector: Collector = fetch_feed,
    planner: QuestionPlanner | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    source_timeout_seconds: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
) -> PlanningReport:
    if not 1 <= limit <= MAX_PLANNING_ARTICLES:
        raise ValueError(f"搜索计划最多检查 {MAX_PLANNING_ARTICLES} 篇文章")
    return WorkflowRunner(
        topic=topic,
        feeds=feeds,
        timeout_seconds=timeout_seconds,
        limit=limit,
        collector=collector,
        planner=planner,
        max_workers=max_workers,
        max_attempts=max_attempts,
        source_timeout_seconds=source_timeout_seconds,
    ).plan()
