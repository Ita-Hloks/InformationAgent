from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from information_agent.agent import (
    AgentDecisionResponseError,
    AgentObservation,
    AgentReport,
    AgentStopReason,
    FinishDecision,
    FinishReason,
    SearchDecision,
    parse_agent_decision,
)
from information_agent.agent import decider as agent_decider
from information_agent.cli import main
from information_agent.collection import RawFeedEntry
from information_agent.contracts import RunStatus
from information_agent.investigation import (
    SEARCH_PLAN_CONTRACT,
    QuestionKind,
    SearchPlan,
    SearchQuery,
)
from information_agent.investigation import planner as investigation_planner
from information_agent.orchestration.agent_workflow import agent_run
from information_agent.orchestration.ingestion import ingest
from information_agent.search import SearchAnswer, SearchAnswerStatus
from information_agent.selection import SelectedEvidence


def _collector(_: str, __: float) -> list[RawFeedEntry]:
    return [
        RawFeedEntry(
            "https://example.com/ai",
            "AI 芯片发布",
            "厂商发布了新一代 AI 芯片，并宣称推理成本下降 70%，但没有说明比较基线。",
        )
    ]


def _plan(evidence_id: int = 1, query: str = "AI 芯片 推理成本 独立测试") -> SearchPlan:
    return SearchPlan(
        evidence_id=evidence_id,
        trigger_quote="推理成本下降 70%",
        question="推理成本降幅采用了什么比较基线？",
        kind=QuestionKind.QUANTITATIVE_CLAIM,
        priority=1,
        queries=(SearchQuery(query, "寻找独立测试材料"),),
    )


def _finish() -> FinishDecision:
    return FinishDecision(
        FinishReason.INSUFFICIENT_AFTER_SEARCH,
        "现有公开材料没有披露可比较的测试基线。",
        (1,),
        ("缺少独立测试报告",),
    )


class SequenceDecider:
    def __init__(self, decisions, failures: int = 0) -> None:
        self.decisions = list(decisions)
        self.failures = failures
        self.calls: list[list[AgentObservation]] = []
        self.validation_feedback: list[str | None] = []

    def decide(
        self,
        topic: str,
        evidence: list[SelectedEvidence],
        observations: list[AgentObservation],
        timeout: float,
        validation_feedback: str | None = None,
    ):
        assert topic == "AI 芯片"
        assert evidence[0].id == 1
        assert timeout > 0
        self.calls.append(list(observations))
        self.validation_feedback.append(validation_feedback)
        if self.failures:
            self.failures -= 1
            raise ConnectionError("模型连接中断")
        return self.decisions.pop(0)


class FormattingFeedbackDecider:
    def __init__(self) -> None:
        self.validation_feedback: list[str | None] = []

    def decide(
        self,
        topic: str,
        evidence: list[SelectedEvidence],
        observations: list[AgentObservation],
        timeout: float,
        validation_feedback: str | None = None,
    ):
        assert topic == "AI 芯片"
        assert evidence[0].id == 1
        assert observations == []
        assert timeout > 0
        self.validation_feedback.append(validation_feedback)
        if validation_feedback is None:
            raise AgentDecisionResponseError(
                "kind 不是支持的可核查主张类型",
                '{"decision":"search"}',
            )
        return _finish()


class RecordingAnswerer:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[SearchPlan] = []

    def answer(self, plan: SearchPlan, timeout: float) -> SearchAnswer:
        assert timeout > 0
        self.calls.append(plan)
        if self.failures:
            self.failures -= 1
            raise ConnectionError("搜索连接中断")
        return SearchAnswer(
            evidence_id=plan.evidence_id,
            question=plan.question,
            answer="没有找到完整比较基线。",
            status=SearchAnswerStatus.INSUFFICIENT_EVIDENCE,
        )


def _ingested_run(tmp_path: Path):
    database_path = tmp_path / "information-agent.db"
    collection = ingest(
        "AI 芯片",
        ["feed"],
        database_path=database_path,
        collector=_collector,
    )
    return database_path, collection.run_id


def test_parse_agent_finish_requires_explicit_valid_evidence() -> None:
    evidence = ingest_evidence()
    raw = json.dumps(
        {
            "decision": "finish",
            "reason": "evidence_sufficient",
            "answer": "现有证据足以确认产品已经发布。",
            "evidence_ids": [1],
            "uncertainties": [],
        },
        ensure_ascii=False,
    )

    decision = parse_agent_decision(raw, evidence)

    assert isinstance(decision, FinishDecision)
    assert decision.reason is FinishReason.EVIDENCE_SUFFICIENT
    assert decision.evidence_ids == (1,)


def test_parse_agent_finish_normalizes_numeric_string_evidence_ids() -> None:
    evidence = ingest_evidence()
    raw = json.dumps(
        {
            "decision": "finish",
            "reason": "evidence_sufficient",
            "answer": "现有证据足以形成谨慎结论。",
            "evidence_ids": ["1"],
            "uncertainties": [],
        },
        ensure_ascii=False,
    )

    decision = parse_agent_decision(raw, evidence)

    assert isinstance(decision, FinishDecision)
    assert decision.evidence_ids == (1,)


def test_parse_agent_finish_normalizes_single_uncertainty_string() -> None:
    evidence = ingest_evidence()
    raw = json.dumps(
        {
            "decision": "finish",
            "reason": "evidence_sufficient",
            "answer": "现有证据足以形成谨慎结论。",
            "evidence_ids": [1],
            "uncertainties": "原始文章正文过短，结论仍存在范围限制。",
        },
        ensure_ascii=False,
    )

    decision = parse_agent_decision(raw, evidence)

    assert isinstance(decision, FinishDecision)
    assert decision.uncertainties == ("原始文章正文过短，结论仍存在范围限制。",)


def test_parse_agent_finish_normalizes_empty_uncertainties() -> None:
    evidence = ingest_evidence()
    for empty_value in (None, ""):
        raw = json.dumps(
            {
                "decision": "finish",
                "reason": "evidence_sufficient",
                "answer": "现有证据足以形成谨慎结论。",
                "evidence_ids": [1],
                "uncertainties": empty_value,
            },
            ensure_ascii=False,
        )

        decision = parse_agent_decision(raw, evidence)

        assert isinstance(decision, FinishDecision)
        assert decision.uncertainties == ()


def test_parse_agent_finish_rejects_non_string_uncertainties() -> None:
    evidence = ingest_evidence()
    raw = json.dumps(
        {
            "decision": "finish",
            "reason": "evidence_sufficient",
            "answer": "现有证据足以形成谨慎结论。",
            "evidence_ids": [1],
            "uncertainties": {"value": "格式错误"},
        },
        ensure_ascii=False,
    )

    with pytest.raises(ValueError, match="uncertainties 必须是字符串数组"):
        parse_agent_decision(raw, evidence)


def test_parse_agent_search_reuses_search_plan_validation() -> None:
    evidence = ingest_evidence()
    raw = json.dumps(
        {
            "decision": "search",
            "plan": {
                "evidence_id": 1,
                "trigger_quote": "推理成本下降 70%",
                "question": "推理成本降幅采用了什么比较基线？",
                "kind": "quantitative_claim",
                "priority": 1,
                "queries": [
                    {
                        "query": "AI 芯片 推理成本 独立测试",
                        "purpose": "寻找独立测试材料",
                    }
                ],
            },
        },
        ensure_ascii=False,
    )

    decision = parse_agent_decision(raw, evidence)

    assert isinstance(decision, SearchDecision)
    assert decision.plan.question == "推理成本降幅采用了什么比较基线？"


def test_agent_and_planner_share_search_plan_contract() -> None:
    assert SEARCH_PLAN_CONTRACT in agent_decider._system_prompt()
    assert SEARCH_PLAN_CONTRACT in investigation_planner._system_prompt()


def ingest_evidence() -> list[SelectedEvidence]:
    from information_agent.normalization import normalize_evidence

    article = normalize_evidence(_collector("", 0), min_content_chars=1)[0]
    return [SelectedEvidence(article, evidence_id=1, relevance_score=1.0)]


def test_agent_finishes_without_calling_search(tmp_path: Path) -> None:
    database_path, run_id = _ingested_run(tmp_path)
    decider = SequenceDecider([_finish()])
    answerer = RecordingAnswerer()

    report = agent_run(
        run_id,
        database_path=database_path,
        decider=decider,
        answerer=answerer,
    )

    assert report.status is RunStatus.COMPLETED
    assert report.stop_reason is AgentStopReason.FINISHED
    assert report.steps == 1
    assert report.plans == []
    assert answerer.calls == []


def test_agent_retries_format_failure_with_feedback(tmp_path: Path) -> None:
    database_path, run_id = _ingested_run(tmp_path)
    decider = FormattingFeedbackDecider()
    answerer = RecordingAnswerer()

    report = agent_run(
        run_id,
        database_path=database_path,
        decider=decider,
        answerer=answerer,
        max_attempts=2,
    )

    assert report.status is RunStatus.COMPLETED
    assert report.stop_reason is AgentStopReason.FINISHED
    assert report.steps == 1
    assert decider.validation_feedback == [None, "kind 不是支持的可核查主张类型"]
    assert answerer.calls == []


def test_agent_returns_search_observation_to_next_decision(tmp_path: Path) -> None:
    database_path, run_id = _ingested_run(tmp_path)
    plan = _plan()
    decider = SequenceDecider([SearchDecision(plan), _finish()])
    answerer = RecordingAnswerer()

    report = agent_run(
        run_id,
        database_path=database_path,
        decider=decider,
        answerer=answerer,
    )

    assert report.status is RunStatus.COMPLETED
    assert report.steps == 2
    assert answerer.calls == [plan]
    assert decider.calls[0] == []
    assert decider.calls[1][0].answer is report.answers[0]


def test_agent_retries_same_decision_and_tool_calls(tmp_path: Path) -> None:
    database_path, run_id = _ingested_run(tmp_path)
    plan = _plan()
    decider = SequenceDecider([SearchDecision(plan), _finish()], failures=2)
    answerer = RecordingAnswerer(failures=2)

    report = agent_run(
        run_id,
        database_path=database_path,
        decider=decider,
        answerer=answerer,
        max_attempts=3,
    )

    assert report.status is RunStatus.COMPLETED
    assert report.steps == 2
    assert len(decider.calls) == 4
    assert answerer.calls == [plan, plan, plan]


def test_agent_does_not_report_completion_at_step_limit(tmp_path: Path) -> None:
    database_path, run_id = _ingested_run(tmp_path)
    plan = _plan()

    report = agent_run(
        run_id,
        database_path=database_path,
        decider=SequenceDecider([SearchDecision(plan)]),
        answerer=RecordingAnswerer(),
        max_steps=1,
    )

    assert report.status is RunStatus.PARTIAL
    assert report.stop_reason is AgentStopReason.MAX_STEPS
    assert report.final_answer is None


def test_agent_run_cli_uses_separate_command(monkeypatch, capsys) -> None:
    report = AgentReport(
        run_id="run-123",
        topic="AI",
        status=RunStatus.COMPLETED,
        articles=[],
        plans=[],
        answers=[],
        final_answer="无需继续搜索。",
        evidence_ids=(1,),
        uncertainties=(),
        steps=1,
        stop_reason=AgentStopReason.FINISHED,
    )

    def fake_agent_run(
        run_id: str,
        *,
        timeout_seconds: float,
        max_steps: int,
        max_attempts: int,
    ) -> AgentReport:
        assert run_id == "run-123"
        assert timeout_seconds == 12
        assert max_steps == 2
        assert max_attempts == 4
        return report

    monkeypatch.setattr(
        "information_agent.orchestration.agent_workflow.agent_run",
        fake_agent_run,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "information-agent",
            "agent-run",
            "run-123",
            "--timeout",
            "12",
            "--max-steps",
            "2",
            "--max-attempts",
            "4",
        ],
    )

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "run-123"
    assert payload["status"] == "completed"
    assert payload["stop_reason"] == "finished"
    assert payload["final_answer"] == "无需继续搜索。"
