from __future__ import annotations

from information_agent.investigation import QuestionKind, SearchPlan
from information_agent.search import (
    SearchAnswer,
    SearchAnswerStatus,
    SearchSource,
    verify_connection,
)


class RecordingAnswerer:
    def __init__(self, source_url: str = "https://docs.python.org/3/") -> None:
        self.plan: SearchPlan | None = None
        self.timeout: float | None = None
        self.source_url = source_url

    def answer(self, plan: SearchPlan, timeout: float) -> SearchAnswer:
        self.plan = plan
        self.timeout = timeout
        return SearchAnswer(
            evidence_id=plan.evidence_id,
            question=plan.question,
            answer="验证完成。",
            status=SearchAnswerStatus.ANSWERED,
            sources=(SearchSource("Python Documentation", self.source_url),),
        )


def test_verify_connection_uses_a_fixed_public_search_plan() -> None:
    answerer = RecordingAnswerer()

    result = verify_connection(12, answerer)

    assert result.status is SearchAnswerStatus.ANSWERED
    assert answerer.timeout == 12
    assert answerer.plan is not None
    assert answerer.plan.evidence_id == 0
    assert answerer.plan.kind is QuestionKind.ATTRIBUTION_CLAIM
    assert answerer.plan.queries[0].query == (
        'site:docs.python.org/3 "Python documentation" homepage'
    )


def test_verify_connection_accepts_localized_official_python_source() -> None:
    result = verify_connection(
        12,
        RecordingAnswerer("https://docs.python.org/fr/3/faq/general.html"),
    )

    assert result.status is SearchAnswerStatus.ANSWERED


class NonOfficialSourceAnswerer(RecordingAnswerer):
    def answer(self, plan: SearchPlan, timeout: float) -> SearchAnswer:
        self.plan = plan
        self.timeout = timeout
        return SearchAnswer(
            evidence_id=plan.evidence_id,
            question=plan.question,
            answer="Python 官方文档首页是 https://docs.python.org/3/。",
            status=SearchAnswerStatus.ANSWERED,
            sources=(SearchSource("Python 资料整理", "https://www.zhihu.com/question/1"),),
        )


def test_verify_connection_rejects_non_official_source() -> None:
    result = verify_connection(12, NonOfficialSourceAnswerer())

    assert result.status is SearchAnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer == "未找到 Python 官方文档首页的可验证官方来源。"
