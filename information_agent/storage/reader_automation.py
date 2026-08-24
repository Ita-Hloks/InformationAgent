from __future__ import annotations

import json
import math
import sqlite3
from typing import Any
from uuid import uuid4

from ..contracts import project_now
from .common import _format_datetime, _load_json_object, _optional_text
from .models import (
    ArticleResearchRun,
    ArticleSummaryJob,
    ReaderArticle,
    ReaderAutomationSettings,
)

_RESEARCH_MODES = {"auto", "manual"}
_RESEARCH_STATUSES = {
    "queued",
    "running",
    "completed",
    "partial",
    "failed",
    "cancelled",
}


class ReaderAutomationPersistenceMixin:
    """持久化文章摘要、阅读参数和阅读触发研究。"""

    def ensure_pending_summaries(self) -> int:
        now = _format_datetime(project_now())
        with self._connect() as connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO article_summaries (
                    snapshot_id, content_hash, status, summary, error_json,
                    attempts, created_at, updated_at, finished_at
                )
                SELECT id, content_hash, 'pending', NULL, NULL, 0, ?, ?, NULL
                FROM article_snapshots
                """,
                (now, now),
            )
            return connection.total_changes - before

    def claim_summary_job(
        self,
        *,
        preferred_snapshot_id: str | None = None,
    ) -> ArticleSummaryJob | None:
        now = _format_datetime(project_now())
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT summaries.snapshot_id, snapshots.article_id, snapshots.content_hash,
                       snapshots.payload_json, summaries.status, summaries.attempts
                FROM article_summaries AS summaries
                JOIN article_snapshots AS snapshots ON snapshots.id = summaries.snapshot_id
                WHERE summaries.status = 'pending'
                ORDER BY CASE WHEN summaries.snapshot_id = ? THEN 0 ELSE 1 END,
                         snapshots.collected_at DESC, snapshots.created_at DESC
                LIMIT 1
                """,
                (preferred_snapshot_id,),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE article_summaries
                SET status = 'running', error_json = NULL, attempts = attempts + 1,
                    updated_at = ?, finished_at = NULL
                WHERE snapshot_id = ? AND status = 'pending'
                """,
                (now, row["snapshot_id"]),
            )
            if updated.rowcount != 1:
                return None
            payload = json.loads(row["payload_json"])
            return ArticleSummaryJob(
                snapshot_id=str(row["snapshot_id"]),
                article_id=str(row["article_id"]),
                title=str(payload.get("title") or "未命名文章"),
                content=str(payload.get("content") or ""),
                content_hash=str(row["content_hash"]),
                status="running",
                attempts=int(row["attempts"]) + 1,
            )

    def complete_summary_job(self, snapshot_id: str, summary: str) -> None:
        normalized = " ".join(summary.split()).strip()
        if not normalized or len(normalized) > 180:
            raise ValueError("摘要必须是最多 180 字的非空文本")
        now = _format_datetime(project_now())
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE article_summaries
                SET status = 'completed', summary = ?, error_json = NULL,
                    updated_at = ?, finished_at = ?
                WHERE snapshot_id = ? AND status = 'running'
                """,
                (normalized, now, now, snapshot_id),
            )
            if updated.rowcount != 1:
                raise ValueError("摘要任务不存在或不在运行中")

    def fail_summary_job(self, snapshot_id: str, error: Exception) -> None:
        now = _format_datetime(project_now())
        error_json = json.dumps(
            {"type": type(error).__name__, "message": str(error)},
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE article_summaries
                SET status = 'failed', summary = NULL, error_json = ?,
                    updated_at = ?, finished_at = ?
                WHERE snapshot_id = ? AND status = 'running'
                """,
                (error_json, now, now, snapshot_id),
            )
            if updated.rowcount != 1:
                raise ValueError("摘要任务不存在或不在运行中")

    def retry_summary(self, article_id: str) -> str:
        now = _format_datetime(project_now())
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM article_snapshots
                WHERE article_id = ?
                ORDER BY collected_at DESC, created_at DESC
                LIMIT 1
                """,
                (article_id,),
            ).fetchone()
            if row is None:
                raise KeyError(article_id)
            connection.execute(
                """
                UPDATE article_summaries
                SET status = 'pending', summary = NULL, error_json = NULL,
                    updated_at = ?, finished_at = NULL
                WHERE snapshot_id = ? AND status = 'failed'
                """,
                (now, row["id"]),
            )
            return str(row["id"])

    def get_reader_automation_settings(self) -> ReaderAutomationSettings:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM reader_automation_settings WHERE id = 1"
            ).fetchone()
        assert row is not None
        return _settings_from_row(row)

    def update_reader_automation_settings(
        self,
        *,
        enabled: bool,
        dwell_seconds: int,
        read_ratio: float,
        agent_timeout_seconds: int,
        max_searches: int,
        max_attempts: int,
    ) -> ReaderAutomationSettings:
        if type(enabled) is not bool:
            raise ValueError("enabled 必须是布尔值")
        if type(dwell_seconds) is not int or not 1 <= dwell_seconds <= 3600:
            raise ValueError("停留时间必须在 1 至 3600 秒之间")
        if not isinstance(read_ratio, (int, float)) or not math.isfinite(read_ratio):
            raise ValueError("阅读比例必须是有限数值")
        if not 0 < float(read_ratio) <= 1:
            raise ValueError("阅读比例必须大于 0 且不超过 1")
        if type(agent_timeout_seconds) is not int or not 1 <= agent_timeout_seconds <= 600:
            raise ValueError("Agent 超时必须在 1 至 600 秒之间")
        if type(max_searches) is not int or not 1 <= max_searches <= 3:
            raise ValueError("搜索次数必须在 1 至 3 次之间")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise ValueError("重试次数必须在 1 至 3 次之间")
        now = _format_datetime(project_now())
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE reader_automation_settings
                SET enabled = ?, dwell_seconds = ?, read_ratio = ?,
                    agent_timeout_seconds = ?, max_searches = ?, max_attempts = ?,
                    updated_at = ?
                WHERE id = 1
                """,
                (
                    int(enabled),
                    dwell_seconds,
                    float(read_ratio),
                    agent_timeout_seconds,
                    max_searches,
                    max_attempts,
                    now,
                ),
            )
        return self.get_reader_automation_settings()

    def create_article_research_run(
        self,
        article: ReaderArticle,
        *,
        mode: str,
        config: dict[str, Any],
        request_id: str | None = None,
    ) -> ArticleResearchRun:
        normalized_mode = mode.strip()
        if normalized_mode not in _RESEARCH_MODES:
            raise ValueError("研究触发方式必须是 auto 或 manual")
        if not article.snapshot_id or not article.content_hash:
            raise ValueError("文章缺少正文快照标识")
        topic = f"核验《{article.article.title}》中会显著影响读者判断的事实主张"
        now = _format_datetime(project_now())
        run_id = uuid4().hex
        agent_request_id = request_id.strip() if request_id else uuid4().hex
        config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            existing_request = connection.execute(
                "SELECT * FROM article_research_runs WHERE agent_request_id = ?",
                (agent_request_id,),
            ).fetchone()
            if existing_request is not None:
                if (
                    existing_request["article_id"] != article.article.article_id
                    or existing_request["snapshot_id"] != article.snapshot_id
                    or existing_request["mode"] != normalized_mode
                    or existing_request["config_json"] != config_json
                ):
                    raise ValueError("研究请求标识已经对应其他任务")
                return _research_run_from_row(existing_request)
            if normalized_mode == "auto":
                existing = connection.execute(
                    """
                    SELECT * FROM article_research_runs
                    WHERE article_id = ? AND snapshot_id = ? AND mode = 'auto'
                    ORDER BY created_at DESC, rowid DESC LIMIT 1
                    """,
                    (article.article.article_id, article.snapshot_id),
                ).fetchone()
                if existing is not None:
                    return _research_run_from_row(existing)

            connection.execute(
                """
                INSERT INTO research_runs (
                    id, topic, feeds_json, status, created_at, finished_at, errors_json
                ) VALUES (?, ?, ?, 'completed', ?, ?, '[]')
                """,
                (
                    run_id,
                    topic,
                    json.dumps([article.article.feed_url] if article.article.feed_url else []),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO run_evidence (run_id, snapshot_id, evidence_no, selected)
                VALUES (?, ?, 1, 1)
                """,
                (run_id, article.snapshot_id),
            )
            connection.execute(
                """
                INSERT INTO article_research_runs (
                    id, article_id, snapshot_id, topic, mode, status,
                    created_at, started_at, finished_at, agent_request_id,
                    analysis_run_id, config_json, error_json
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, NULL, NULL, ?, NULL, ?, NULL)
                """,
                (
                    run_id,
                    article.article.article_id,
                    article.snapshot_id,
                    topic,
                    normalized_mode,
                    now,
                    agent_request_id,
                    config_json,
                ),
            )
            row = connection.execute(
                "SELECT * FROM article_research_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row is not None
        return _research_run_from_row(row)

    def list_article_research_runs(
        self,
        article_id: str | None = None,
        *,
        mode: str | None = None,
        limit: int = 100,
    ) -> list[ArticleResearchRun]:
        if mode is not None and mode not in _RESEARCH_MODES:
            raise ValueError("研究触发方式必须是 auto 或 manual")
        if not 1 <= limit <= 200:
            raise ValueError("研究记录数量必须在 1 至 200 之间")
        conditions: list[str] = []
        parameters: list[object] = []
        if article_id is not None:
            conditions.append("article_id = ?")
            parameters.append(article_id)
        if mode is not None:
            conditions.append("mode = ?")
            parameters.append(mode)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM article_research_runs
                {where}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [_research_run_from_row(row) for row in rows]

    def get_article_research_run(self, run_id: str) -> ArticleResearchRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM article_research_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return _research_run_from_row(row) if row is not None else None

    def recover_running_article_research_runs(self) -> int:
        now = _format_datetime(project_now())
        error_json = json.dumps(
            {
                "type": "AgentProcessRestarted",
                "message": "后端重启，上一轮文章研究未能继续运行",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE article_research_runs
                SET status = 'partial', finished_at = ?, error_json = ?
                WHERE status = 'running'
                """,
                (now, error_json),
            )
            return updated.rowcount

    def update_article_research_run(
        self,
        run_id: str,
        *,
        status: str,
        analysis_run_id: str | None = None,
        error: Exception | dict[str, Any] | None = None,
    ) -> ArticleResearchRun:
        if status not in _RESEARCH_STATUSES:
            raise ValueError("不支持的文章研究状态")
        now = _format_datetime(project_now())
        error_json = None
        if error is not None:
            payload = (
                error
                if isinstance(error, dict)
                else {"type": type(error).__name__, "message": str(error)}
            )
            error_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM article_research_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if existing is None:
                raise ValueError("不存在的文章研究运行")
            connection.execute(
                """
                UPDATE article_research_runs
                SET status = ?, started_at = CASE
                        WHEN ? = 'running' THEN COALESCE(started_at, ?) ELSE started_at END,
                    finished_at = CASE
                        WHEN ? IN ('completed', 'partial', 'failed', 'cancelled') THEN ?
                        ELSE NULL END,
                    analysis_run_id = COALESCE(?, analysis_run_id), error_json = ?
                WHERE id = ?
                """,
                (
                    status,
                    status,
                    now,
                    status,
                    now,
                    analysis_run_id,
                    error_json,
                    run_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM article_research_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row is not None
        return _research_run_from_row(row)


def _summary_error_message(value: object) -> str | None:
    if value is None:
        return None
    try:
        payload = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return "摘要生成失败"
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return payload["message"]
    return "摘要生成失败"


def _settings_from_row(row: sqlite3.Row) -> ReaderAutomationSettings:
    return ReaderAutomationSettings(
        enabled=bool(row["enabled"]),
        dwell_seconds=int(row["dwell_seconds"]),
        read_ratio=float(row["read_ratio"]),
        agent_timeout_seconds=int(row["agent_timeout_seconds"]),
        max_searches=int(row["max_searches"]),
        max_attempts=int(row["max_attempts"]),
        updated_at=str(row["updated_at"]),
    )


def _research_run_from_row(row: sqlite3.Row) -> ArticleResearchRun:
    error = (
        _load_json_object(row["error_json"], "article_research_runs.error_json")
        if row["error_json"] is not None
        else None
    )
    return ArticleResearchRun(
        id=str(row["id"]),
        article_id=str(row["article_id"]),
        snapshot_id=str(row["snapshot_id"]),
        topic=str(row["topic"]),
        mode=str(row["mode"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        started_at=_optional_text(row["started_at"]),
        finished_at=_optional_text(row["finished_at"]),
        agent_request_id=str(row["agent_request_id"]),
        analysis_run_id=_optional_text(row["analysis_run_id"]),
        config=_load_json_object(row["config_json"], "article_research_runs.config_json"),
        error=error,
    )
