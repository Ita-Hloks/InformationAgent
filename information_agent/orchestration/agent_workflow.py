from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar

from ..agent import (
    AgentDecision,
    AgentDecisionResponseError,
    AgentObservation,
    AgentReport,
    AgentStopReason,
    FinishDecision,
    FinishReason,
    LLMResearchDecider,
    ResearchDecider,
    SearchDecision,
)
from ..common import DEFAULT_LLM_TIMEOUT_SECONDS, is_retryable_llm_error
from ..contracts import RunStatus
from ..search import (
    HostedSearchAnswerer,
    SearchAnswer,
    SearchAnswerer,
    SearchAnswerStatus,
)
from ..storage import (
    AnalysisAttemptStatus,
    AnalysisRunStatus,
    AnalysisStepStatus,
    SQLiteCollectionStore,
    default_database_path,
)
from .execution import ExecutionBudget

DEFAULT_MAX_AGENT_STEPS = 3
DEFAULT_MAX_ATTEMPTS = 3

T = TypeVar("T")


class _AgentRunRecorder:
    """Store agent decisions, search observations, and the final report as analysis artifacts."""

    def __init__(
        self,
        store: SQLiteCollectionStore,
        research_run_id: str,
        *,
        timeout_seconds: float,
        max_steps: int,
        max_attempts: int,
    ) -> None:
        self._store = store
        self._run = store.create_analysis_run(
            research_run_id,
            "agent_research",
            {
                "timeout_seconds": timeout_seconds,
                "max_steps": max_steps,
                "max_attempts": max_attempts,
            },
        )
        self._position = 0

    @property
    def analysis_run_id(self) -> str:
        return self._run.id

    def execute(
        self,
        step_key: str,
        operation: str,
        request_payload: dict[str, Any],
        action: Callable[[], T],
        *,
        result_kind: str,
        result_payload: Callable[[T], dict[str, Any]],
    ) -> T:
        self._position += 1
        step = self._store.create_analysis_step(self._run.id, self._position, step_key)
        self._store.set_analysis_step_status(step.id, AnalysisStepStatus.RUNNING)
        attempt = self._store.create_analysis_attempt(
            step.id,
            operation,
            _payload_hash(request_payload),
            f"{step_key}:attempt-1",
        )
        self._store.record_analysis_artifact(
            self._run.id,
            "request",
            request_payload,
            artifact_key=f"{step_key}:attempt-1:request",
            metadata={"operation": operation},
            step_id=step.id,
            attempt_id=attempt.id,
        )
        try:
            result = action()
        except Exception as exc:
            self._store.record_analysis_artifact(
                self._run.id,
                "error",
                {"type": type(exc).__name__, "message": str(exc)},
                artifact_key=f"{step_key}:attempt-1:error",
                metadata={"operation": operation},
                step_id=step.id,
                attempt_id=attempt.id,
            )
            self._store.set_analysis_attempt_status(
                attempt.id,
                AnalysisAttemptStatus.FAILED,
                error=exc,
            )
            self._store.set_analysis_step_status(
                step.id,
                AnalysisStepStatus.FAILED,
                error=exc,
            )
            raise

        self._store.record_analysis_artifact(
            self._run.id,
            result_kind,
            result_payload(result),
            artifact_key=f"{step_key}:attempt-1:result",
            metadata={"operation": operation},
            step_id=step.id,
            attempt_id=attempt.id,
        )
        self._store.set_analysis_attempt_status(attempt.id, AnalysisAttemptStatus.SUCCEEDED)
        self._store.set_analysis_step_status(step.id, AnalysisStepStatus.SUCCEEDED)
        return result

    def finalize(self, report: AgentReport) -> AgentReport:
        persisted_report = replace(report, analysis_run_id=self._run.id)
        self.execute(
            "finalize",
            "agent_finalize",
            {"run_id": report.run_id, "stop_reason": report.stop_reason.value},
            lambda: persisted_report,
            result_kind="agent_report",
            result_payload=_agent_report_payload,
        )
        status = (
            AnalysisRunStatus.COMPLETED
            if persisted_report.status is RunStatus.COMPLETED
            else AnalysisRunStatus.PARTIAL
        )
        error = (
            {"type": "AgentRunError", "message": "; ".join(persisted_report.errors)}
            if persisted_report.errors
            else None
        )
        self._store.set_analysis_run_status(self._run.id, status, error=error)
        return persisted_report


def agent_run(
    run_id: str,
    *,
    database_path: str | Path | None = None,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    max_steps: int = DEFAULT_MAX_AGENT_STEPS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    decider: ResearchDecider | None = None,
    answerer: SearchAnswerer | None = None,
) -> AgentReport:
    """基于已入库证据运行一个只允许搜索或结束的有界 Agent。"""

    _validate_input(timeout_seconds, max_steps, max_attempts)
    store = SQLiteCollectionStore(database_path or default_database_path())
    topic, evidence = store.load_planning_input(run_id)
    recorder = _AgentRunRecorder(
        store,
        run_id,
        timeout_seconds=timeout_seconds,
        max_steps=max_steps,
        max_attempts=max_attempts,
    )
    if not evidence:
        return recorder.finalize(
            AgentReport(
                run_id,
                topic,
                RunStatus.PARTIAL,
                [],
                [],
                [],
                None,
                (),
                ("没有可供 Agent 判断的已选证据",),
                0,
                AgentStopReason.NO_EVIDENCE,
            )
        )

    budget = ExecutionBudget.start(timeout_seconds)
    observations: list[AgentObservation] = []
    plans = []
    answers: list[SearchAnswer] = []
    errors: list[str] = []
    seen_queries: set[str] = set()

    try:
        active_decider = decider or LLMResearchDecider()
    except Exception as exc:
        return recorder.finalize(
            _failed_report(
                run_id,
                topic,
                evidence,
                plans,
                answers,
                observations,
                AgentStopReason.ERROR,
                f"Agent 决策器初始化失败：{exc}",
            )
        )

    active_answerer = answerer
    search_count = 0
    decision_count = 0
    while True:
        if budget.remaining() <= 0:
            return recorder.finalize(
                _failed_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    observations,
                    AgentStopReason.TIMEOUT,
                    "Agent 在作出下一步决策前超时",
                    steps=decision_count,
                )
            )
        decision_count += 1
        search_limit_reached = search_count >= max_steps
        try:
            decision = recorder.execute(
                f"decision-{decision_count}",
                "agent_decision",
                _decision_request_payload(topic, evidence, observations, budget),
                lambda search_limit_reached=search_limit_reached: _call_decider_with_retries(
                    active_decider,
                    topic,
                    evidence,
                    observations,
                    budget,
                    max_attempts,
                    initial_validation_feedback=(
                        f"已达到最大搜索动作数 {max_steps}；本轮只能输出 finish，不能继续 search。"
                        if search_limit_reached
                        else None
                    ),
                ),
                result_kind="agent_decision",
                result_payload=_agent_decision_payload,
            )
        except Exception as exc:
            return recorder.finalize(
                _failed_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    observations,
                    _failure_reason(budget),
                    f"Agent 决策失败：{exc}",
                    steps=decision_count,
                )
            )

        if isinstance(decision, FinishDecision):
            return recorder.finalize(
                _finish_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    observations,
                    decision,
                    decision_count,
                    errors,
                )
            )

        if search_limit_reached:
            errors.append(f"Agent 达到最大搜索动作数 {max_steps}，未收到 finish 决策")
            return recorder.finalize(
                AgentReport(
                    run_id,
                    topic,
                    RunStatus.PARTIAL,
                    evidence,
                    plans,
                    answers,
                    None,
                    (),
                    ("Agent 在搜索动作限制内未完成研究",),
                    decision_count,
                    AgentStopReason.MAX_STEPS,
                    errors,
                )
            )

        if not isinstance(decision, SearchDecision):
            raise TypeError("Agent 决策类型不受支持")
        normalized_queries = {_normalize_query(query.query) for query in decision.plan.queries}
        if normalized_queries & seen_queries:
            return recorder.finalize(
                _failed_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    observations,
                    AgentStopReason.REPEATED_QUERY,
                    "Agent 生成了重复搜索查询",
                )
            )
        seen_queries.update(normalized_queries)
        plans.append(decision.plan)
        search_count += 1

        try:

            def answer_search(plan=decision.plan) -> SearchAnswer:
                nonlocal active_answerer
                if active_answerer is None:
                    active_answerer = HostedSearchAnswerer()
                return _call_with_retries(
                    lambda timeout: active_answerer.answer(plan, timeout),
                    budget,
                    max_attempts,
                )

            answer = recorder.execute(
                f"search-{search_count}",
                "hosted_search",
                _search_request_payload(decision.plan, budget),
                answer_search,
                result_kind="search_answer",
                result_payload=_search_answer_payload,
            )
        except Exception as exc:
            return recorder.finalize(
                _failed_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    observations,
                    _failure_reason(budget),
                    f"Agent 搜索工具失败：{exc}",
                    steps=decision_count,
                )
            )
        answers.append(answer)
        observations.append(AgentObservation(decision.plan, answer))


def _call_with_retries(
    operation: Callable[[float], T],
    budget: ExecutionBudget,
    max_attempts: int,
) -> T:
    last_error: Exception | None = None
    for _ in range(max_attempts):
        remaining = budget.remaining()
        if remaining <= 0:
            raise TimeoutError("Agent 总时限已耗尽")
        try:
            return operation(remaining)
        except Exception as exc:
            last_error = exc
            if not is_retryable_llm_error(exc):
                break
    if last_error is None:
        raise AssertionError("重试循环必须执行至少一次")
    raise last_error


def _call_decider_with_retries(
    decider: ResearchDecider,
    topic: str,
    evidence,
    observations,
    budget: ExecutionBudget,
    max_attempts: int,
    initial_validation_feedback: str | None = None,
) -> AgentDecision:
    last_error: Exception | None = None
    validation_feedback = initial_validation_feedback
    for _ in range(max_attempts):
        remaining = budget.remaining()
        if remaining <= 0:
            raise TimeoutError("Agent 总时限已耗尽")
        try:
            return decider.decide(
                topic,
                evidence,
                observations,
                remaining,
                validation_feedback=validation_feedback,
            )
        except AgentDecisionResponseError as exc:
            last_error = exc
            validation_feedback = str(exc)
        except Exception as exc:
            last_error = exc
            if not is_retryable_llm_error(exc):
                break
            validation_feedback = None
    if last_error is None:
        raise AssertionError("重试循环必须执行至少一次")
    raise last_error


def _failed_report(
    run_id,
    topic,
    evidence,
    plans,
    answers,
    observations,
    stop_reason,
    error,
    *,
    steps: int | None = None,
) -> AgentReport:
    return AgentReport(
        run_id,
        topic,
        RunStatus.PARTIAL,
        evidence,
        plans,
        answers,
        None,
        (),
        ("Agent 未生成最终结论",),
        len(observations) if steps is None else steps,
        stop_reason,
        [error],
    )


def _finish_report(
    run_id,
    topic,
    evidence,
    plans,
    answers,
    observations,
    decision: FinishDecision,
    step: int,
    errors,
) -> AgentReport:
    searches_found_no_evidence = bool(observations) and all(
        observation.answer.status is SearchAnswerStatus.INSUFFICIENT_EVIDENCE
        for observation in observations
    )
    reason_reports_insufficient = decision.reason is FinishReason.INSUFFICIENT_AFTER_SEARCH
    insufficient = reason_reports_insufficient or searches_found_no_evidence
    reason_mismatches_searches = searches_found_no_evidence and not reason_reports_insufficient
    final_answer = None if reason_mismatches_searches else decision.answer
    evidence_ids = () if reason_mismatches_searches else decision.evidence_ids
    citations = () if reason_mismatches_searches else decision.citations
    uncertainties = decision.uncertainties
    if reason_mismatches_searches:
        uncertainties = (*uncertainties, "所有搜索均未获得可验证证据")
    return AgentReport(
        run_id,
        topic,
        RunStatus.PARTIAL if insufficient else RunStatus.COMPLETED,
        evidence,
        plans,
        answers,
        final_answer,
        evidence_ids,
        uncertainties,
        step,
        AgentStopReason.INSUFFICIENT_EVIDENCE if insufficient else AgentStopReason.FINISHED,
        errors,
        citations,
    )


def _failure_reason(budget: ExecutionBudget) -> AgentStopReason:
    return AgentStopReason.TIMEOUT if budget.remaining() <= 0 else AgentStopReason.ERROR


def _normalize_query(value: str) -> str:
    return " ".join(value.casefold().split())


def _payload_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _decision_request_payload(
    topic: str,
    evidence,
    observations: list[AgentObservation],
    budget: ExecutionBudget,
) -> dict[str, Any]:
    return {
        "topic": topic,
        "evidence_ids": [item.id for item in evidence],
        "observations": [
            {
                "plan": _search_plan_payload(observation.plan),
                "answer": _search_answer_payload(observation.answer),
            }
            for observation in observations
        ],
        "remaining_seconds": round(budget.remaining(), 6),
    }


def _search_request_payload(plan, budget: ExecutionBudget) -> dict[str, Any]:
    return {
        "plan": _search_plan_payload(plan),
        "remaining_seconds": round(budget.remaining(), 6),
    }


def _agent_decision_payload(decision: AgentDecision) -> dict[str, Any]:
    if isinstance(decision, SearchDecision):
        return {"decision": "search", "plan": _search_plan_payload(decision.plan)}
    return {
        "decision": "finish",
        "reason": decision.reason.value,
        "citations": [
            {
                "claim": citation.claim,
                "evidence_ids": list(citation.evidence_ids),
                "source_urls": list(citation.source_urls),
            }
            for citation in decision.citations
        ],
        "uncertainties": list(decision.uncertainties),
    }


def _search_plan_payload(plan) -> dict[str, Any]:
    return {
        "evidence_id": plan.evidence_id,
        "trigger_quote": plan.trigger_quote,
        "question": plan.question,
        "kind": plan.kind.value,
        "priority": plan.priority,
        "queries": [{"query": query.query, "purpose": query.purpose} for query in plan.queries],
    }


def _search_answer_payload(answer: SearchAnswer) -> dict[str, Any]:
    return {
        "evidence_id": answer.evidence_id,
        "question": answer.question,
        "answer": answer.answer,
        "status": answer.status.value,
        "sources": [
            {
                "title": source.title,
                "url": source.url,
                "site_name": source.site_name,
                "published_at": source.published_at,
                "snippet": source.snippet,
                "reference": source.reference,
            }
            for source in answer.sources
        ],
    }


def _agent_report_payload(report: AgentReport) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "topic": report.topic,
        "status": report.status.value,
        "articles": [item.id for item in report.articles],
        "plans": [_search_plan_payload(plan) for plan in report.plans],
        "answers": [_search_answer_payload(answer) for answer in report.answers],
        "final_answer": report.final_answer,
        "evidence_ids": list(report.evidence_ids),
        "citations": [
            {
                "claim": citation.claim,
                "evidence_ids": list(citation.evidence_ids),
                "source_urls": list(citation.source_urls),
            }
            for citation in report.citations
        ],
        "uncertainties": list(report.uncertainties),
        "steps": report.steps,
        "stop_reason": report.stop_reason.value,
        "errors": report.errors,
    }


def _validate_input(timeout_seconds: float, max_steps: int, max_attempts: int) -> None:
    if timeout_seconds <= 0:
        raise ValueError("Agent 时限必须大于 0 秒")
    if max_steps <= 0:
        raise ValueError("Agent 最大步骤数必须大于 0")
    if max_attempts <= 0:
        raise ValueError("单步最大尝试次数必须大于 0")
