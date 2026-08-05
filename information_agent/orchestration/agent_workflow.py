from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

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
from ..storage import SQLiteCollectionStore, default_database_path
from .execution import ExecutionBudget

DEFAULT_MAX_AGENT_STEPS = 3
DEFAULT_MAX_ATTEMPTS = 3

T = TypeVar("T")


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
    if not evidence:
        return AgentReport(
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

    budget = ExecutionBudget.start(timeout_seconds)
    observations: list[AgentObservation] = []
    plans = []
    answers: list[SearchAnswer] = []
    errors: list[str] = []
    seen_queries: set[str] = set()

    try:
        active_decider = decider or LLMResearchDecider()
    except Exception as exc:
        return _failed_report(
            run_id,
            topic,
            evidence,
            plans,
            answers,
            observations,
            AgentStopReason.ERROR,
            f"Agent 决策器初始化失败：{exc}",
        )

    active_answerer = answerer
    search_count = 0
    decision_count = 0
    while True:
        if budget.remaining() <= 0:
            return _failed_report(
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
        decision_count += 1
        search_limit_reached = search_count >= max_steps
        try:
            decision = _call_decider_with_retries(
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
            )
        except Exception as exc:
            return _failed_report(
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

        if isinstance(decision, FinishDecision):
            return _finish_report(
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

        if search_limit_reached:
            errors.append(f"Agent 达到最大搜索动作数 {max_steps}，未收到 finish 决策")
            return AgentReport(
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

        if not isinstance(decision, SearchDecision):
            raise TypeError("Agent 决策类型不受支持")
        normalized_queries = {_normalize_query(query.query) for query in decision.plan.queries}
        if normalized_queries & seen_queries:
            return _failed_report(
                run_id,
                topic,
                evidence,
                plans,
                answers,
                observations,
                AgentStopReason.REPEATED_QUERY,
                "Agent 生成了重复搜索查询",
            )
        seen_queries.update(normalized_queries)
        plans.append(decision.plan)
        search_count += 1

        try:
            if active_answerer is None:
                active_answerer = HostedSearchAnswerer()
            answer = _call_with_retries(
                lambda timeout, answerer=active_answerer, plan=decision.plan: answerer.answer(
                    plan, timeout
                ),
                budget,
                max_attempts,
            )
        except Exception as exc:
            return _failed_report(
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


def _validate_input(timeout_seconds: float, max_steps: int, max_attempts: int) -> None:
    if timeout_seconds <= 0:
        raise ValueError("Agent 时限必须大于 0 秒")
    if max_steps <= 0:
        raise ValueError("Agent 最大步骤数必须大于 0")
    if max_attempts <= 0:
        raise ValueError("单步最大尝试次数必须大于 0")
