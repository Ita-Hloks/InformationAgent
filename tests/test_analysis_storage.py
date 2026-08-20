from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from information_agent.contracts import CollectionReport, RunStatus
from information_agent.storage import (
    AnalysisAttemptStatus,
    AnalysisRunStatus,
    AnalysisStepStatus,
    SQLiteCollectionStore,
)


def _store_with_research_run(tmp_path: Path) -> tuple[SQLiteCollectionStore, str]:
    database_path = tmp_path / "information-agent.db"
    store = SQLiteCollectionStore(database_path)
    research_run_id = store.start_run("AI 芯片", ["https://example.com/feed.xml"])
    store.complete_run(
        research_run_id,
        CollectionReport("AI 芯片", RunStatus.COMPLETED, []),
        [],
    )
    return store, research_run_id


def test_analysis_storage_migrates_and_run_creation_is_idempotent(tmp_path: Path) -> None:
    store, research_run_id = _store_with_research_run(tmp_path)

    first = store.create_analysis_run(
        research_run_id,
        "opinion_analysis",
        {"window": "2026-08", "target": "AI 芯片"},
        idempotency_key="frontend-request-1",
    )
    second = store.create_analysis_run(
        research_run_id,
        "opinion_analysis",
        {"target": "AI 芯片", "window": "2026-08"},
        idempotency_key="frontend-request-1",
    )

    assert first.id == second.id
    assert first.status is AnalysisRunStatus.CREATED
    assert first.config == {"target": "AI 芯片", "window": "2026-08"}

    with sqlite3.connect(store.database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        migration_version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]

    assert migration_version == 11
    assert {
        "analysis_runs",
        "analysis_steps",
        "analysis_attempts",
        "analysis_artifacts",
    } <= tables


def test_analysis_storage_migration_does_not_reuse_historical_version_four(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "historical.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE research_runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO research_runs (id, status) VALUES ('existing', 'completed')"
        )
        connection.executemany(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            [(version, "2026-08-03T23:00:00+08:00") for version in range(1, 5)],
        )

    store = SQLiteCollectionStore(database_path)
    store.create_analysis_run("existing", "opinion_analysis", {})

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        migration_version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]

    assert migration_version == 5
    assert {
        "analysis_runs",
        "analysis_steps",
        "analysis_attempts",
        "analysis_artifacts",
    } <= tables


def test_opinion_schema_migrates_and_reads_legacy_run(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-opinion.db"
    result_payload = {
        "article_id": "article-1",
        "source_url": "https://www.bilibili.com/video/BV1xx",
        "status": "completed",
        "requested_limit": 10,
        "analyzed_count": 1,
        "controversy_points": [],
        "comments": [
            {
                "comment_id": "reply-1",
                "source_url": "https://www.bilibili.com/video/BV1xx#reply-1",
                "author": "用户甲",
                "content": "评论内容。",
                "likes": 1,
                "published_at": None,
            }
        ],
        "classifications": [
            {
                "run_id": "run-1",
                "evidence_id": 1,
                "comment_id": "reply-1",
                "classification_status": "classified",
                "stance": "support",
                "error_code": None,
            }
        ],
        "points": [],
        "uncertainties": [],
        "errors": [],
        "attempts": [],
    }
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE articles (
                id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE TABLE opinion_runs (
                id TEXT PRIMARY KEY,
                article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                platform TEXT NOT NULL,
                window_hours INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                errors_json TEXT NOT NULL,
                result_json TEXT
            );
            CREATE TABLE opinion_comments (
                run_id TEXT NOT NULL,
                comment_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                author TEXT NOT NULL,
                content TEXT NOT NULL,
                likes INTEGER NOT NULL,
                published_at TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, comment_id)
            );
            """
        )
        connection.executemany(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            [(version, "2026-08-17T10:00:00+08:00") for version in range(1, 10)],
        )
        connection.execute(
            "INSERT INTO articles (id, source_url, created_at) VALUES (?, ?, ?)",
            ("article-1", result_payload["source_url"], "2026-08-17T10:00:00+08:00"),
        )
        connection.execute(
            """
            INSERT INTO opinion_runs (
                id, article_id, platform, window_hours, status, created_at,
                started_at, finished_at, errors_json, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                "article-1",
                "bilibili",
                72,
                "completed",
                "2026-08-17T10:00:00+08:00",
                "2026-08-17T10:00:00+08:00",
                "2026-08-17T10:01:00+08:00",
                "[]",
                json.dumps(result_payload, ensure_ascii=False),
            ),
        )

    store = SQLiteCollectionStore(database_path)
    record = store.get_latest_opinion_run("article-1")

    assert record is not None
    assert record.article_snapshot_id is None
    assert record.requested_limit == 10
    assert record.collected_count == 1
    assert record.classification_total == 1
    assert store.load_opinion_classifications("run-1")[0]["stance"] == "support"


def test_analysis_state_persists_steps_attempts_and_immutable_artifacts(tmp_path: Path) -> None:
    store, research_run_id = _store_with_research_run(tmp_path)
    run = store.create_analysis_run(research_run_id, "opinion_analysis", {})
    step = store.create_analysis_step(run.id, 1, "value_assessment")
    attempt = store.create_analysis_attempt(
        step.id,
        "llm_value_assessment",
        "request-hash-1",
        "attempt-1",
    )

    request_artifact = store.record_analysis_artifact(
        run.id,
        "request",
        {"topic": "AI 芯片"},
        artifact_key="value_assessment:attempt-1:request",
        step_id=step.id,
        attempt_id=attempt.id,
    )
    duplicate_artifact = store.record_analysis_artifact(
        run.id,
        "request",
        {"topic": "AI 芯片"},
        artifact_key="value_assessment:attempt-1:request",
        step_id=step.id,
        attempt_id=attempt.id,
    )
    response_artifact = store.record_analysis_artifact(
        run.id,
        "parsed_result",
        {"decision": "worthwhile"},
        artifact_key="value_assessment:attempt-1:parsed_result",
        step_id=step.id,
        attempt_id=attempt.id,
    )

    assert attempt.status is AnalysisAttemptStatus.STARTED
    assert duplicate_artifact.id == request_artifact.id
    assert request_artifact.payload == {"topic": "AI 芯片"}
    assert response_artifact.payload == {"decision": "worthwhile"}

    finished_attempt = store.set_analysis_attempt_status(
        attempt.id,
        AnalysisAttemptStatus.SUCCEEDED,
    )
    attempt_state = store.load_analysis_state(run.id)
    assert attempt_state.run.updated_at == finished_attempt.finished_at
    assert attempt_state.steps[0].updated_at == finished_attempt.finished_at
    finished_step = store.set_analysis_step_status(
        step.id,
        AnalysisStepStatus.SUCCEEDED,
    )
    finished_run = store.set_analysis_run_status(run.id, AnalysisRunStatus.COMPLETED)
    state = store.load_analysis_state(run.id)

    assert finished_attempt.status is AnalysisAttemptStatus.SUCCEEDED
    assert finished_step.status is AnalysisStepStatus.SUCCEEDED
    assert finished_run.status is AnalysisRunStatus.COMPLETED
    assert state.run.status is AnalysisRunStatus.COMPLETED
    assert [item.step_key for item in state.steps] == ["value_assessment"]
    assert [item.status for item in state.attempts] == [AnalysisAttemptStatus.SUCCEEDED]
    assert {item.kind for item in state.artifacts} == {"request", "parsed_result"}


def test_interruption_marks_active_work_and_allows_a_new_attempt(tmp_path: Path) -> None:
    store, research_run_id = _store_with_research_run(tmp_path)
    run = store.create_analysis_run(research_run_id, "opinion_analysis", {})
    step = store.create_analysis_step(run.id, 1, "collect_sources")
    attempt = store.create_analysis_attempt(step.id, "search", "hash-1", "query-1")

    interrupted = store.interrupt_analysis_run(run.id, "worker 与前端断开")

    assert interrupted.run.status is AnalysisRunStatus.INTERRUPTED
    assert interrupted.steps[0].status is AnalysisStepStatus.INTERRUPTED
    assert interrupted.attempts[0].status is AnalysisAttemptStatus.INTERRUPTED
    assert interrupted.run.errors == ({"message": "worker 与前端断开", "type": "InterruptedError"},)

    existing = store.create_analysis_attempt(step.id, "search", "hash-1", "query-1")
    retry = store.create_analysis_attempt(step.id, "search", "hash-1", "query-2")

    assert existing.id == attempt.id
    assert existing.status is AnalysisAttemptStatus.INTERRUPTED
    assert retry.attempt_no == 2
    assert store.load_analysis_run(run.id).status is AnalysisRunStatus.RUNNING


def test_analysis_storage_rejects_invalid_parent_and_cross_run_artifact_links(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "information-agent.db"
    store = SQLiteCollectionStore(database_path)
    collecting_run_id = store.start_run("AI 芯片", ["feed"])

    with pytest.raises(ValueError, match="尚未产生可分析结果"):
        store.create_analysis_run(collecting_run_id, "opinion_analysis", {})

    store.complete_run(
        collecting_run_id,
        CollectionReport("AI 芯片", RunStatus.COMPLETED, []),
        [],
    )
    other_research_run_id = store.start_run("机器人", ["feed"])
    store.complete_run(
        other_research_run_id,
        CollectionReport("机器人", RunStatus.COMPLETED, []),
        [],
    )
    first_run = store.create_analysis_run(collecting_run_id, "opinion_analysis", {})
    other_run = store.create_analysis_run(other_research_run_id, "opinion_analysis", {})
    other_step = store.create_analysis_step(other_run.id, 1, "value_assessment")

    with pytest.raises(ValueError, match="不属于当前分析运行"):
        store.record_analysis_artifact(
            first_run.id,
            "result",
            {"ok": True},
            artifact_key="value_assessment:attempt-1:result",
            step_id=other_step.id,
        )


def test_completed_analysis_attempt_and_step_are_immutable(tmp_path: Path) -> None:
    store, research_run_id = _store_with_research_run(tmp_path)
    run = store.create_analysis_run(research_run_id, "opinion_analysis", {})
    step = store.create_analysis_step(run.id, 1, "value_assessment")
    attempt = store.create_analysis_attempt(step.id, "llm", "hash-1", "attempt-1")
    store.set_analysis_attempt_status(attempt.id, AnalysisAttemptStatus.SUCCEEDED)
    store.set_analysis_step_status(step.id, AnalysisStepStatus.SUCCEEDED)

    with pytest.raises(ValueError, match="已结束"):
        store.set_analysis_attempt_status(attempt.id, AnalysisAttemptStatus.FAILED)
    with pytest.raises(ValueError, match="已完成"):
        store.set_analysis_step_status(step.id, AnalysisStepStatus.RUNNING)


def test_artifacts_keep_same_payload_from_different_attempts_separate(
    tmp_path: Path,
) -> None:
    store, research_run_id = _store_with_research_run(tmp_path)
    run = store.create_analysis_run(research_run_id, "opinion_analysis", {})
    step = store.create_analysis_step(run.id, 1, "collect_sources")
    first_attempt = store.create_analysis_attempt(step.id, "search", "hash-1", "query-1")
    store.record_analysis_artifact(
        run.id,
        "search_request",
        {"query": "AI 芯片"},
        artifact_key="collect_sources:attempt-1:request",
        step_id=step.id,
        attempt_id=first_attempt.id,
    )
    store.set_analysis_attempt_status(first_attempt.id, AnalysisAttemptStatus.INTERRUPTED)
    store.set_analysis_step_status(step.id, AnalysisStepStatus.INTERRUPTED)

    second_attempt = store.create_analysis_attempt(step.id, "search", "hash-1", "query-2")
    second_artifact = store.record_analysis_artifact(
        run.id,
        "search_request",
        {"query": "AI 芯片"},
        artifact_key="collect_sources:attempt-2:request",
        step_id=step.id,
        attempt_id=second_attempt.id,
    )
    state = store.load_analysis_state(run.id)

    assert second_artifact.attempt_id == second_attempt.id
    assert len(state.artifacts) == 2
    assert {artifact.attempt_id for artifact in state.artifacts} == {
        first_attempt.id,
        second_attempt.id,
    }


def test_artifact_key_cannot_be_overwritten(tmp_path: Path) -> None:
    store, research_run_id = _store_with_research_run(tmp_path)
    run = store.create_analysis_run(research_run_id, "opinion_analysis", {})
    artifact = store.record_analysis_artifact(
        run.id,
        "result",
        {"decision": "worthwhile"},
        artifact_key="final:result",
    )

    with pytest.raises(ValueError, match="已经对应其他内容"):
        store.record_analysis_artifact(
            run.id,
            "result",
            {"decision": "not_worthwhile"},
            artifact_key="final:result",
        )

    assert store.load_analysis_state(run.id).artifacts == (artifact,)
