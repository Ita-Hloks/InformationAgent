from __future__ import annotations

import math

import pytest

from information_agent.collection import RawFeedEntry
from information_agent.contracts import Analysis, Claim, RunStatus
from information_agent.investigation import SearchPlan
from information_agent.orchestration.execution import ExecutionBudget
from information_agent.orchestration.runner import WorkflowRunner
from information_agent.selection import SelectedEvidence


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class NeverAnalyst:
    def analyze(
        self,
        topic: str,
        evidence: list[SelectedEvidence],
        timeout: float,
    ) -> Analysis:
        raise AssertionError("总预算耗尽后不应调用分析器")


class NeverPlanner:
    def plan(
        self,
        topic: str,
        evidence: list[SelectedEvidence],
        timeout: float,
    ) -> list[SearchPlan]:
        raise AssertionError("总预算耗尽后不应调用规划器")


class RecordingAnalyst:
    def __init__(self) -> None:
        self.timeout: float | None = None

    def analyze(
        self,
        topic: str,
        evidence: list[SelectedEvidence],
        timeout: float,
    ) -> Analysis:
        self.timeout = timeout
        return Analysis(
            summary="采集后仍有时间完成分析。",
            claims=[Claim("AI 芯片取得新进展", [evidence[0].id])],
        )


def test_execution_budget_caps_stage_timeout_and_never_becomes_negative() -> None:
    clock = FakeClock(100.0)
    budget = ExecutionBudget.start(10.0, clock=clock)

    assert budget.deadline == 110.0
    assert budget.remaining() == 10.0
    assert budget.timeout_for(3.0) == 3.0

    clock.advance(8.0)

    assert budget.remaining() == 2.0
    assert budget.timeout_for(3.0) == 2.0

    clock.advance(5.0)

    assert budget.remaining() == 0.0
    assert budget.timeout_for(3.0) == 0.0


@pytest.mark.parametrize("total_seconds", [math.nan, math.inf, -math.inf, 0.0, -1.0])
def test_execution_budget_rejects_nonpositive_and_nonfinite_totals(
    total_seconds: float,
) -> None:
    with pytest.raises(ValueError, match="total_seconds must be a positive finite number"):
        ExecutionBudget.start(total_seconds)


def test_runner_skips_analysis_when_collection_consumes_total_budget() -> None:
    clock = FakeClock()

    def collector(_: str, timeout: float) -> list[RawFeedEntry]:
        assert timeout == 5.0
        clock.advance(timeout)
        return [_matching_article()]

    report = WorkflowRunner(
        topic="AI",
        feeds=["feed"],
        timeout_seconds=5.0,
        collector=collector,
        analyst=NeverAnalyst(),
        max_attempts=1,
        clock=clock,
    ).run()

    assert report.status is RunStatus.PARTIAL
    assert report.evidence == []
    assert report.analysis.summary == "没有找到与主题匹配的 RSS 内容。"
    assert report.errors == ["语义筛选失败：任务在语义筛选前超时"]


def test_runner_passes_only_remaining_budget_to_analysis() -> None:
    clock = FakeClock()
    analyst = RecordingAnalyst()

    def collector(_: str, timeout: float) -> list[RawFeedEntry]:
        assert timeout == 10.0
        clock.advance(4.0)
        return [_matching_article()]

    report = WorkflowRunner(
        topic="AI",
        feeds=["feed"],
        timeout_seconds=10.0,
        collector=collector,
        analyst=analyst,
        max_attempts=1,
        clock=clock,
    ).run()

    assert report.status is RunStatus.COMPLETED
    assert analyst.timeout == 6.0
    assert report.errors == []


def test_runner_skips_planning_when_collection_consumes_total_budget() -> None:
    clock = FakeClock()

    def collector(_: str, timeout: float) -> list[RawFeedEntry]:
        assert timeout == 5.0
        clock.advance(timeout)
        return [_matching_article()]

    report = WorkflowRunner(
        topic="AI",
        feeds=["feed"],
        timeout_seconds=5.0,
        collector=collector,
        planner=NeverPlanner(),
        max_attempts=1,
        clock=clock,
    ).plan()

    assert report.status is RunStatus.PARTIAL
    assert report.articles == []
    assert report.plans == []
    assert report.errors == ["语义筛选失败：任务在语义筛选前超时"]


def _matching_article() -> RawFeedEntry:
    return RawFeedEntry(
        "https://example.com/ai",
        "AI 芯片进展",
        "这是一篇长度超过二十个字符并且与 AI 主题相关的测试文章正文。",
    )
