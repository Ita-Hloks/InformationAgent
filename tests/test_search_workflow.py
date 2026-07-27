from __future__ import annotations

from information_agent.collection import RawFeedEntry
from information_agent.contracts import RunStatus
from information_agent.investigation import QuestionKind, SearchPlan, SearchQuery
from information_agent.orchestration import search
from information_agent.search import SearchAnswer, SearchAnswerStatus
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
        return [
            SearchPlan(
                evidence_id=evidence[0].id,
                trigger_quote="推理成本下降 70%",
                question="成本降幅的比较基线是什么？",
                kind=QuestionKind.QUANTITATIVE_CLAIM,
                priority=1,
                queries=(SearchQuery("AI 芯片 推理成本测试", "寻找原始测试材料"),),
            )
        ]


class FakeAnswerer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[SearchPlan, float]] = []

    def answer(self, plan: SearchPlan, timeout: float) -> SearchAnswer:
        self.calls.append((plan, timeout))
        if self.error is not None:
            raise self.error
        return SearchAnswer(
            evidence_id=plan.evidence_id,
            question=plan.question,
            answer="公开材料未披露完整比较基线。",
            status=SearchAnswerStatus.INSUFFICIENT_EVIDENCE,
        )


def _collector(_: str, __: float) -> list[RawFeedEntry]:
    return [
        RawFeedEntry(
            "https://example.com/ai",
            "AI 芯片发布",
            "新一代 AI 芯片已经发布，厂商称推理成本下降 70%，但尚未披露完整测试条件和比较基线。",
        )
    ]


def test_search_runs_collection_planning_and_answering() -> None:
    answerer = FakeAnswerer()

    report = search(
        "AI 芯片",
        ["feed"],
        collector=_collector,
        planner=FakePlanner(),
        answerer=answerer,
    )

    assert report.status is RunStatus.COMPLETED
    assert len(report.articles) == 1
    assert len(report.plans) == 1
    assert len(report.answers) == 1
    assert report.answers[0].status is SearchAnswerStatus.INSUFFICIENT_EVIDENCE
    assert answerer.calls[0][0] is report.plans[0]
    assert answerer.calls[0][1] > 0
    assert report.errors == []


def test_search_preserves_plan_when_an_answer_fails() -> None:
    report = search(
        "AI 芯片",
        ["feed"],
        collector=_collector,
        planner=FakePlanner(),
        answerer=FakeAnswerer(RuntimeError("联网服务不可用")),
    )

    assert report.status is RunStatus.PARTIAL
    assert len(report.plans) == 1
    assert report.answers == []
    assert report.errors == ["搜索回答失败：联网服务不可用"]
