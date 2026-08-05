from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any
from uuid import uuid4

from ..contracts import project_now
from .common import (
    _canonical_json,
    _error_json,
    _error_object,
    _format_datetime,
    _load_json_list,
    _load_json_object,
    _optional_text,
)
from .models import (
    AnalysisArtifact,
    AnalysisAttempt,
    AnalysisAttemptStatus,
    AnalysisRun,
    AnalysisRunStatus,
    AnalysisState,
    AnalysisStep,
    AnalysisStepStatus,
)


class AnalysisPersistenceMixin:
    """Persist a resumable analysis lifecycle in SQLite."""

    def create_analysis_run(
        self,
        research_run_id: str,
        analysis_type: str,
        config: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> AnalysisRun:
        normalized_type = analysis_type.strip()
        if not normalized_type:
            raise ValueError("分析类型不能为空")
        normalized_key = idempotency_key.strip() if idempotency_key is not None else None
        normalized_key = normalized_key or None
        config_json = _canonical_json(config)
        now = _format_datetime(project_now())
        with self._connect() as connection:
            research_run = connection.execute(
                "SELECT status FROM research_runs WHERE id = ?",
                (research_run_id,),
            ).fetchone()
            if research_run is None:
                raise ValueError(f"不存在的研究运行：{research_run_id}")
            if research_run["status"] not in {"completed", "partial"}:
                raise ValueError(f"研究运行尚未产生可分析结果：{research_run_id}")

            if normalized_key is not None:
                existing = connection.execute(
                    "SELECT * FROM analysis_runs WHERE idempotency_key = ?",
                    (normalized_key,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["research_run_id"] != research_run_id
                        or existing["analysis_type"] != normalized_type
                        or existing["config_json"] != config_json
                    ):
                        raise ValueError("分析幂等键已经对应其他任务")
                    return _analysis_run_from_row(existing)

            analysis_run_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO analysis_runs (
                    id, research_run_id, analysis_type, status, current_step_key,
                    config_json, idempotency_key, created_at, updated_at,
                    started_at, finished_at, errors_json
                ) VALUES (?, ?, ?, 'created', NULL, ?, ?, ?, ?, NULL, NULL, '[]')
                """,
                (
                    analysis_run_id,
                    research_run_id,
                    normalized_type,
                    config_json,
                    normalized_key,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM analysis_runs WHERE id = ?", (analysis_run_id,)
            ).fetchone()
        assert row is not None
        return _analysis_run_from_row(row)

    def load_analysis_run(self, analysis_run_id: str) -> AnalysisRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_runs WHERE id = ?", (analysis_run_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f"不存在的分析运行：{analysis_run_id}")
        return _analysis_run_from_row(row)

    def load_analysis_state(self, analysis_run_id: str) -> AnalysisState:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM analysis_runs WHERE id = ?", (analysis_run_id,)
            ).fetchone()
            if run is None:
                raise ValueError(f"不存在的分析运行：{analysis_run_id}")
            steps = connection.execute(
                """
                SELECT * FROM analysis_steps
                WHERE analysis_run_id = ?
                ORDER BY position
                """,
                (analysis_run_id,),
            ).fetchall()
            attempts = connection.execute(
                """
                SELECT attempts.*
                FROM analysis_attempts AS attempts
                JOIN analysis_steps AS steps ON steps.id = attempts.analysis_step_id
                WHERE steps.analysis_run_id = ?
                ORDER BY steps.position, attempts.attempt_no
                """,
                (analysis_run_id,),
            ).fetchall()
            artifacts = connection.execute(
                """
                SELECT * FROM analysis_artifacts
                WHERE analysis_run_id = ?
                ORDER BY created_at, id
                """,
                (analysis_run_id,),
            ).fetchall()
        return AnalysisState(
            run=_analysis_run_from_row(run),
            steps=tuple(_analysis_step_from_row(row) for row in steps),
            attempts=tuple(_analysis_attempt_from_row(row) for row in attempts),
            artifacts=tuple(_analysis_artifact_from_row(row) for row in artifacts),
        )

    def create_analysis_step(
        self,
        analysis_run_id: str,
        position: int,
        step_key: str,
    ) -> AnalysisStep:
        normalized_key = step_key.strip()
        if position <= 0:
            raise ValueError("分析步骤位置必须大于 0")
        if not normalized_key:
            raise ValueError("分析步骤标识不能为空")
        now = _format_datetime(project_now())
        with self._connect() as connection:
            self._require_analysis_run(connection, analysis_run_id)
            existing = connection.execute(
                """
                SELECT * FROM analysis_steps
                WHERE analysis_run_id = ? AND step_key = ?
                """,
                (analysis_run_id, normalized_key),
            ).fetchone()
            if existing is not None:
                if int(existing["position"]) != position:
                    raise ValueError("分析步骤标识已经对应其他位置")
                return _analysis_step_from_row(existing)

            position_owner = connection.execute(
                """
                SELECT step_key FROM analysis_steps
                WHERE analysis_run_id = ? AND position = ?
                """,
                (analysis_run_id, position),
            ).fetchone()
            if position_owner is not None:
                raise ValueError("分析步骤位置已经被占用")

            step_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO analysis_steps (
                    id, analysis_run_id, position, step_key, status,
                    created_at, updated_at, started_at, finished_at, error_json
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, NULL, NULL, NULL)
                """,
                (step_id, analysis_run_id, position, normalized_key, now, now),
            )
            row = connection.execute(
                "SELECT * FROM analysis_steps WHERE id = ?", (step_id,)
            ).fetchone()
        assert row is not None
        return _analysis_step_from_row(row)

    def set_analysis_step_status(
        self,
        step_id: str,
        status: AnalysisStepStatus,
        *,
        error: Exception | dict[str, Any] | None = None,
    ) -> AnalysisStep:
        now = _format_datetime(project_now())
        status = AnalysisStepStatus(status)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_steps WHERE id = ?", (step_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"不存在的分析步骤：{step_id}")
            current = AnalysisStepStatus(row["status"])
            if current == AnalysisStepStatus.SUCCEEDED and status != current:
                raise ValueError("已完成的分析步骤不能再次改变状态")
            if (
                current
                in {
                    AnalysisStepStatus.SKIPPED,
                    AnalysisStepStatus.CANCELLED,
                }
                and status != current
            ):
                raise ValueError("已终止的分析步骤不能再次改变状态")
            if status == AnalysisStepStatus.RUNNING and current not in {
                AnalysisStepStatus.PENDING,
                AnalysisStepStatus.RUNNING,
                AnalysisStepStatus.FAILED,
                AnalysisStepStatus.INTERRUPTED,
            }:
                raise ValueError(f"分析步骤不能从 {current.value} 进入 running")
            error_json = _error_json(error)
            started_at = (
                row["started_at"] or now
                if status == AnalysisStepStatus.RUNNING
                else row["started_at"]
            )
            finished_at = (
                now
                if status
                in {
                    AnalysisStepStatus.SUCCEEDED,
                    AnalysisStepStatus.FAILED,
                    AnalysisStepStatus.INTERRUPTED,
                    AnalysisStepStatus.SKIPPED,
                    AnalysisStepStatus.CANCELLED,
                }
                else None
            )
            connection.execute(
                """
                UPDATE analysis_steps
                SET status = ?, updated_at = ?, started_at = ?, finished_at = ?, error_json = ?
                WHERE id = ?
                """,
                (status.value, now, started_at, finished_at, error_json, step_id),
            )
            if status == AnalysisStepStatus.RUNNING:
                connection.execute(
                    """
                    UPDATE analysis_runs
                    SET status = 'running', current_step_key = ?, updated_at = ?,
                        started_at = COALESCE(started_at, ?)
                    WHERE id = ?
                    """,
                    (row["step_key"], now, now, row["analysis_run_id"]),
                )
            elif status == AnalysisStepStatus.INTERRUPTED:
                connection.execute(
                    """
                    UPDATE analysis_runs
                    SET status = 'interrupted', current_step_key = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (row["step_key"], now, row["analysis_run_id"]),
                )
            else:
                connection.execute(
                    """
                    UPDATE analysis_runs
                    SET current_step_key = CASE
                            WHEN current_step_key = ? THEN NULL
                            ELSE current_step_key
                        END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (row["step_key"], now, row["analysis_run_id"]),
                )
            updated = connection.execute(
                "SELECT * FROM analysis_steps WHERE id = ?", (step_id,)
            ).fetchone()
        assert updated is not None
        return _analysis_step_from_row(updated)

    def create_analysis_attempt(
        self,
        step_id: str,
        operation: str,
        request_hash: str,
        idempotency_key: str,
    ) -> AnalysisAttempt:
        normalized_operation = operation.strip()
        normalized_hash = request_hash.strip()
        normalized_key = idempotency_key.strip()
        if not normalized_operation:
            raise ValueError("分析尝试操作名不能为空")
        if not normalized_hash:
            raise ValueError("分析尝试请求哈希不能为空")
        if not normalized_key:
            raise ValueError("分析尝试幂等键不能为空")
        now = _format_datetime(project_now())
        with self._connect() as connection:
            step = connection.execute(
                "SELECT * FROM analysis_steps WHERE id = ?", (step_id,)
            ).fetchone()
            if step is None:
                raise ValueError(f"不存在的分析步骤：{step_id}")
            if step["status"] in {
                AnalysisStepStatus.SUCCEEDED.value,
                AnalysisStepStatus.SKIPPED.value,
                AnalysisStepStatus.CANCELLED.value,
            }:
                raise ValueError("已终止的分析步骤不能创建新尝试")
            existing = connection.execute(
                """
                SELECT * FROM analysis_attempts
                WHERE analysis_step_id = ? AND idempotency_key = ?
                """,
                (step_id, normalized_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["operation"] != normalized_operation
                    or existing["request_hash"] != normalized_hash
                ):
                    raise ValueError("分析尝试幂等键已经对应其他请求")
                return _analysis_attempt_from_row(existing)

            attempt_no = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(attempt_no), 0) + 1
                    FROM analysis_attempts WHERE analysis_step_id = ?
                    """,
                    (step_id,),
                ).fetchone()[0]
            )
            attempt_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO analysis_attempts (
                    id, analysis_step_id, attempt_no, operation, idempotency_key,
                    request_hash, status, started_at, finished_at, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, 'started', ?, NULL, NULL)
                """,
                (
                    attempt_id,
                    step_id,
                    attempt_no,
                    normalized_operation,
                    normalized_key,
                    normalized_hash,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE analysis_steps
                SET status = 'running', updated_at = ?, started_at = COALESCE(started_at, ?),
                    finished_at = NULL, error_json = NULL
                WHERE id = ?
                """,
                (now, now, step_id),
            )
            connection.execute(
                """
                UPDATE analysis_runs
                SET status = 'running', current_step_key = ?, updated_at = ?,
                    started_at = COALESCE(started_at, ?)
                WHERE id = ?
                """,
                (step["step_key"], now, now, step["analysis_run_id"]),
            )
            row = connection.execute(
                "SELECT * FROM analysis_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        assert row is not None
        return _analysis_attempt_from_row(row)

    def set_analysis_attempt_status(
        self,
        attempt_id: str,
        status: AnalysisAttemptStatus,
        *,
        error: Exception | dict[str, Any] | None = None,
    ) -> AnalysisAttempt:
        status = AnalysisAttemptStatus(status)
        if status == AnalysisAttemptStatus.STARTED:
            raise ValueError("分析尝试不能从外部重新设置为 started")
        now = _format_datetime(project_now())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"不存在的分析尝试：{attempt_id}")
            current = AnalysisAttemptStatus(row["status"])
            if current != AnalysisAttemptStatus.STARTED:
                if current == status:
                    return _analysis_attempt_from_row(row)
                raise ValueError("已结束的分析尝试不能再次改变状态")
            connection.execute(
                """
                UPDATE analysis_attempts
                SET status = ?, finished_at = ?, error_json = ?
                WHERE id = ? AND status = 'started'
                """,
                (status.value, now, _error_json(error), attempt_id),
            )
            if status == AnalysisAttemptStatus.INTERRUPTED:
                connection.execute(
                    """
                    UPDATE analysis_steps
                    SET status = 'interrupted', updated_at = ?, error_json = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (now, _error_json(error), row["analysis_step_id"]),
                )
                connection.execute(
                    """
                    UPDATE analysis_runs
                    SET status = 'interrupted', updated_at = ?
                    WHERE id = (
                        SELECT analysis_run_id FROM analysis_steps WHERE id = ?
                    )
                    """,
                    (now, row["analysis_step_id"]),
                )
            else:
                connection.execute(
                    """
                    UPDATE analysis_steps
                    SET updated_at = ?
                    WHERE id = ?
                    """,
                    (now, row["analysis_step_id"]),
                )
                connection.execute(
                    """
                    UPDATE analysis_runs
                    SET updated_at = ?
                    WHERE id = (
                        SELECT analysis_run_id FROM analysis_steps WHERE id = ?
                    )
                    """,
                    (now, row["analysis_step_id"]),
                )
            updated = connection.execute(
                "SELECT * FROM analysis_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        assert updated is not None
        return _analysis_attempt_from_row(updated)

    def record_analysis_artifact(
        self,
        analysis_run_id: str,
        kind: str,
        payload: Any,
        *,
        artifact_key: str,
        content_type: str = "application/json",
        metadata: dict[str, Any] | None = None,
        step_id: str | None = None,
        attempt_id: str | None = None,
    ) -> AnalysisArtifact:
        normalized_kind = kind.strip()
        normalized_key = artifact_key.strip()
        normalized_content_type = content_type.strip()
        if not normalized_key:
            raise ValueError("分析 artifact 标识不能为空")
        if not normalized_kind:
            raise ValueError("分析 artifact 类型不能为空")
        if not normalized_content_type:
            raise ValueError("分析 artifact 内容类型不能为空")
        payload_json = _canonical_json(payload)
        metadata_json = _canonical_json(metadata or {})
        content_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        now = _format_datetime(project_now())
        with self._connect() as connection:
            self._require_analysis_run(connection, analysis_run_id)
            if step_id is not None:
                step = connection.execute(
                    "SELECT analysis_run_id FROM analysis_steps WHERE id = ?", (step_id,)
                ).fetchone()
                if step is None or step["analysis_run_id"] != analysis_run_id:
                    raise ValueError("artifact 引用了不属于当前分析运行的步骤")
            if attempt_id is not None:
                attempt = connection.execute(
                    """
                    SELECT steps.analysis_run_id, attempts.analysis_step_id
                    FROM analysis_attempts AS attempts
                    JOIN analysis_steps AS steps ON steps.id = attempts.analysis_step_id
                    WHERE attempts.id = ?
                    """,
                    (attempt_id,),
                ).fetchone()
                if attempt is None or attempt["analysis_run_id"] != analysis_run_id:
                    raise ValueError("artifact 引用了不属于当前分析运行的尝试")
                if step_id is not None and attempt["analysis_step_id"] != step_id:
                    raise ValueError("artifact 的步骤和尝试不匹配")

            existing = connection.execute(
                """
                SELECT * FROM analysis_artifacts
                WHERE analysis_run_id = ? AND artifact_key = ?
                """,
                (analysis_run_id, normalized_key),
            ).fetchone()
            if existing is not None:
                if any(
                    (
                        existing["step_id"] != step_id,
                        existing["attempt_id"] != attempt_id,
                        existing["kind"] != normalized_kind,
                        existing["content_type"] != normalized_content_type,
                        existing["payload_json"] != payload_json,
                        existing["metadata_json"] != metadata_json,
                        existing["content_hash"] != content_hash,
                    )
                ):
                    raise ValueError("分析 artifact 标识已经对应其他内容")
                return _analysis_artifact_from_row(existing)

            artifact_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO analysis_artifacts (
                    id, analysis_run_id, artifact_key, step_id, attempt_id, kind,
                    content_type, payload_json, metadata_json, content_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    analysis_run_id,
                    normalized_key,
                    step_id,
                    attempt_id,
                    normalized_kind,
                    normalized_content_type,
                    payload_json,
                    metadata_json,
                    content_hash,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM analysis_artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        assert row is not None
        return _analysis_artifact_from_row(row)

    def set_analysis_run_status(
        self,
        analysis_run_id: str,
        status: AnalysisRunStatus,
        *,
        current_step_key: str | None = None,
        error: Exception | dict[str, Any] | None = None,
    ) -> AnalysisRun:
        status = AnalysisRunStatus(status)
        now = _format_datetime(project_now())
        terminal = {
            AnalysisRunStatus.COMPLETED,
            AnalysisRunStatus.PARTIAL,
            AnalysisRunStatus.SKIPPED,
            AnalysisRunStatus.FAILED,
            AnalysisRunStatus.CANCELLED,
        }
        with self._connect() as connection:
            row = self._require_analysis_run(connection, analysis_run_id)
            if (
                row["status"]
                in {
                    AnalysisRunStatus.COMPLETED.value,
                    AnalysisRunStatus.SKIPPED.value,
                    AnalysisRunStatus.CANCELLED.value,
                }
                and row["status"] != status.value
            ):
                raise ValueError("已结束的分析运行不能再次改变状态")
            errors = _load_json_list(row["errors_json"])
            if error is not None:
                errors.append(_error_object(error))
            finished_at = now if status in terminal else None
            started_at = (
                row["started_at"] or now
                if status == AnalysisRunStatus.RUNNING
                else row["started_at"]
            )
            connection.execute(
                """
                UPDATE analysis_runs
                SET status = ?, current_step_key = ?, updated_at = ?,
                    started_at = ?, finished_at = ?, errors_json = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    current_step_key,
                    now,
                    started_at,
                    finished_at,
                    json.dumps(errors, ensure_ascii=False, sort_keys=True),
                    analysis_run_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM analysis_runs WHERE id = ?", (analysis_run_id,)
            ).fetchone()
        assert updated is not None
        return _analysis_run_from_row(updated)

    def interrupt_analysis_run(self, analysis_run_id: str, reason: str) -> AnalysisState:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("中断原因不能为空")
        now = _format_datetime(project_now())
        error = {"type": "InterruptedError", "message": normalized_reason}
        with self._connect() as connection:
            run = self._require_analysis_run(connection, analysis_run_id)
            connection.execute(
                """
                UPDATE analysis_attempts
                SET status = 'interrupted', finished_at = ?, error_json = ?
                WHERE analysis_step_id IN (
                    SELECT id FROM analysis_steps
                    WHERE analysis_run_id = ? AND status = 'running'
                ) AND status = 'started'
                """,
                (now, json.dumps(error, ensure_ascii=False, sort_keys=True), analysis_run_id),
            )
            connection.execute(
                """
                UPDATE analysis_steps
                SET status = 'interrupted', updated_at = ?, error_json = ?
                WHERE analysis_run_id = ? AND status = 'running'
                """,
                (now, json.dumps(error, ensure_ascii=False, sort_keys=True), analysis_run_id),
            )
            errors = _load_json_list(run["errors_json"])
            errors.append(error)
            connection.execute(
                """
                UPDATE analysis_runs
                SET status = 'interrupted', current_step_key = current_step_key,
                    updated_at = ?, errors_json = ?
                WHERE id = ?
                """,
                (now, json.dumps(errors, ensure_ascii=False, sort_keys=True), analysis_run_id),
            )
        return self.load_analysis_state(analysis_run_id)

    @staticmethod
    def _require_analysis_run(
        connection: sqlite3.Connection,
        analysis_run_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM analysis_runs WHERE id = ?", (analysis_run_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"不存在的分析运行：{analysis_run_id}")
        return row


def _analysis_run_from_row(row: sqlite3.Row) -> AnalysisRun:
    return AnalysisRun(
        id=str(row["id"]),
        research_run_id=str(row["research_run_id"]),
        analysis_type=str(row["analysis_type"]),
        status=AnalysisRunStatus(str(row["status"])),
        current_step_key=_optional_text(row["current_step_key"]),
        config=_load_json_object(row["config_json"], "config_json"),
        idempotency_key=_optional_text(row["idempotency_key"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=_optional_text(row["started_at"]),
        finished_at=_optional_text(row["finished_at"]),
        errors=tuple(_load_json_list(row["errors_json"])),
    )


def _analysis_step_from_row(row: sqlite3.Row) -> AnalysisStep:
    return AnalysisStep(
        id=str(row["id"]),
        analysis_run_id=str(row["analysis_run_id"]),
        position=int(row["position"]),
        step_key=str(row["step_key"]),
        status=AnalysisStepStatus(str(row["status"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=_optional_text(row["started_at"]),
        finished_at=_optional_text(row["finished_at"]),
        error=(
            _load_json_object(row["error_json"], "error_json")
            if row["error_json"] is not None
            else None
        ),
    )


def _analysis_attempt_from_row(row: sqlite3.Row) -> AnalysisAttempt:
    return AnalysisAttempt(
        id=str(row["id"]),
        analysis_step_id=str(row["analysis_step_id"]),
        attempt_no=int(row["attempt_no"]),
        operation=str(row["operation"]),
        idempotency_key=str(row["idempotency_key"]),
        request_hash=str(row["request_hash"]),
        status=AnalysisAttemptStatus(str(row["status"])),
        started_at=str(row["started_at"]),
        finished_at=_optional_text(row["finished_at"]),
        error=(
            _load_json_object(row["error_json"], "error_json")
            if row["error_json"] is not None
            else None
        ),
    )


def _analysis_artifact_from_row(row: sqlite3.Row) -> AnalysisArtifact:
    return AnalysisArtifact(
        id=str(row["id"]),
        analysis_run_id=str(row["analysis_run_id"]),
        artifact_key=str(row["artifact_key"]),
        step_id=_optional_text(row["step_id"]),
        attempt_id=_optional_text(row["attempt_id"]),
        kind=str(row["kind"]),
        content_type=str(row["content_type"]),
        payload=json.loads(row["payload_json"]),
        metadata=_load_json_object(row["metadata_json"], "metadata_json"),
        content_hash=str(row["content_hash"]),
        created_at=str(row["created_at"]),
    )
