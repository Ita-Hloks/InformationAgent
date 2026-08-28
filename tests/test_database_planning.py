from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from information_agent.collection import RawFeedEntry
from information_agent.contracts import CollectionReport, RunStatus
from information_agent.investigation import (
    PlanningResponseError,
    PlanningResult,
    QuestionKind,
    SearchPlan,
    SearchQuery,
)
from information_agent.orchestration import database_planning
from information_agent.orchestration.database_planning import plan_run
from information_agent.selection import SelectedEvidence
from information_agent.storage import SQLiteCollectionStore


class RecordingPlanner:
    def __init__(self) -> None:
        self.evidence: list[SelectedEvidence] = []
        self.calls = 0
        self.timeout: float | None = None

    def plan(
        self,
        topic: str,
        evidence: list[SelectedEvidence],
        timeout: float,
    ) -> list[SearchPlan]:
        return self.plan_with_result(topic, evidence, timeout).plans

    def plan_with_result(
        self,
        topic: str,
        evidence: list[SelectedEvidence],
        timeout: float,
    ) -> PlanningResult:
        assert topic == "AI 芯片"
        assert timeout > 0
        self.calls += 1
        self.timeout = timeout
        self.evidence = evidence
        plan = SearchPlan(
            evidence_id=evidence[0].evidence_id,
            trigger_quote="推理成本下降 70%",
            question="推理成本降幅采用了什么比较基线？",
            kind=QuestionKind.QUANTITATIVE_CLAIM,
            priority=1,
            queries=(SearchQuery("AI 芯片 推理成本 基准测试", "寻找原始测试材料"),),
        )
        raw = json.dumps(
            {
                "plans": [
                    {
                        "evidence_id": 1,
                        "trigger_quote": plan.trigger_quote,
                        "question": plan.question,
                        "kind": plan.kind.value,
                        "priority": plan.priority,
                        "queries": [
                            {"query": plan.queries[0].query, "purpose": plan.queries[0].purpose}
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )
        return PlanningResult(raw, [plan])


class FailingPlanner:
    def plan(
        self,
        topic: str,
        evidence: list[SelectedEvidence],
        timeout: float,
    ) -> list[SearchPlan]:
        raise RuntimeError("模型连接失败")


class InvalidResponsePlanner(FailingPlanner):
    def plan_with_result(
        self,
        topic: str,
        evidence: list[SelectedEvidence],
        timeout: float,
    ) -> PlanningResult:
        raise PlanningResponseError("计划字段不符合约定", '{"unexpected": true}')


def _collector(_: str, __: float) -> list[RawFeedEntry]:
    return [
        RawFeedEntry(
            "https://example.com/ai",
            "AI 芯片发布",
            "厂商发布了新一代 AI 芯片，并宣称推理成本下降 70%，但没有说明比较基线。",
        )
    ]


def _completed_run(database_path: Path) -> str:
    from information_agent.normalization import normalize_evidence

    store = SQLiteCollectionStore(database_path)
    article = normalize_evidence(_collector("", 0), min_content_chars=1)[0]
    run_id = store.start_run("AI 芯片", ["feed"])
    store.complete_run(
        run_id,
        CollectionReport(
            "AI 芯片",
            RunStatus.COMPLETED,
            [SelectedEvidence(article, evidence_id=1)],
            [],
        ),
        [article],
    )
    return run_id


def test_plan_run_loads_database_evidence_and_saves_plans(tmp_path: Path) -> None:
    database_path = tmp_path / "information-agent.db"
    run_id = _completed_run(database_path)
    planner = RecordingPlanner()

    result = plan_run(
        run_id,
        database_path=database_path,
        timeout_seconds=12.5,
        planner=planner,
    )

    assert result.report.status is RunStatus.COMPLETED
    assert planner.timeout == 12.5
    assert planner.evidence[0].content.startswith("厂商发布了新一代 AI 芯片")
    assert result.report.articles[0] is planner.evidence[0]
    assert result.report.plans[0].question == "推理成本降幅采用了什么比较基线？"

    with sqlite3.connect(database_path) as connection:
        planning = connection.execute(
            "SELECT run_id, status, raw_response FROM planning_runs WHERE id = ?",
            (result.planning_run_id,),
        ).fetchone()
        stored_plan = connection.execute(
            "SELECT evidence_no, question, kind FROM search_plans WHERE planning_run_id = ?",
            (result.planning_run_id,),
        ).fetchone()
        stored_query = connection.execute(
            "SELECT position, query, purpose FROM search_queries"
        ).fetchone()

    assert planning is not None
    assert planning[0] == run_id
    assert planning[1] == "completed"
    assert json.loads(planning[2])["plans"][0]["evidence_id"] == 1
    assert stored_plan == (1, "推理成本降幅采用了什么比较基线？", "quantitative_claim")
    assert stored_query == (1, "AI 芯片 推理成本 基准测试", "寻找原始测试材料")


def test_plan_run_saves_planner_failure_without_losing_evidence(tmp_path: Path) -> None:
    database_path = tmp_path / "information-agent.db"
    run_id = _completed_run(database_path)

    result = plan_run(
        run_id,
        database_path=database_path,
        planner=FailingPlanner(),
    )

    assert result.report.status is RunStatus.PARTIAL
    assert len(result.report.articles) == 1
    assert result.report.plans == []
    assert result.report.errors == ["搜索计划生成失败：模型连接失败"]
    with sqlite3.connect(database_path) as connection:
        status, errors_json = connection.execute(
            "SELECT status, errors_json FROM planning_runs WHERE id = ?",
            (result.planning_run_id,),
        ).fetchone()
    assert status == "failed"
    assert json.loads(errors_json) == [{"type": "RuntimeError", "message": "模型连接失败"}]


def test_plan_run_preserves_invalid_llm_response(tmp_path: Path) -> None:
    database_path = tmp_path / "information-agent.db"
    run_id = _completed_run(database_path)

    result = plan_run(
        run_id,
        database_path=database_path,
        planner=InvalidResponsePlanner(),
    )

    assert result.report.status is RunStatus.PARTIAL
    with sqlite3.connect(database_path) as connection:
        status, raw_response = connection.execute(
            "SELECT status, raw_response FROM planning_runs WHERE id = ?",
            (result.planning_run_id,),
        ).fetchone()
    assert status == "failed"
    assert raw_response == '{"unexpected": true}'


def test_plan_run_rejects_unknown_collection_run(tmp_path: Path) -> None:
    database_path = tmp_path / "information-agent.db"

    try:
        plan_run("missing-run", database_path=database_path, planner=RecordingPlanner())
    except ValueError as exc:
        assert str(exc) == "不存在的研究运行：missing-run"
    else:
        raise AssertionError("不存在的运行必须被拒绝")


def test_plan_run_rejects_collection_that_is_still_running(tmp_path: Path) -> None:
    database_path = tmp_path / "information-agent.db"
    run_id = SQLiteCollectionStore(database_path).start_run("AI 芯片", ["feed"])

    try:
        plan_run(run_id, database_path=database_path, planner=RecordingPlanner())
    except ValueError as exc:
        assert str(exc) == f"研究运行尚未产生可规划结果：{run_id}"
    else:
        raise AssertionError("尚未完成粗处理的运行必须被拒绝")


@pytest.mark.parametrize("timeout_seconds", [0, -1, float("nan"), float("inf"), float("-inf")])
def test_plan_run_rejects_invalid_timeout_before_storage_or_planner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timeout_seconds: float,
) -> None:
    database_path = tmp_path / "information-agent.db"
    supplied_planner = RecordingPlanner()
    storage_calls: list[object] = []

    def unexpected_store(*args: object) -> None:
        storage_calls.append(args)
        raise AssertionError("storage must not be constructed")

    monkeypatch.setattr(database_planning, "SQLiteCollectionStore", unexpected_store)

    with pytest.raises(ValueError, match="finite positive"):
        database_planning.plan_run(
            "run-id",
            database_path=database_path,
            timeout_seconds=timeout_seconds,
            planner=supplied_planner,
        )

    assert storage_calls == []
    assert supplied_planner.calls == 0
    assert not database_path.exists()
