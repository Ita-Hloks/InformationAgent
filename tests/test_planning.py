from __future__ import annotations

import pytest

from information_agent.collection import RawFeedEntry
from information_agent.contracts import RunStatus
from information_agent.investigation import QuestionKind, SearchPlan, SearchQuery
from information_agent.orchestration import plan
from information_agent.selection import SelectedEvidence


class FakePlanner:
    def plan(
        self,
        topic: str,
        evidence: list[SelectedEvidence],
        timeout: float,
    ) -> list[SearchPlan]:
        assert topic == "AI 芯片"
        assert timeout > 0
        assert len(evidence) == 1
        assert evidence[0].id == 1
        return [
            SearchPlan(
                evidence_id=evidence[0].id,
                trigger_quote="推理成本下降 70%",
                question="成本降幅的比较基线是什么？",
                kind=QuestionKind.QUANTITATIVE_CLAIM,
                priority=1,
                queries=(SearchQuery("AI 芯片 推理成本 70% 测试方法", "寻找原始测试材料"),),
            )
        ]


class FailingPlanner:
    def plan(
        self,
        topic: str,
        evidence: list[SelectedEvidence],
        timeout: float,
    ) -> list[SearchPlan]:
        raise RuntimeError("规划服务不可用")


class EmptyPlanner:
    def plan(
        self,
        topic: str,
        evidence: list[SelectedEvidence],
        timeout: float,
    ) -> list[SearchPlan]:
        return []


def article_collector(_: str, __: float) -> list[RawFeedEntry]:
    return [
        RawFeedEntry(
            "https://example.com/ai",
            "AI 芯片发布",
            "新一代 AI 芯片已经发布，厂商称推理成本下降 70%，但尚未披露完整测试条件和比较基线。",
        )
    ]


def test_plan_collects_filters_and_generates_article_search_plan() -> None:
    def collector(_: str, __: float) -> list[RawFeedEntry]:
        return [
            RawFeedEntry(
                "https://example.com/ai",
                "AI 芯片发布",
                "新一代 AI 芯片已经发布，厂商称推理成本下降 70%，"
                "但尚未披露完整测试条件和比较基线。",
            ),
            RawFeedEntry(
                "https://example.com/weather",
                "天气",
                "今天阳光充足，适合户外活动，气温将保持稳定。",
            ),
        ]

    report = plan(
        "AI 芯片",
        ["feed"],
        collector=collector,
        planner=FakePlanner(),
    )

    assert report.status.value == "completed"
    assert len(report.articles) == 1
    assert report.plans[0].evidence_id == report.articles[0].id
    assert report.errors == []


def test_plan_does_not_create_planner_without_matching_articles() -> None:
    class FailingPlanner:
        def plan(self, *_: object) -> list[SearchPlan]:
            raise AssertionError("没有匹配文章时不应调用规划器")

    def collector(_: str, __: float) -> list[RawFeedEntry]:
        return [RawFeedEntry("https://example.com/weather", "天气", "今天阳光充足，适合户外活动。")]

    report = plan("AI", ["feed"], collector=collector, planner=FailingPlanner())

    assert report.status.value == "partial"
    assert report.plans == []


def test_plan_limits_the_number_of_articles_sent_for_planning() -> None:
    with pytest.raises(ValueError, match="最多检查 5 篇文章"):
        plan("AI", ["feed"], limit=6, planner=FakePlanner())


def test_plan_preserves_source_errors_when_every_feed_fails() -> None:
    def collector(feed: str, _: float) -> list[RawFeedEntry]:
        raise RuntimeError(f"{feed} 连接失败")

    report = plan(
        "AI",
        ["feed-a", "feed-b"],
        collector=collector,
        planner=FailingPlanner(),
    )

    assert report.status is RunStatus.PARTIAL
    assert report.articles == []
    assert report.plans == []
    assert report.errors == [
        "feed-a：feed-a 连接失败",
        "feed-b：feed-b 连接失败",
    ]


def test_plan_reports_planner_failure_without_dropping_articles() -> None:
    report = plan(
        "AI 芯片",
        ["feed"],
        collector=article_collector,
        planner=FailingPlanner(),
    )

    assert report.status is RunStatus.PARTIAL
    assert len(report.articles) == 1
    assert report.plans == []
    assert report.errors == ["搜索计划生成失败：规划服务不可用"]


def test_plan_treats_an_empty_plan_as_completed() -> None:
    report = plan(
        "AI 芯片",
        ["feed"],
        collector=article_collector,
        planner=EmptyPlanner(),
    )

    assert report.status is RunStatus.COMPLETED
    assert len(report.articles) == 1
    assert report.plans == []
    assert report.errors == []
