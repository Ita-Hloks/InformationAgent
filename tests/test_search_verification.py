from __future__ import annotations

from information_agent.investigation import QuestionKind, SearchPlan
from information_agent.search import SearchAnswer, SearchAnswerStatus, verify_connection


class RecordingAnswerer:
    def __init__(self) -> None:
        self.plan: SearchPlan | None = None
        self.timeout: float | None = None

    def answer(self, plan: SearchPlan, timeout: float) -> SearchAnswer:
        self.plan = plan
        self.timeout = timeout
        return SearchAnswer(
            evidence_id=plan.evidence_id,
            question=plan.question,
            answer="验证完成。",
            status=SearchAnswerStatus.ANSWERED,
        )


def test_verify_connection_uses_a_fixed_public_search_plan() -> None:
    answerer = RecordingAnswerer()

    result = verify_connection(12, answerer)

    assert result.status is SearchAnswerStatus.ANSWERED
    assert answerer.timeout == 12
    assert answerer.plan is not None
    assert answerer.plan.evidence_id == 0
    assert answerer.plan.kind is QuestionKind.ATTRIBUTION_CLAIM
    assert answerer.plan.queries[0].query == "Python official documentation"
