from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..collection import RawFeedEntry
from ..contracts import PROJECT_TIMEZONE, CollectionReport, ContentType, project_now
from ..investigation import SearchPlan
from ..normalization import NormalizedArticle
from ..selection import SelectedEvidence
from .models import FeedObservation, FeedState


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
        feed_observations: list[FeedObservation] | None = None,
    ) -> None:
        selected_by_snapshot_key = {_snapshot_key(item.article): item for item in report.articles}
        finished_at = _format_datetime(project_now())
        with self._connect() as connection:
            article_ids_by_url: dict[str, str] = {}
            for article in normalized_articles:
                snapshot_id = self._upsert_snapshot(connection, article)
                article_ids_by_url[article.source_url] = article.article_id
                selected = selected_by_snapshot_key.get(_snapshot_key(article))
                connection.execute(
                    """
                    INSERT INTO run_evidence (
                        run_id, snapshot_id, evidence_no, selected
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(run_id, snapshot_id) DO UPDATE SET
                        evidence_no = excluded.evidence_no,
                        selected = excluded.selected
                    """,
                    (
                        run_id,
                        snapshot_id,
                        selected.evidence_id if selected else None,
                        1 if selected else 0,
                    ),
                )

            for observation in feed_observations or []:
                self._record_feed_observation(connection, observation, article_ids_by_url)

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

    def feed_state(self, feed_url: str) -> FeedState:
        feed_id = _feed_id(feed_url)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT etag, last_modified FROM feeds WHERE id = ?", (feed_id,)
            ).fetchone()
        return FeedState(
            feed_id=feed_id,
            feed_url=feed_url,
            etag=str(row["etag"]) if row and row["etag"] else None,
            last_modified=str(row["last_modified"]) if row and row["last_modified"] else None,
        )

    def new_feed_entries(
        self,
        state: FeedState,
        entries: list[RawFeedEntry],
    ) -> list[RawFeedEntry]:
        new_entries: list[RawFeedEntry] = []
        with self._connect() as connection:
            for entry in entries:
                entry_key = _entry_key(entry)
                row = connection.execute(
                    """
                    SELECT updated_marker FROM feed_entries
                    WHERE feed_id = ? AND entry_key = ?
                    """,
                    (state.feed_id, entry_key),
                ).fetchone()
                marker = _entry_marker(entry)
                if row is None or (marker is not None and marker != row["updated_marker"]):
                    new_entries.append(entry)
        return new_entries

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
                SELECT snapshots.payload_json, evidence.evidence_no
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
            )
            for row in rows
        ]

    def load_planning_input(self, run_id: str) -> tuple[str, list[SelectedEvidence]]:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT topic, status FROM research_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if run is None:
            raise ValueError(f"不存在的研究运行：{run_id}")
        if run["status"] not in {"completed", "partial"}:
            raise ValueError(f"研究运行尚未产生可规划结果：{run_id}")
        return str(run["topic"]), self.load_selected_evidence(run_id)

    def start_planning(self, run_id: str) -> str:
        planning_run_id = uuid4().hex
        created_at = _format_datetime(project_now())
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM research_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if exists is None:
                raise ValueError(f"不存在的研究运行：{run_id}")
            connection.execute(
                """
                INSERT INTO planning_runs (
                    id, run_id, status, raw_response, errors_json, created_at
                )
                VALUES (?, ?, 'started', NULL, '[]', ?)
                """,
                (planning_run_id, run_id, created_at),
            )
        return planning_run_id

    def complete_planning(
        self,
        planning_run_id: str,
        run_id: str,
        plans: list[SearchPlan],
        raw_response: str | None,
    ) -> None:
        finished_at = _format_datetime(project_now())
        with self._connect() as connection:
            for plan in plans:
                evidence = connection.execute(
                    """
                    SELECT snapshot_id FROM run_evidence
                    WHERE run_id = ? AND evidence_no = ? AND selected = 1
                    """,
                    (run_id, plan.evidence_id),
                ).fetchone()
                if evidence is None:
                    raise ValueError(f"规划引用了不存在的已选证据：{plan.evidence_id}")
                plan_id = uuid4().hex
                connection.execute(
                    """
                    INSERT INTO search_plans (
                        id, planning_run_id, run_id, snapshot_id, evidence_no,
                        trigger_quote, question, kind, priority
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id,
                        planning_run_id,
                        run_id,
                        evidence["snapshot_id"],
                        plan.evidence_id,
                        plan.trigger_quote,
                        plan.question,
                        plan.kind.value,
                        plan.priority,
                    ),
                )
                for position, query in enumerate(plan.queries, start=1):
                    connection.execute(
                        """
                        INSERT INTO search_queries (plan_id, position, query, purpose)
                        VALUES (?, ?, ?, ?)
                        """,
                        (plan_id, position, query.query, query.purpose),
                    )

            updated = connection.execute(
                """
                UPDATE planning_runs
                SET status = 'completed', raw_response = ?, finished_at = ?
                WHERE id = ? AND run_id = ? AND status = 'started'
                """,
                (raw_response, finished_at, planning_run_id, run_id),
            )
            if updated.rowcount != 1:
                raise ValueError(f"无法完成不存在或已结束的规划运行：{planning_run_id}")

    def fail_planning(
        self,
        planning_run_id: str,
        run_id: str,
        error: Exception,
        raw_response: str | None = None,
    ) -> None:
        finished_at = _format_datetime(project_now())
        errors = [{"type": type(error).__name__, "message": str(error)}]
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE planning_runs
                SET status = 'failed', raw_response = ?, finished_at = ?, errors_json = ?
                WHERE id = ? AND run_id = ? AND status = 'started'
                """,
                (
                    raw_response,
                    finished_at,
                    json.dumps(errors, ensure_ascii=False),
                    planning_run_id,
                    run_id,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError(f"无法标记不存在或已结束的规划运行：{planning_run_id}")

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

    def _record_feed_observation(
        self,
        connection: sqlite3.Connection,
        observation: FeedObservation,
        article_ids_by_url: dict[str, str],
    ) -> None:
        state = observation.state
        observed_at = _format_datetime(project_now())
        connection.execute(
            """
            INSERT INTO feeds (id, feed_url, etag, last_modified, last_success_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                last_success_at = excluded.last_success_at
            """,
            (
                state.feed_id,
                state.feed_url,
                observation.etag,
                observation.last_modified,
                observed_at,
            ),
        )
        for entry in observation.new_entries:
            connection.execute(
                """
                INSERT INTO feed_entries (
                    feed_id, entry_key, article_url, article_id, updated_marker,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(feed_id, entry_key) DO UPDATE SET
                    article_url = excluded.article_url,
                    article_id = excluded.article_id,
                    updated_marker = excluded.updated_marker,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    state.feed_id,
                    _entry_key(entry),
                    entry.source_url,
                    article_ids_by_url.get(entry.source_url),
                    _entry_marker(entry),
                    observed_at,
                    observed_at,
                ),
            )

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
        applied_versions = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations")
        }
        if 1 not in applied_versions:
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
                selected INTEGER NOT NULL CHECK (selected IN (0, 1)),
                PRIMARY KEY (run_id, snapshot_id),
                UNIQUE (run_id, evidence_no)
            );
            """
            )
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (1, _format_datetime(project_now())),
            )
        if 2 not in applied_versions:
            connection.executescript(
                """
                CREATE TABLE feeds (
                    id TEXT PRIMARY KEY,
                    feed_url TEXT NOT NULL UNIQUE,
                    etag TEXT,
                    last_modified TEXT,
                    last_success_at TEXT
                );

                CREATE TABLE feed_entries (
                    feed_id TEXT NOT NULL REFERENCES feeds(id),
                    entry_key TEXT NOT NULL,
                    article_url TEXT NOT NULL,
                    article_id TEXT REFERENCES articles(id),
                    updated_marker TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (feed_id, entry_key)
                );
                """
            )
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (2, _format_datetime(project_now())),
            )
        if 3 not in applied_versions:
            connection.executescript(
                """
                CREATE TABLE planning_runs (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES research_runs(id),
                    status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed')),
                    raw_response TEXT,
                    errors_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    UNIQUE (id, run_id)
                );

                CREATE TABLE search_plans (
                    id TEXT PRIMARY KEY,
                    planning_run_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    evidence_no INTEGER NOT NULL,
                    trigger_quote TEXT NOT NULL,
                    question TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    FOREIGN KEY (planning_run_id, run_id)
                        REFERENCES planning_runs(id, run_id),
                    FOREIGN KEY (run_id, snapshot_id)
                        REFERENCES run_evidence(run_id, snapshot_id)
                );

                CREATE TABLE search_queries (
                    plan_id TEXT NOT NULL REFERENCES search_plans(id),
                    position INTEGER NOT NULL,
                    query TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    PRIMARY KEY (plan_id, position)
                );

                CREATE INDEX planning_runs_run_id_idx ON planning_runs(run_id);
                CREATE INDEX search_plans_planning_run_id_idx
                    ON search_plans(planning_run_id);
                """
            )
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (3, _format_datetime(project_now())),
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
    return payload


def _snapshot_key(article: NormalizedArticle) -> tuple[str, str]:
    return article.article_id, _content_hash(article)


def _content_hash(article: NormalizedArticle) -> str:
    return hashlib.sha256(article.content.encode("utf-8")).hexdigest()


def _feed_id(feed_url: str) -> str:
    digest = hashlib.sha256(feed_url.encode("utf-8")).hexdigest()
    return f"feed-{digest}"


def _entry_key(entry: RawFeedEntry) -> str:
    return entry.entry_id or entry.source_url


def _entry_marker(entry: RawFeedEntry) -> str | None:
    value = entry.updated_at or entry.published_at
    if value is None:
        return None
    if isinstance(value, datetime):
        return _format_datetime(value)
    marker = str(value).strip()
    return marker or None


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
