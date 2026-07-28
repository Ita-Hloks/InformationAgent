from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..contracts import PROJECT_TIMEZONE, CollectionReport, ContentType, project_now
from ..normalization import NormalizedArticle
from ..selection import SelectedEvidence

_SCHEMA_VERSION = 1


def default_database_path() -> Path:
    configured = os.getenv("INFORMATION_AGENT_DB_PATH")
    if configured:
        return Path(configured)
    return Path("data") / "information_agent.db"


class SQLiteCollectionStore:
    """保存粗处理文章快照与运行内证据关系。"""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def start_run(self, topic: str, feeds: list[str]) -> str:
        run_id = uuid4().hex
        created_at = _format_datetime(project_now())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_runs (
                    id, topic, feeds_json, status, created_at, errors_json
                ) VALUES (?, ?, ?, 'collecting', ?, '[]')
                """,
                (run_id, topic, json.dumps(feeds, ensure_ascii=False), created_at),
            )
        return run_id

    def complete_run(
        self,
        run_id: str,
        report: CollectionReport,
        normalized_articles: list[NormalizedArticle],
    ) -> None:
        selected_by_snapshot_key = {_snapshot_key(item.article): item for item in report.articles}
        finished_at = _format_datetime(project_now())
        with self._connect() as connection:
            for article in normalized_articles:
                snapshot_id = self._upsert_snapshot(connection, article)
                selected = selected_by_snapshot_key.get(_snapshot_key(article))
                connection.execute(
                    """
                    INSERT INTO run_evidence (
                        run_id, snapshot_id, evidence_no, relevance_score, selected
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, snapshot_id) DO UPDATE SET
                        evidence_no = excluded.evidence_no,
                        relevance_score = excluded.relevance_score,
                        selected = excluded.selected
                    """,
                    (
                        run_id,
                        snapshot_id,
                        selected.evidence_id if selected else None,
                        selected.relevance_score if selected else None,
                        1 if selected else 0,
                    ),
                )

            updated = connection.execute(
                """
                UPDATE research_runs
                SET status = ?, finished_at = ?, errors_json = ?
                WHERE id = ? AND status = 'collecting'
                """,
                (
                    report.status.value,
                    finished_at,
                    json.dumps(report.errors, ensure_ascii=False),
                    run_id,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError(f"无法完成不存在或已结束的运行：{run_id}")

    def fail_run(self, run_id: str, error: Exception) -> None:
        finished_at = _format_datetime(project_now())
        payload = [{"type": type(error).__name__, "message": str(error)}]
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE research_runs
                SET status = 'failed', finished_at = ?, errors_json = ?
                WHERE id = ? AND status = 'collecting'
                """,
                (finished_at, json.dumps(payload, ensure_ascii=False), run_id),
            )

    def load_selected_evidence(self, run_id: str) -> list[SelectedEvidence]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT snapshots.payload_json, evidence.evidence_no, evidence.relevance_score
                FROM run_evidence AS evidence
                JOIN article_snapshots AS snapshots ON snapshots.id = evidence.snapshot_id
                WHERE evidence.run_id = ? AND evidence.selected = 1
                ORDER BY evidence.evidence_no
                """,
                (run_id,),
            ).fetchall()
        return [
            SelectedEvidence(
                article=_article_from_payload(json.loads(row["payload_json"])),
                evidence_id=int(row["evidence_no"]),
                relevance_score=float(row["relevance_score"]),
            )
            for row in rows
        ]

    def _upsert_snapshot(self, connection: sqlite3.Connection, article: NormalizedArticle) -> str:
        content_hash = _content_hash(article)
        existing = connection.execute(
            """
            SELECT id FROM article_snapshots
            WHERE article_id = ? AND content_hash = ?
            """,
            (article.article_id, content_hash),
        ).fetchone()
        if existing is not None:
            return str(existing["id"])

        created_at = _format_datetime(project_now())
        connection.execute(
            """
            INSERT INTO articles (id, source_url, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (article.article_id, article.source_url, created_at),
        )
        snapshot_id = uuid4().hex
        connection.execute(
            """
            INSERT INTO article_snapshots (
                id, article_id, content_hash, payload_json, normalizer_version,
                collected_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                article.article_id,
                content_hash,
                json.dumps(_article_payload(article), ensure_ascii=False, sort_keys=True),
                1,
                _format_datetime(article.collected_at),
                created_at,
            ),
        )
        return snapshot_id

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        self._migrate(connection)
        return connection

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (_SCHEMA_VERSION,)
        ).fetchone()
        if applied is not None:
            return

        connection.executescript(
            """
            CREATE TABLE research_runs (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                feeds_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('collecting', 'completed', 'partial', 'failed')
                ),
                created_at TEXT NOT NULL,
                finished_at TEXT,
                errors_json TEXT NOT NULL
            );

            CREATE TABLE articles (
                id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE article_snapshots (
                id TEXT PRIMARY KEY,
                article_id TEXT NOT NULL REFERENCES articles(id),
                content_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                normalizer_version INTEGER NOT NULL,
                collected_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(article_id, content_hash)
            );

            CREATE TABLE run_evidence (
                run_id TEXT NOT NULL REFERENCES research_runs(id),
                snapshot_id TEXT NOT NULL REFERENCES article_snapshots(id),
                evidence_no INTEGER,
                relevance_score REAL,
                selected INTEGER NOT NULL CHECK (selected IN (0, 1)),
                PRIMARY KEY (run_id, snapshot_id),
                UNIQUE (run_id, evidence_no)
            );
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (_SCHEMA_VERSION, _format_datetime(project_now())),
        )


def _article_payload(article: NormalizedArticle) -> dict[str, object]:
    payload = asdict(article)
    payload["content_type"] = article.content_type.value
    payload["categories"] = list(article.categories)
    payload["content_chunks"] = list(article.content_chunks)
    payload["processing_warnings"] = list(article.processing_warnings)
    payload["published_at"] = (
        _format_datetime(article.published_at) if article.published_at else None
    )
    payload["collected_at"] = _format_datetime(article.collected_at)
    payload["schema_version"] = 1
    return payload


def _snapshot_key(article: NormalizedArticle) -> tuple[str, str]:
    return article.article_id, _content_hash(article)


def _content_hash(article: NormalizedArticle) -> str:
    return hashlib.sha256(article.content.encode("utf-8")).hexdigest()


def _article_from_payload(payload: dict[str, object]) -> NormalizedArticle:
    return NormalizedArticle(
        article_id=str(payload["article_id"]),
        source_url=str(payload["source_url"]),
        title=str(payload["title"]),
        content=str(payload["content"]),
        feed_url=_optional_text(payload.get("feed_url")),
        site_url=_optional_text(payload.get("site_url")),
        source_type=str(payload["source_type"]),
        author=_optional_text(payload.get("author")),
        categories=tuple(str(item) for item in payload["categories"]),
        language=_optional_text(payload.get("language")),
        content_type=ContentType(str(payload["content_type"])),
        content_chunks=tuple(str(item) for item in payload["content_chunks"]),
        published_at=_parse_datetime(payload.get("published_at")),
        collected_at=_required_datetime(payload["collected_at"]),
        processing_warnings=tuple(str(item) for item in payload["processing_warnings"]),
    )


def _optional_text(value: object | None) -> str | None:
    return str(value) if value is not None else None


def _parse_datetime(value: object | None) -> datetime | None:
    if value is None:
        return None
    return _required_datetime(value)


def _required_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("数据库中的日期时间必须包含时区")
    return parsed.astimezone(PROJECT_TIMEZONE)


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("数据库中的日期时间必须包含时区")
    return value.astimezone(PROJECT_TIMEZONE).isoformat(timespec="seconds")
