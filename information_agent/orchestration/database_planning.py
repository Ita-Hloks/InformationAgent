from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..common import DEFAULT_LLM_TIMEOUT_SECONDS
from ..contracts import RunStatus
from ..investigation import (
    LLMQuestionPlanner,
    PlanningReport,
    PlanningResult,
    QuestionPlanner,
    SearchPlan,
)
from ..selection import SelectedEvidence
from ..storage import PersistedPlanning, SQLiteCollectionStore, default_database_path


@runtime_checkable
class ResultQuestionPlanner(Protocol):
    def plan_with_result(
        self,
        topic: str,
        evidence: list[SelectedEvidence],
        timeout: float,
    ) -> PlanningResult: ...


def plan_run(
    run_id: str,
    *,
    database_path: str | Path | None = None,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    planner: QuestionPlanner | None = None,
) -> PersistedPlanning:
    """从已持久化证据生成问题计划，并将结果写回同一数据库。"""

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a finite positive number")

    store = SQLiteCollectionStore(database_path or default_database_path())
    topic, evidence = store.load_planning_input(run_id)
    planning_run_id = store.start_planning(run_id)
    if not evidence:
        report = PlanningReport(topic, RunStatus.PARTIAL, [], [], ["没有可用于生成计划的已选证据"])
        store.complete_planning(planning_run_id, run_id, [], None)
        return PersistedPlanning(run_id, planning_run_id, report)

    try:
        active_planner = planner or LLMQuestionPlanner()
        plans, raw_response = _create_plans(active_planner, topic, evidence, timeout_seconds)
        store.complete_planning(planning_run_id, run_id, plans, raw_response)
    except Exception as exc:
        raw_response = getattr(exc, "raw_response", None)
        store.fail_planning(planning_run_id, run_id, exc, raw_response)
        report = PlanningReport(
            topic,
            RunStatus.PARTIAL,
            evidence,
            [],
            [f"搜索计划生成失败：{exc}"],
        )
        return PersistedPlanning(run_id, planning_run_id, report)

    report = PlanningReport(topic, RunStatus.COMPLETED, evidence, plans)
    return PersistedPlanning(run_id, planning_run_id, report)


def _create_plans(
    planner: QuestionPlanner,
    topic: str,
    evidence: list[SelectedEvidence],
    timeout_seconds: float,
) -> tuple[list[SearchPlan], str | None]:
    if isinstance(planner, ResultQuestionPlanner):
        result = planner.plan_with_result(topic, evidence, timeout_seconds)
        return result.plans, result.raw_response
    return planner.plan(topic, evidence, timeout_seconds), None
