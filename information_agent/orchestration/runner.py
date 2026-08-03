from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from ..analysis import LLMAnalyst, evaluate_analysis
from ..collection import fetch_feed
from ..contracts import Analysis, CollectionReport, Report, RunStatus
from ..investigation import LLMQuestionPlanner, PlanningReport, QuestionPlanner
from ..selection import RelevanceSelector, SelectedEvidence
from .collection import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_WORKERS,
    DEFAULT_SOURCE_TIMEOUT_SECONDS,
    Collector,
    _execute_collection,
)
from .execution import Clock, ExecutionBudget


class Analyst(Protocol):
    def analyze(self, topic: str, evidence: list[SelectedEvidence], timeout: float) -> Analysis: ...


@dataclass(slots=True)
class WorkflowRunner:
    """Internal runner for one invocation of the fixed MVP workflow."""

    topic: str
    feeds: list[str]
    timeout_seconds: float = 300
    limit: int = 20
    collector: Collector = fetch_feed
    relevance_selector: RelevanceSelector | None = None
    analyst: Analyst | None = None
    planner: QuestionPlanner | None = None
    max_workers: int = DEFAULT_MAX_WORKERS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    source_timeout_seconds: float = DEFAULT_SOURCE_TIMEOUT_SECONDS
    clock: Clock = field(default=time.monotonic, repr=False)
    _budget: ExecutionBudget = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._budget = ExecutionBudget.start(self.timeout_seconds, clock=self.clock)

    def collect(self) -> CollectionReport:
        return self._collect()

    def run(self) -> Report:
        collection_report = self._collect()
        evidence = collection_report.articles
        errors = list(collection_report.errors)
        if not evidence:
            analysis = Analysis(
                summary="没有找到与主题匹配的 RSS 内容。",
                claims=[],
                uncertainties=["没有证据，因此未调用模型。"],
            )
            evaluation = evaluate_analysis(analysis, [])
            return Report(self.topic, RunStatus.PARTIAL, analysis, [], evaluation, errors)

        remaining = self._budget.remaining()
        if remaining <= 0:
            errors.append("任务在模型分析前超时")
            analysis = _failed_analysis("已获得证据，但没有剩余时间调用模型。")
            evaluation = evaluate_analysis(analysis, evidence)
            return Report(
                self.topic,
                RunStatus.PARTIAL,
                analysis,
                evidence,
                evaluation,
                errors,
            )

        try:
            active_analyst = self.analyst or LLMAnalyst()
            analysis = active_analyst.analyze(self.topic, evidence, remaining)
        except Exception as exc:
            errors.append(f"分析失败：{exc}")
            analysis = _failed_analysis("模型分析不可用，未生成事实结论。")

        status = RunStatus.COMPLETED if not errors and analysis.claims else RunStatus.PARTIAL
        evaluation = evaluate_analysis(analysis, evidence)
        return Report(self.topic, status, analysis, evidence, evaluation, errors)

    def plan(self) -> PlanningReport:
        collection_report = self._collect()
        articles = collection_report.articles
        errors = list(collection_report.errors)
        if not articles:
            return PlanningReport(self.topic, RunStatus.PARTIAL, articles, [], errors)

        remaining = self._budget.remaining()
        if remaining <= 0:
            errors.append("任务在生成搜索计划前超时")
            return PlanningReport(self.topic, RunStatus.PARTIAL, articles, [], errors)

        try:
            active_planner = self.planner or LLMQuestionPlanner()
            plans = active_planner.plan(self.topic, articles, remaining)
        except Exception as exc:
            errors.append(f"搜索计划生成失败：{exc}")
            return PlanningReport(self.topic, RunStatus.PARTIAL, articles, [], errors)

        status = RunStatus.COMPLETED if not errors else RunStatus.PARTIAL
        return PlanningReport(self.topic, status, articles, plans, errors)

    def _collect(self) -> CollectionReport:
        return _execute_collection(
            self.topic,
            self.feeds,
            budget=self._budget,
            limit=self.limit,
            collector=self.collector,
            relevance_selector=self.relevance_selector,
            max_workers=self.max_workers,
            max_attempts=self.max_attempts,
            source_timeout_seconds=self.source_timeout_seconds,
        )


def _failed_analysis(message: str) -> Analysis:
    return Analysis(summary=message, claims=[], uncertainties=[message])
