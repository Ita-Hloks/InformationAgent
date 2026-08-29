from __future__ import annotations

import hashlib
import json
import time
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
from ..common import (
    DEFAULT_LLM_TIMEOUT_SECONDS,
    LLM_RETRY_DELAYS_SECONDS,
    MAX_LLM_REQUEST_TIMEOUT_SECONDS,
    is_retryable_llm_error,
)
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
MIN_AGENT_DECISION_TIMEOUT_SECONDS = 30.0

T = TypeVar("T")


class AgentCancellationRequested(Exception):
    """Raised at a safe boundary after a persisted Agent stop request."""


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
        idempotency_key: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        should_stop: Callable[[], bool] = lambda: False,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
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
            idempotency_key=idempotency_key,
        )
        self._position = 0
        self._sleep = sleep
        self._should_stop = should_stop
        self._on_progress = on_progress

    @property
    def analysis_run_id(self) -> str:
        return self._run.id

    def execute(
        self,
        step_key: str,
        operation: str,
        request_payload: Callable[[], dict[str, Any]],
        action: Callable[[], T],
        *,
        max_attempts: int = 1,
        should_retry: Callable[[Exception], bool] | None = None,
        can_retry: Callable[[float], bool] | None = None,
        result_kind: str,
        result_payload: Callable[[T], dict[str, Any]],
        allow_when_stopped: bool = False,
    ) -> T:
        self._position += 1
        step = self._store.create_analysis_step(self._run.id, self._position, step_key)
        self._store.set_analysis_step_status(step.id, AnalysisStepStatus.RUNNING)
        self._notify(step_key=step_key, phase=operation, status="running")
        last_error: Exception | None = None
        for attempt_no in range(1, max_attempts + 1):
            if self._should_stop() and not allow_when_stopped:
                self._cancel_step(step.id, step_key, "用户请求停止 Agent")
                raise AgentCancellationRequested("用户请求停止 Agent")
            payload = request_payload()
            attempt_key = f"{step_key}:attempt-{attempt_no}"
            attempt = self._store.create_analysis_attempt(
                step.id,
                operation,
                _payload_hash(payload),
                attempt_key,
            )
            self._store.record_analysis_artifact(
                self._run.id,
                "request",
                payload,
                artifact_key=f"{attempt_key}:request",
                metadata={"operation": operation},
                step_id=step.id,
                attempt_id=attempt.id,
            )
            self._notify(
                step_key=step_key,
                phase=operation,
                status="running",
                attempt=attempt_no,
                max_attempts=max_attempts,
            )
            try:
                result = action()
            except Exception as exc:
                if self._should_stop() and not allow_when_stopped:
                    self._store.set_analysis_attempt_status(
                        attempt.id,
                        AnalysisAttemptStatus.CANCELLED,
                        error={
                            "type": "AgentCancellationRequested",
                            "message": "用户请求停止 Agent",
                        },
                    )
                    self._cancel_step(step.id, step_key, "用户请求停止 Agent")
                    raise AgentCancellationRequested("用户请求停止 Agent") from exc
                last_error = exc
                self._store.record_analysis_artifact(
                    self._run.id,
                    "error",
                    {"type": type(exc).__name__, "message": str(exc)},
                    artifact_key=f"{attempt_key}:error",
                    metadata={"operation": operation},
                    step_id=step.id,
                    attempt_id=attempt.id,
                )
                self._store.set_analysis_attempt_status(
                    attempt.id,
                    AnalysisAttemptStatus.FAILED,
                    error=exc,
                )
                retryable = (
                    attempt_no < max_attempts and should_retry is not None and should_retry(exc)
                )
                if retryable:
                    delay = LLM_RETRY_DELAYS_SECONDS[
                        min(attempt_no - 1, len(LLM_RETRY_DELAYS_SECONDS) - 1)
                    ]
                    if can_retry is None or can_retry(delay):
                        self._notify(
                            step_key=step_key,
                            phase=operation,
                            status="retrying",
                            attempt=attempt_no,
                            max_attempts=max_attempts,
                            retryable=True,
                            retry_in_seconds=delay,
                            error={"type": type(exc).__name__, "message": str(exc)},
                        )
                        if self._should_stop() and not allow_when_stopped:
                            self._cancel_step(step.id, step_key, "用户请求停止 Agent")
                            raise AgentCancellationRequested("用户请求停止 Agent") from exc
                        self._sleep(delay)
                        continue
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
                artifact_key=f"{attempt_key}:result",
                metadata={"operation": operation},
                step_id=step.id,
                attempt_id=attempt.id,
            )
            self._store.set_analysis_attempt_status(attempt.id, AnalysisAttemptStatus.SUCCEEDED)
            self._store.set_analysis_step_status(step.id, AnalysisStepStatus.SUCCEEDED)
            self._notify(
                step_key=step_key,
                phase=operation,
                status="succeeded",
                attempt=attempt_no,
                max_attempts=max_attempts,
            )
            return result
        if last_error is None:
            raise AssertionError("重试循环必须执行至少一次")
        raise last_error

    def finalize(self, report: AgentReport) -> AgentReport:
        persisted_report = replace(report, analysis_run_id=self._run.id)
        self.execute(
            "finalize",
            "agent_finalize",
            lambda: {"run_id": report.run_id, "stop_reason": report.stop_reason.value},
            lambda: persisted_report,
            result_kind="agent_report",
            result_payload=_agent_report_payload,
            allow_when_stopped=True,
        )
        status = (
            AnalysisRunStatus.COMPLETED
            if persisted_report.status is RunStatus.COMPLETED
            else AnalysisRunStatus.CANCELLED
            if persisted_report.stop_reason is AgentStopReason.CANCELLED
            else AnalysisRunStatus.FAILED
            if persisted_report.status is RunStatus.FAILED
            else AnalysisRunStatus.PARTIAL
        )
        error = (
            {"type": "AgentRunError", "message": "; ".join(persisted_report.errors)}
            if persisted_report.errors
            else None
        )
        self._store.set_analysis_run_status(self._run.id, status, error=error)
        self._notify(
            step_key="finalize",
            phase="agent_finalize",
            status=status.value,
            attempt=1,
            max_attempts=1,
            error=error,
        )
        return persisted_report

    def _cancel_step(self, step_id: str, step_key: str, message: str) -> None:
        error = {"type": "AgentCancellationRequested", "message": message}
        self._store.set_analysis_step_status(step_id, AnalysisStepStatus.CANCELLED, error=error)
        self._notify(step_key=step_key, phase="cancelled", status="cancelled", error=error)

    def _notify(self, **payload: Any) -> None:
        if self._on_progress is None:
            return
        payload["retryable"] = payload.get("status") == "retrying"
        try:
            self._on_progress(payload)
        except Exception:
            return


def agent_run(
    run_id: str,
    *,
    database_path: str | Path | None = None,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    max_steps: int = DEFAULT_MAX_AGENT_STEPS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    decider: ResearchDecider | None = None,
    answerer: SearchAnswerer | None = None,
    idempotency_key: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
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
        idempotency_key=idempotency_key,
        sleep=sleep,
        should_stop=should_stop,
        on_progress=on_progress,
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
        if should_stop():
            return recorder.finalize(
                _cancelled_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    AgentStopReason.CANCELLED,
                    "用户请求停止 Agent",
                    steps=decision_count,
                )
            )
        if budget.remaining() <= 0:
            return recorder.finalize(
                _timeout_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    steps=decision_count,
                )
                if answers
                else _failed_report(
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
        validation_messages = []
        if search_limit_reached:
            validation_messages.append(
                f"已达到最大搜索动作数 {max_steps}；本轮只能输出 finish，不能继续 search。"
            )
        if answers and _must_finish_before_search(budget):
            validation_messages.append(
                "当前剩余时间不足以再执行一次完整搜索并保留最终决策时间；"
                "本轮必须输出 finish，不能继续 search。"
            )
        validation_feedback = ["\n".join(validation_messages) or None]

        def decide(feedback_state=validation_feedback) -> AgentDecision:
            remaining = budget.remaining()
            if remaining <= 0:
                raise TimeoutError("Agent 总时限已耗尽")
            try:
                return active_decider.decide(
                    topic,
                    evidence,
                    observations,
                    remaining,
                    validation_feedback=feedback_state[0],
                )
            except AgentDecisionResponseError as exc:
                if exc.retryable:
                    feedback_state[0] = (
                        f"{feedback_state[0]}\n{exc}" if feedback_state[0] else str(exc)
                    )
                raise

        try:
            decision = recorder.execute(
                f"decision-{decision_count}",
                "agent_decision",
                lambda feedback_state=validation_feedback: _decision_request_payload(
                    topic,
                    evidence,
                    observations,
                    budget,
                    validation_feedback=feedback_state[0],
                ),
                decide,
                max_attempts=max_attempts,
                should_retry=_should_retry_agent_decision,
                can_retry=lambda delay: _can_retry_with_budget(budget, delay),
                result_kind="agent_decision",
                result_payload=_agent_decision_payload,
            )
        except AgentCancellationRequested as exc:
            return recorder.finalize(
                _cancelled_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    AgentStopReason.CANCELLED,
                    str(exc),
                    steps=decision_count,
                )
            )
        except Exception as exc:
            failure_reason = _failure_reason(budget)
            return recorder.finalize(
                _timeout_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    steps=decision_count,
                )
                if answers and (failure_reason is AgentStopReason.TIMEOUT or _is_timeout_error(exc))
                else _failed_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    observations,
                    failure_reason,
                    f"Agent 决策失败：{exc}",
                    steps=decision_count,
                )
            )

        if should_stop():
            return recorder.finalize(
                _cancelled_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    AgentStopReason.CANCELLED,
                    "用户请求停止 Agent",
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
        if answers and _must_finish_before_search(budget):
            return recorder.finalize(
                _timeout_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    steps=decision_count,
                )
            )
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
        search_plan = decision.plan

        try:

            def answer_search(plan=search_plan) -> SearchAnswer:
                nonlocal active_answerer
                if active_answerer is None:
                    active_answerer = HostedSearchAnswerer()
                remaining = budget.remaining()
                if remaining <= 0:
                    raise TimeoutError("Agent 总时限已耗尽")
                return active_answerer.answer(plan, remaining)

            answer = recorder.execute(
                f"search-{search_count}",
                "hosted_search",
                lambda plan=search_plan: _search_request_payload(plan, budget),
                answer_search,
                max_attempts=max_attempts,
                should_retry=is_retryable_llm_error,
                can_retry=lambda delay: _can_retry_with_budget(budget, delay),
                result_kind="search_answer",
                result_payload=_search_answer_payload,
            )
        except AgentCancellationRequested as exc:
            return recorder.finalize(
                _cancelled_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    AgentStopReason.CANCELLED,
                    str(exc),
                    steps=decision_count,
                )
            )
        except Exception as exc:
            failure_reason = _failure_reason(budget)
            return recorder.finalize(
                _timeout_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    steps=decision_count,
                )
                if answers and (failure_reason is AgentStopReason.TIMEOUT or _is_timeout_error(exc))
                else _failed_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    observations,
                    failure_reason,
                    f"Agent 搜索工具失败：{exc}",
                    steps=decision_count,
                )
            )
        answers.append(answer)
        observations.append(AgentObservation(decision.plan, answer))
        if should_stop():
            return recorder.finalize(
                _cancelled_report(
                    run_id,
                    topic,
                    evidence,
                    plans,
                    answers,
                    AgentStopReason.CANCELLED,
                    "用户请求停止 Agent",
                    steps=decision_count,
                )
            )


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
        RunStatus.FAILED,
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


def _timeout_report(
    run_id,
    topic,
    evidence,
    plans,
    answers,
    *,
    steps: int,
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
        ("Agent 未在剩余时限内生成最终结论，已保留此前的搜索结果。",),
        steps,
        AgentStopReason.TIMEOUT,
        [],
    )


def _cancelled_report(
    run_id,
    topic,
    evidence,
    plans,
    answers,
    stop_reason,
    error,
    *,
    steps: int = 0,
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
        ("Agent 已停止，未生成最终结论",),
        steps,
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


def _must_finish_before_search(budget: ExecutionBudget) -> bool:
    if budget.total_seconds <= MAX_LLM_REQUEST_TIMEOUT_SECONDS:
        return False
    return budget.remaining() <= (
        MAX_LLM_REQUEST_TIMEOUT_SECONDS + MIN_AGENT_DECISION_TIMEOUT_SECONDS
    )


def _can_retry_with_budget(budget: ExecutionBudget, delay: float) -> bool:
    if budget.total_seconds <= MAX_LLM_REQUEST_TIMEOUT_SECONDS:
        return budget.remaining() > delay
    return budget.remaining() > (
        delay + MAX_LLM_REQUEST_TIMEOUT_SECONDS + MIN_AGENT_DECISION_TIMEOUT_SECONDS
    )


def _is_timeout_error(error: Exception) -> bool:
    if isinstance(error, TimeoutError):
        return True
    if getattr(error, "status_code", None) == 524:
        return True
    return type(error).__name__ == "APITimeoutError" or "timed out" in str(error).casefold()


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
    *,
    validation_feedback: str | None,
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
        "validation_feedback": validation_feedback,
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


def _should_retry_agent_decision(error: Exception) -> bool:
    if isinstance(error, AgentDecisionResponseError):
        return error.retryable
    return is_retryable_llm_error(error)


def _validate_input(timeout_seconds: float, max_steps: int, max_attempts: int) -> None:
    if timeout_seconds <= 0:
        raise ValueError("Agent 时限必须大于 0 秒")
    if max_steps <= 0:
        raise ValueError("Agent 最大步骤数必须大于 0")
    if not 1 <= max_attempts <= DEFAULT_MAX_ATTEMPTS:
        raise ValueError(f"单步最大尝试次数必须在 1 到 {DEFAULT_MAX_ATTEMPTS} 次之间")
