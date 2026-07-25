from __future__ import annotations

from ..collection import fetch_feed
from ..contracts import RunStatus
from ..investigation import LLMQuestionPlanner, PlanningReport, QuestionPlanner
from .collection import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_WORKERS,
    DEFAULT_SOURCE_TIMEOUT_SECONDS,
    Collector,
    _execute_collection,
)

MAX_PLANNING_ARTICLES = 5


def plan(
    topic: str,
    feeds: list[str],
    *,
    timeout_seconds: float = 60,
    limit: int = MAX_PLANNING_ARTICLES,
    collector: Collector = fetch_feed,
    planner: QuestionPlanner | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    source_timeout_seconds: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
) -> PlanningReport:
    if not 1 <= limit <= MAX_PLANNING_ARTICLES:
        raise ValueError(f"搜索计划最多检查 {MAX_PLANNING_ARTICLES} 篇文章")
    execution = _execute_collection(
        topic,
        feeds,
        timeout_seconds=timeout_seconds,
        limit=limit,
        collector=collector,
        max_workers=max_workers,
        max_attempts=max_attempts,
        source_timeout_seconds=source_timeout_seconds,
    )
    articles = execution.report.articles
    errors = list(execution.report.errors)
    if not articles:
        return PlanningReport(topic, RunStatus.PARTIAL, articles, [], errors)

    remaining = execution.remaining_seconds
    if remaining <= 0:
        errors.append("任务在生成搜索计划前超时")
        return PlanningReport(topic, RunStatus.PARTIAL, articles, [], errors)

    try:
        active_planner = planner or LLMQuestionPlanner()
        plans = active_planner.plan(topic, articles, remaining)
    except Exception as exc:
        errors.append(f"搜索计划生成失败：{exc}")
        return PlanningReport(topic, RunStatus.PARTIAL, articles, [], errors)

    status = RunStatus.COMPLETED if not errors else RunStatus.PARTIAL
    return PlanningReport(topic, status, articles, plans, errors)
