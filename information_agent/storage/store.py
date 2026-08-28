from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..collection import RawFeedEntry
from ..common import should_generate_article_summary
from ..contracts import CollectionReport, ContentType, project_now
from ..investigation import (
    OPINION_PLATFORM,
    OPINION_WINDOW_HOURS,
    OpinionPlan,
    SearchPlan,
    SearchQuery,
)
from ..normalization import (
    NormalizedArticle,
    content_blocks_from_payload,
    content_blocks_to_payload,
)
from ..selection import SelectedEvidence
from .analysis_store import AnalysisPersistenceMixin
from .common import (
    _format_datetime,
    _load_json_object,
    _optional_text,
    _parse_datetime,
    _required_datetime,
)
from .models import (
    ArticleSnapshotMismatchError,
    FeedObservation,
    FeedState,
    FeedSubscription,
    OpinionRunRecord,
    ReaderArticle,
    ReaderArticleState,
    ResearchRunNotFoundError,
    ResearchRunNotReadyError,
)
from .reader_answers import ReaderAnswerPersistenceMixin
from .reader_automation import ReaderAutomationPersistenceMixin, _summary_error_message

_OPINION_STATUS_REASONS = {
    "completed": {"completed", "no_controversy_points", "sample_empty"},
    "partial": {"partial_collection", "partial_classification", "timeout", "retry_exhausted"},
    "failed": {"timeout", "retry_exhausted", "stale_running", "failed"},
}
_OPINION_STANCES = {"support", "oppose", "mixed", "unclear"}
_OPINION_CLASSIFICATION_STATUSES = {"classified", "unclassified"}
_OPINION_ATTEMPT_STAGES = {
    "opinion_planning",
    "aid_resolution",
    "comment_collection",
    "opinion_analysis",
    "classification",
}
_OPINION_ATTEMPT_OUTCOMES = {"succeeded", "failed", "timed_out", "skipped"}


def default_database_path() -> Path:
    configured = os.getenv("INFORMATION_AGENT_DB_PATH")
    if configured:
        return Path(configured)
    return Path("data") / "information_agent.db"


class SQLiteCollectionStore(
    AnalysisPersistenceMixin,
    ReaderAnswerPersistenceMixin,
    ReaderAutomationPersistenceMixin,
):
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

    def save_subscription(
        self,
        *,
        feed_url: str,
        title: str,
        site_url: str | None,
        result_etag: str | None,
        result_last_modified: str | None,
        entries: list[RawFeedEntry],
        articles: list[NormalizedArticle],
    ) -> FeedSubscription:
        state = self.feed_state(feed_url)
        new_entries = self.new_feed_entries(state, entries)
        observed_at = _format_datetime(project_now())
        observation = FeedObservation(
            state=state,
            etag=result_etag,
            last_modified=result_last_modified,
            not_modified=False,
            new_entries=new_entries,
        )
        with self._connect() as connection:
            article_ids_by_url: dict[str, str] = {}
            for article in articles:
                self._upsert_snapshot(connection, article)
                article_ids_by_url[article.source_url] = article.article_id
            self._record_feed_observation(connection, observation, article_ids_by_url)
            connection.execute(
                """
                INSERT INTO feed_subscriptions (
                    feed_id, title, site_url, subscribed_at, last_refreshed_at, last_error
                ) VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(feed_id) DO UPDATE SET
                    title = excluded.title,
                    site_url = COALESCE(excluded.site_url, feed_subscriptions.site_url),
                    last_refreshed_at = excluded.last_refreshed_at,
                    last_error = NULL
                """,
                (state.feed_id, title, site_url, observed_at, observed_at),
            )
        subscription = self.get_subscription(state.feed_id)
        if subscription is None:
            raise RuntimeError("订阅保存后无法读取")
        return subscription

    def list_subscriptions(self) -> list[FeedSubscription]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT subscriptions.feed_id, feeds.feed_url, subscriptions.title,
                       subscriptions.site_url, subscriptions.subscribed_at,
                       subscriptions.last_refreshed_at, subscriptions.last_error,
                       COUNT(CASE WHEN deleted.article_id IS NULL
                                  THEN feed_entries.article_id END) AS article_count,
                       COUNT(CASE WHEN deleted.article_id IS NULL
                                  THEN feed_entries.article_id END)
                         - COUNT(CASE WHEN deleted.article_id IS NULL
                                           AND states.is_read = 1
                                      THEN feed_entries.article_id END)
                         AS unread_count
                FROM feed_subscriptions AS subscriptions
                JOIN feeds ON feeds.id = subscriptions.feed_id
                LEFT JOIN feed_entries ON feed_entries.feed_id = subscriptions.feed_id
                LEFT JOIN reader_article_states AS states
                    ON states.article_id = feed_entries.article_id
                LEFT JOIN reader_deleted_articles AS deleted
                    ON deleted.article_id = feed_entries.article_id
                GROUP BY subscriptions.feed_id
                ORDER BY subscriptions.subscribed_at, subscriptions.feed_id
                """
            ).fetchall()
        return [_subscription_from_row(row) for row in rows]

    def get_subscription(self, feed_id: str) -> FeedSubscription | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT subscriptions.feed_id, feeds.feed_url, subscriptions.title,
                       subscriptions.site_url, subscriptions.subscribed_at,
                       subscriptions.last_refreshed_at, subscriptions.last_error,
                       COUNT(CASE WHEN deleted.article_id IS NULL
                                  THEN feed_entries.article_id END) AS article_count,
                       COUNT(CASE WHEN deleted.article_id IS NULL
                                  THEN feed_entries.article_id END)
                         - COUNT(CASE WHEN deleted.article_id IS NULL
                                           AND states.is_read = 1
                                      THEN feed_entries.article_id END)
                         AS unread_count
                FROM feed_subscriptions AS subscriptions
                JOIN feeds ON feeds.id = subscriptions.feed_id
                LEFT JOIN feed_entries ON feed_entries.feed_id = subscriptions.feed_id
                LEFT JOIN reader_article_states AS states
                    ON states.article_id = feed_entries.article_id
                LEFT JOIN reader_deleted_articles AS deleted
                    ON deleted.article_id = feed_entries.article_id
                WHERE subscriptions.feed_id = ?
                GROUP BY subscriptions.feed_id
                """,
                (feed_id,),
            ).fetchone()
        return _subscription_from_row(row) if row is not None else None

    def record_subscription_error(self, feed_id: str, error: Exception) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE feed_subscriptions SET last_error = ? WHERE feed_id = ?",
                (str(error), feed_id),
            )

    def delete_subscription(self, feed_id: str) -> bool:
        with self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM feed_subscriptions WHERE feed_id = ?",
                (feed_id,),
            )
        return deleted.rowcount == 1

    def delete_reader_article(self, article_id: str) -> bool:
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT 1
                FROM feed_entries AS entries
                JOIN feed_subscriptions AS subscriptions
                    ON subscriptions.feed_id = entries.feed_id
                WHERE entries.article_id = ?
                LIMIT 1
                """,
                (article_id,),
            ).fetchone()
            if existing is None:
                return False
            connection.execute(
                """
                INSERT OR IGNORE INTO reader_deleted_articles (article_id, deleted_at)
                VALUES (?, ?)
                """,
                (article_id, _format_datetime(project_now())),
            )
        return True

    def list_reader_articles(
        self,
        *,
        feed_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ReaderArticle]:
        parameters: list[object] = []
        if feed_id is not None:
            query = """
                SELECT entries.feed_id, snapshots.id AS snapshot_id, snapshots.content_hash,
                       snapshots.payload_json, summaries.summary,
                       summaries.status AS summary_status,
                       summaries.error_json AS summary_error,
                       COALESCE((SELECT research.status FROM article_research_runs AS research
                                 WHERE research.article_id = entries.article_id
                                   AND research.snapshot_id = snapshots.id
                                 ORDER BY research.created_at DESC, research.rowid DESC LIMIT 1),
                                'none') AS research_status,
                       (SELECT research.mode FROM article_research_runs AS research
                        WHERE research.article_id = entries.article_id
                          AND research.snapshot_id = snapshots.id
                        ORDER BY research.created_at DESC, research.rowid DESC LIMIT 1
                       ) AS research_mode,
                       COALESCE(states.is_read, 0) AS is_read,
                       COALESCE(states.is_saved, 0) AS is_saved
                FROM feed_entries AS entries
                JOIN feed_subscriptions AS subscriptions
                    ON subscriptions.feed_id = entries.feed_id
                JOIN article_snapshots AS snapshots
                    ON snapshots.article_id = entries.article_id
                LEFT JOIN article_summaries AS summaries
                    ON summaries.snapshot_id = snapshots.id
                LEFT JOIN reader_article_states AS states
                    ON states.article_id = entries.article_id
                WHERE entries.feed_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM reader_deleted_articles AS deleted
                      WHERE deleted.article_id = entries.article_id
                  )
                  AND snapshots.id = (
                      SELECT latest.id FROM article_snapshots AS latest
                      WHERE latest.article_id = entries.article_id
                      ORDER BY latest.collected_at DESC, latest.created_at DESC
                      LIMIT 1
                  )
                ORDER BY snapshots.collected_at DESC, entries.entry_key
                LIMIT ? OFFSET ?
                """
            parameters.append(feed_id)
        else:
            query = """
                SELECT entries.feed_id, snapshots.id AS snapshot_id, snapshots.content_hash,
                       snapshots.payload_json, summaries.summary,
                       summaries.status AS summary_status,
                       summaries.error_json AS summary_error,
                       COALESCE((SELECT research.status FROM article_research_runs AS research
                                 WHERE research.article_id = entries.article_id
                                   AND research.snapshot_id = snapshots.id
                                 ORDER BY research.created_at DESC, research.rowid DESC LIMIT 1),
                                'none') AS research_status,
                       (SELECT research.mode FROM article_research_runs AS research
                        WHERE research.article_id = entries.article_id
                          AND research.snapshot_id = snapshots.id
                        ORDER BY research.created_at DESC, research.rowid DESC LIMIT 1
                       ) AS research_mode,
                       COALESCE(states.is_read, 0) AS is_read,
                       COALESCE(states.is_saved, 0) AS is_saved
                FROM feed_entries AS entries
                JOIN feed_subscriptions AS subscriptions
                    ON subscriptions.feed_id = entries.feed_id
                JOIN article_snapshots AS snapshots
                    ON snapshots.article_id = entries.article_id
                LEFT JOIN article_summaries AS summaries
                    ON summaries.snapshot_id = snapshots.id
                LEFT JOIN reader_article_states AS states
                    ON states.article_id = entries.article_id
                WHERE snapshots.id = (
                    SELECT latest.id FROM article_snapshots AS latest
                    WHERE latest.article_id = entries.article_id
                    ORDER BY latest.collected_at DESC, latest.created_at DESC
                    LIMIT 1
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM reader_deleted_articles AS deleted
                      WHERE deleted.article_id = entries.article_id
                  )
                ORDER BY snapshots.collected_at DESC, entries.entry_key
                LIMIT ? OFFSET ?
                """
        parameters.extend((limit, offset))
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_reader_article_from_row(row) for row in rows]

    def get_reader_article(self, article_id: str) -> ReaderArticle | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT entries.feed_id, snapshots.id AS snapshot_id, snapshots.content_hash,
                       snapshots.payload_json, summaries.summary,
                       summaries.status AS summary_status,
                       summaries.error_json AS summary_error,
                       COALESCE((SELECT research.status FROM article_research_runs AS research
                                 WHERE research.article_id = entries.article_id
                                   AND research.snapshot_id = snapshots.id
                                 ORDER BY research.created_at DESC, research.rowid DESC LIMIT 1),
                                'none') AS research_status,
                       (SELECT research.mode FROM article_research_runs AS research
                        WHERE research.article_id = entries.article_id
                          AND research.snapshot_id = snapshots.id
                        ORDER BY research.created_at DESC, research.rowid DESC LIMIT 1
                       ) AS research_mode,
                       COALESCE(states.is_read, 0) AS is_read,
                       COALESCE(states.is_saved, 0) AS is_saved
                FROM feed_entries AS entries
                JOIN feed_subscriptions AS subscriptions
                    ON subscriptions.feed_id = entries.feed_id
                JOIN article_snapshots AS snapshots
                    ON snapshots.article_id = entries.article_id
                LEFT JOIN article_summaries AS summaries
                    ON summaries.snapshot_id = snapshots.id
                LEFT JOIN reader_article_states AS states
                    ON states.article_id = entries.article_id
                WHERE entries.article_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM reader_deleted_articles AS deleted
                      WHERE deleted.article_id = entries.article_id
                  )
                ORDER BY snapshots.collected_at DESC, snapshots.created_at DESC
                LIMIT 1
                """,
                (article_id,),
            ).fetchone()
        if row is None:
            return None
        return _reader_article_from_row(row)

    def update_reader_article_states(
        self,
        article_ids: list[str],
        *,
        is_read: bool | None = None,
        is_saved: bool | None = None,
    ) -> list[ReaderArticleState]:
        unique_ids = list(dict.fromkeys(article_ids))
        if not unique_ids:
            raise ValueError("至少需要一个文章 ID")
        if is_read is None and is_saved is None:
            raise ValueError("至少需要提供一个文章状态")

        placeholders = ", ".join("?" for _ in unique_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT DISTINCT entries.article_id
                FROM feed_entries AS entries
                JOIN feed_subscriptions AS subscriptions
                    ON subscriptions.feed_id = entries.feed_id
                WHERE entries.article_id IN ({placeholders})
                  AND NOT EXISTS (
                      SELECT 1 FROM reader_deleted_articles AS deleted
                      WHERE deleted.article_id = entries.article_id
                  )
                """,
                unique_ids,
            ).fetchall()
            existing_ids = {str(row["article_id"]) for row in rows}
            missing_ids = [
                article_id for article_id in unique_ids if article_id not in existing_ids
            ]
            if missing_ids:
                raise KeyError(missing_ids[0])

            updated_at = _format_datetime(project_now())
            for article_id in unique_ids:
                current = connection.execute(
                    """
                    SELECT is_read, is_saved, read_at, saved_at
                    FROM reader_article_states
                    WHERE article_id = ?
                    """,
                    (article_id,),
                ).fetchone()
                current_is_read = bool(current["is_read"]) if current else False
                current_is_saved = bool(current["is_saved"]) if current else False
                current_read_at = _optional_text(current["read_at"]) if current else None
                current_saved_at = _optional_text(current["saved_at"]) if current else None
                next_is_read = current_is_read if is_read is None else is_read
                next_is_saved = current_is_saved if is_saved is None else is_saved
                next_read_at = (
                    current_read_at if is_read is None else updated_at if is_read else None
                )
                next_saved_at = (
                    current_saved_at if is_saved is None else updated_at if is_saved else None
                )
                connection.execute(
                    """
                    INSERT INTO reader_article_states (
                        article_id, is_read, is_saved, read_at, saved_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(article_id) DO UPDATE SET
                        is_read = excluded.is_read,
                        is_saved = excluded.is_saved,
                        read_at = excluded.read_at,
                        saved_at = excluded.saved_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        article_id,
                        int(next_is_read),
                        int(next_is_saved),
                        next_read_at,
                        next_saved_at,
                        updated_at,
                    ),
                )

            state_rows = connection.execute(
                f"""
                SELECT article_id, is_read, is_saved, read_at, saved_at, updated_at
                FROM reader_article_states
                WHERE article_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()

        states_by_id = {
            str(row["article_id"]): _reader_article_state_from_row(row) for row in state_rows
        }
        return [states_by_id[article_id] for article_id in unique_ids]

    def get_latest_opinion_run(
        self,
        article_id: str,
        *,
        platform: str = OPINION_PLATFORM,
        window_hours: int = OPINION_WINDOW_HOURS,
    ) -> OpinionRunRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM opinion_runs
                WHERE article_id = ? AND platform = ? AND window_hours = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (article_id, platform, window_hours),
            ).fetchone()
        return _opinion_run_from_row(row) if row is not None else None

    def load_opinion_plans_for_article(
        self,
        article_id: str,
        *,
        article_snapshot_id: str | None = None,
        content_hash: str | None = None,
    ) -> list[OpinionPlan]:
        if (article_snapshot_id is None) != (content_hash is None):
            raise ValueError("article_snapshot_id 和 content_hash 必须同时存在")
        with self._connect() as connection:
            latest_run = connection.execute(
                """
                SELECT planning_runs.id, article_snapshots.id AS snapshot_id,
                       article_snapshots.content_hash
                FROM planning_runs
                JOIN run_evidence
                    ON run_evidence.run_id = planning_runs.run_id
                   AND run_evidence.selected = 1
                JOIN article_snapshots
                    ON article_snapshots.id = run_evidence.snapshot_id
                WHERE article_snapshots.article_id = ?
                  AND planning_runs.status = 'completed'
                ORDER BY planning_runs.created_at DESC, planning_runs.rowid DESC
                LIMIT 1
                """,
                (article_id,),
            ).fetchone()
            if latest_run is None:
                return []
            if article_snapshot_id is not None and (
                str(latest_run["snapshot_id"]) != article_snapshot_id
                or str(latest_run["content_hash"]) != content_hash
            ):
                raise ArticleSnapshotMismatchError(
                    "文章规划与当前文章快照不一致：article_snapshot_mismatch"
                )
            rows = connection.execute(
                """
                SELECT plans.id, plans.planning_run_id, plans.evidence_no,
                       plans.trigger_quote, plans.question, plans.platform,
                       plans.window_hours, queries.position, queries.query, queries.purpose,
                       planning_runs.created_at
                FROM opinion_plans AS plans
                JOIN article_snapshots AS snapshots
                    ON snapshots.id = plans.snapshot_id
                JOIN planning_runs
                    ON planning_runs.id = plans.planning_run_id
                LEFT JOIN opinion_queries AS queries
                    ON queries.plan_id = plans.id
                WHERE plans.planning_run_id = ?
                  AND plans.snapshot_id = ?
                  AND plans.platform = ?
                ORDER BY plans.id, queries.position
                """,
                (latest_run["id"], latest_run["snapshot_id"], OPINION_PLATFORM),
            ).fetchall()

        if not rows:
            return []
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            evidence_no = int(row["evidence_no"])
            item = grouped.setdefault(
                evidence_no,
                {
                    "trigger_quote": str(row["trigger_quote"]),
                    "question": str(row["question"]),
                    "platform": str(row["platform"]),
                    "window_hours": int(row["window_hours"]),
                    "queries": [],
                },
            )
            if row["position"] is not None:
                item["queries"].append(SearchQuery(str(row["query"]), str(row["purpose"])))
        return [
            OpinionPlan(
                evidence_id=evidence_no,
                trigger_quote=item["trigger_quote"],
                question=item["question"],
                queries=tuple(item["queries"]),
                platform=item["platform"],
                window_hours=item["window_hours"],
            )
            for evidence_no, item in sorted(grouped.items())
        ]

    def start_opinion_run(
        self,
        article_id: str,
        *,
        platform: str = OPINION_PLATFORM,
        window_hours: int = OPINION_WINDOW_HOURS,
        article_snapshot_id: str | None = None,
        content_hash: str | None = None,
        requested_limit: int | None = None,
        timeout_seconds: float = 300.0,
        stale_grace_seconds: float = 30.0,
    ) -> OpinionRunRecord:
        record, _ = self.acquire_opinion_run(
            article_id,
            platform=platform,
            window_hours=window_hours,
            article_snapshot_id=article_snapshot_id,
            content_hash=content_hash,
            requested_limit=requested_limit,
            timeout_seconds=timeout_seconds,
            stale_grace_seconds=stale_grace_seconds,
        )
        return record

    def acquire_opinion_run(
        self,
        article_id: str,
        *,
        platform: str = OPINION_PLATFORM,
        window_hours: int = OPINION_WINDOW_HOURS,
        article_snapshot_id: str | None = None,
        content_hash: str | None = None,
        requested_limit: int | None = None,
        timeout_seconds: float = 300.0,
        stale_grace_seconds: float = 30.0,
    ) -> tuple[OpinionRunRecord, bool]:
        """Atomically reuse an active run or create the only active run for a target."""
        if platform != OPINION_PLATFORM:
            raise ValueError(f"舆情平台固定为 {OPINION_PLATFORM}")
        if window_hours != OPINION_WINDOW_HOURS:
            raise ValueError(f"舆情时间窗固定为 {OPINION_WINDOW_HOURS} 小时")
        if (article_snapshot_id is None) != (content_hash is None):
            raise ValueError("article_snapshot_id 和 content_hash 必须同时存在")
        if requested_limit is not None and not 1 <= requested_limit <= 200:
            raise ValueError("requested_limit 必须在 1 到 200 之间")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive finite number")
        if not math.isfinite(stale_grace_seconds) or stale_grace_seconds < 0:
            raise ValueError("stale_grace_seconds must be a non-negative finite number")
        run_id = uuid4().hex
        now = project_now()
        created_at = _format_datetime(now)
        metadata = {
            "article_snapshot_id": article_snapshot_id,
            "content_hash": content_hash,
            "requested_limit": requested_limit,
            "timeout_seconds": timeout_seconds,
        }
        record: OpinionRunRecord | None = None
        created = False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            article = connection.execute(
                "SELECT 1 FROM articles WHERE id = ?", (article_id,)
            ).fetchone()
            if article is None:
                raise ValueError(f"不存在的文章：{article_id}")

            active = connection.execute(
                """
                SELECT * FROM opinion_runs
                WHERE article_id = ? AND platform = ? AND window_hours = ?
                  AND status = 'running'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (article_id, platform, window_hours),
            ).fetchone()
            if active is not None:
                heartbeat = _opinion_heartbeat(active)
                active_timeout = _opinion_timeout_seconds(active)
                if heartbeat + timedelta(seconds=active_timeout + stale_grace_seconds) > now:
                    record = _opinion_run_from_row(active)
                else:
                    _mark_stale_opinion_run(connection, active, now)

            if record is None:
                try:
                    connection.execute(
                        """
                        INSERT INTO opinion_runs (
                            id, article_id, platform, window_hours, status,
                            created_at, started_at, finished_at, last_heartbeat_at,
                            timeout_seconds, article_snapshot_id, content_hash,
                            requested_limit, collected_count, analyzed_count,
                            classification_total, classified_count, unclassified_count,
                            status_reason, errors_json, result_json
                        ) VALUES (
                            ?, ?, ?, ?, 'running', ?, ?, NULL, ?, ?, ?, ?, ?,
                            0, 0, 0, 0, 0, 'running', '[]', ?
                        )
                        """,
                        (
                            run_id,
                            article_id,
                            platform,
                            window_hours,
                            created_at,
                            created_at,
                            created_at,
                            timeout_seconds,
                            article_snapshot_id,
                            content_hash,
                            requested_limit,
                            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                        ),
                    )
                except sqlite3.IntegrityError:
                    active = connection.execute(
                        """
                        SELECT * FROM opinion_runs
                        WHERE article_id = ? AND platform = ? AND window_hours = ?
                          AND status = 'running'
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                        """,
                        (article_id, platform, window_hours),
                    ).fetchone()
                    if active is None:
                        raise
                    record = _opinion_run_from_row(active)
                else:
                    created = True
                    row = connection.execute(
                        "SELECT * FROM opinion_runs WHERE id = ?", (run_id,)
                    ).fetchone()
                    assert row is not None
                    record = _opinion_run_from_row(row)
        assert record is not None
        return record, created

    def complete_opinion_run(
        self,
        run_id: str,
        *,
        status: str,
        result_payload: dict[str, Any],
        comments: list[dict[str, Any]],
        errors: list[dict[str, Any]] | list[str] | None = None,
        classifications: list[dict[str, Any]] | None = None,
        attempts: list[dict[str, Any]] | None = None,
    ) -> OpinionRunRecord:
        if status not in {"completed", "partial", "failed"}:
            raise ValueError("舆情运行结束状态无效")
        if status == "completed":
            summary = result_payload.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                raise ValueError("completed 舆情结果必须包含非空 summary")
        finished_at = _format_datetime(project_now())
        errors_payload = [
            dict(error) if isinstance(error, dict) else {"type": "OpinionError", "message": error}
            for error in errors or []
        ]
        persisted_comments = _opinion_comment_rows(comments)
        persisted_classifications = _opinion_classification_rows(
            classifications
            if classifications is not None
            else result_payload.get("classifications", []),
            run_id=run_id,
        )
        persisted_attempts = _opinion_attempt_rows(
            attempts if attempts is not None else result_payload.get("attempts", [])
        )
        comment_ids = {item["comment_id"] for item in persisted_comments}
        for item in persisted_classifications:
            if item["comment_id"] not in comment_ids:
                raise ValueError("分类关系引用了当前运行之外的评论")

        with self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM opinion_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise ValueError(f"不存在的舆情运行：{run_id}")
            if run["status"] != "running":
                raise ValueError(f"无法完成不存在或已结束的舆情运行：{run_id}")

            snapshot_id = _optional_text(
                result_payload.get("article_snapshot_id") or run["article_snapshot_id"]
            )
            content_hash = _optional_text(result_payload.get("content_hash") or run["content_hash"])
            if (snapshot_id is None) != (content_hash is None):
                raise ValueError("article_snapshot_id 和 content_hash 必须同时存在")
            requested_limit = result_payload.get("requested_limit")
            if requested_limit is None:
                requested_limit = run["requested_limit"]
            if requested_limit is not None and (
                type(requested_limit) is not int or not 1 <= requested_limit <= 200
            ):
                raise ValueError("requested_limit 必须在 1 到 200 之间")
            collected_count = len(persisted_comments)
            analyzed_value = result_payload.get("analyzed_count")
            if analyzed_value is None:
                analyzed_value = run["analyzed_count"]
            analyzed_count = _opinion_nonnegative_int(analyzed_value, "analyzed_count")
            if requested_limit is not None and collected_count > requested_limit:
                raise ValueError("collected_count 不能超过 requested_limit")
            if analyzed_count > collected_count:
                raise ValueError("analyzed_count 不能超过 collected_count")
            classification_total = len(persisted_classifications)
            classified_count = sum(
                item["classification_status"] == "classified" for item in persisted_classifications
            )
            unclassified_count = classification_total - classified_count
            status_reason = str(
                result_payload.get("status_reason") or _default_opinion_status_reason(status)
            )
            if status_reason not in _OPINION_STATUS_REASONS[status]:
                raise ValueError(f"{status} 不允许使用状态原因 {status_reason}")

            persisted_payload = dict(result_payload)
            persisted_payload.update(
                {
                    "status": status,
                    "status_reason": status_reason,
                    "article_snapshot_id": snapshot_id,
                    "content_hash": content_hash,
                    "requested_limit": requested_limit,
                    "collected_count": collected_count,
                    "analyzed_count": analyzed_count,
                    "classification_total": classification_total,
                    "classified_count": classified_count,
                    "unclassified_count": unclassified_count,
                    "comments": [dict(item) for item in persisted_comments],
                    "classifications": [dict(item) for item in persisted_classifications],
                    "attempts": [dict(item) for item in persisted_attempts],
                    "errors": [dict(item) for item in errors_payload],
                }
            )
            persisted_payload.setdefault("requested_at", str(run["created_at"]))
            persisted_payload["finished_at"] = finished_at
            persisted_payload["last_heartbeat_at"] = finished_at
            for comment in persisted_comments:
                payload_json = json.dumps(comment, ensure_ascii=False, sort_keys=True)
                connection.execute(
                    """
                    INSERT INTO opinion_comments (
                        run_id, comment_id, source_url, author, content,
                        likes, published_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        comment["comment_id"],
                        comment["source_url"],
                        comment["author"],
                        comment["content"],
                        comment["likes"],
                        comment.get("published_at"),
                        payload_json,
                    ),
                )
            for classification in persisted_classifications:
                connection.execute(
                    """
                    INSERT INTO opinion_classifications (
                        run_id, evidence_id, comment_id, classification_status,
                        stance, error_code
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        classification["evidence_id"],
                        classification["comment_id"],
                        classification["classification_status"],
                        classification["stance"],
                        classification["error_code"],
                    ),
                )
            for attempt in persisted_attempts:
                connection.execute(
                    """
                    INSERT INTO opinion_attempts (
                        run_id, stage, attempt_no, started_at, finished_at,
                        outcome, error_code, error_summary
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, stage, attempt_no) DO UPDATE SET
                        started_at = excluded.started_at,
                        finished_at = excluded.finished_at,
                        outcome = excluded.outcome,
                        error_code = excluded.error_code,
                        error_summary = excluded.error_summary
                    """,
                    (
                        run_id,
                        attempt["stage"],
                        attempt["attempt"],
                        attempt["started_at"],
                        attempt["finished_at"],
                        attempt["outcome"],
                        attempt["error_code"],
                        attempt["error_summary"],
                    ),
                )
            updated = connection.execute(
                """
                UPDATE opinion_runs
                SET status = ?, finished_at = ?, last_heartbeat_at = ?,
                    article_snapshot_id = ?, content_hash = ?, requested_limit = ?,
                    collected_count = ?, analyzed_count = ?, classification_total = ?,
                    classified_count = ?, unclassified_count = ?, status_reason = ?,
                    errors_json = ?, result_json = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    status,
                    finished_at,
                    finished_at,
                    snapshot_id,
                    content_hash,
                    requested_limit,
                    collected_count,
                    analyzed_count,
                    classification_total,
                    classified_count,
                    unclassified_count,
                    status_reason,
                    json.dumps(errors_payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(persisted_payload, ensure_ascii=False, sort_keys=True),
                    run_id,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError(f"无法完成不存在或已结束的舆情运行：{run_id}")
            row = connection.execute(
                "SELECT * FROM opinion_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row is not None
        return _opinion_run_from_row(row)

    def heartbeat_opinion_run(
        self,
        run_id: str,
        *,
        attempts: list[dict[str, Any]] | None = None,
    ) -> OpinionRunRecord:
        heartbeat_at = _format_datetime(project_now())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM opinion_runs WHERE id = ? AND status = 'running'",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"无法更新不存在或已结束的舆情运行：{run_id}")
            result_payload = (
                _load_json_object(row["result_json"], "opinion_runs.result_json")
                if row["result_json"] is not None
                else {}
            )
            if attempts is not None:
                persisted_attempts = _opinion_attempt_rows(attempts)
                result_payload["attempts"] = attempts
                for attempt in persisted_attempts:
                    connection.execute(
                        """
                        INSERT INTO opinion_attempts (
                            run_id, stage, attempt_no, started_at, finished_at,
                            outcome, error_code, error_summary
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(run_id, stage, attempt_no) DO UPDATE SET
                            started_at = excluded.started_at,
                            finished_at = excluded.finished_at,
                            outcome = excluded.outcome,
                            error_code = excluded.error_code,
                            error_summary = excluded.error_summary
                        """,
                        (
                            run_id,
                            attempt["stage"],
                            attempt["attempt"],
                            attempt["started_at"],
                            attempt["finished_at"],
                            attempt["outcome"],
                            attempt["error_code"],
                            attempt["error_summary"],
                        ),
                    )
            connection.execute(
                """
                UPDATE opinion_runs
                SET last_heartbeat_at = ?, result_json = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    heartbeat_at,
                    json.dumps(result_payload, ensure_ascii=False, sort_keys=True),
                    run_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM opinion_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert updated is not None
        return _opinion_run_from_row(updated)

    def load_opinion_comments(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT comment_id, source_url, author, content, likes, published_at
                FROM opinion_comments
                WHERE run_id = ? ORDER BY published_at DESC, comment_id
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "comment_id": str(row["comment_id"]),
                "source_url": str(row["source_url"]),
                "author": str(row["author"]),
                "content": str(row["content"]),
                "likes": int(row["likes"]),
                "published_at": _optional_text(row["published_at"]),
            }
            for row in rows
        ]

    def load_opinion_classifications(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, evidence_id, comment_id, classification_status,
                       stance, error_code
                FROM opinion_classifications
                WHERE run_id = ?
                ORDER BY evidence_id, comment_id
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "run_id": str(row["run_id"]),
                "evidence_id": int(row["evidence_id"]),
                "comment_id": str(row["comment_id"]),
                "classification_status": str(row["classification_status"]),
                "stance": _optional_text(row["stance"]),
                "error_code": _optional_text(row["error_code"]),
            }
            for row in rows
        ]

    def load_opinion_attempts(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT stage, attempt_no, started_at, finished_at, outcome,
                       error_code, error_summary
                FROM opinion_attempts
                WHERE run_id = ?
                ORDER BY stage, attempt_no
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "stage": str(row["stage"]),
                "attempt": int(row["attempt_no"]),
                "started_at": str(row["started_at"]),
                "finished_at": str(row["finished_at"]),
                "outcome": str(row["outcome"]),
                "error_code": _optional_text(row["error_code"]),
                "error_summary": _optional_text(row["error_summary"]),
            }
            for row in rows
        ]

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
            raise ResearchRunNotFoundError(f"不存在的研究运行：{run_id}")
        if run["status"] not in {"completed", "partial"}:
            raise ResearchRunNotReadyError(f"研究运行尚未产生可规划结果：{run_id}")
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
        *,
        opinion_plans: list[OpinionPlan] | None = None,
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

            for opinion_plan in opinion_plans or []:
                evidence = connection.execute(
                    """
                    SELECT snapshot_id FROM run_evidence
                    WHERE run_id = ? AND evidence_no = ? AND selected = 1
                    """,
                    (run_id, opinion_plan.evidence_id),
                ).fetchone()
                if evidence is None:
                    raise ValueError(f"舆情提示引用了不存在的已选证据：{opinion_plan.evidence_id}")
                opinion_plan_id = uuid4().hex
                connection.execute(
                    """
                    INSERT INTO opinion_plans (
                        id, planning_run_id, run_id, snapshot_id, evidence_no,
                        trigger_quote, question, platform, window_hours
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        opinion_plan_id,
                        planning_run_id,
                        run_id,
                        evidence["snapshot_id"],
                        opinion_plan.evidence_id,
                        opinion_plan.trigger_quote,
                        opinion_plan.question,
                        opinion_plan.platform,
                        opinion_plan.window_hours,
                    ),
                )
                for position, query in enumerate(opinion_plan.queries, start=1):
                    connection.execute(
                        """
                        INSERT INTO opinion_queries (plan_id, position, query, purpose)
                        VALUES (?, ?, ?, ?)
                        """,
                        (opinion_plan_id, position, query.query, query.purpose),
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
            SELECT id, payload_json FROM article_snapshots
            WHERE article_id = ? AND content_hash = ?
            """,
            (article.article_id, content_hash),
        ).fetchone()
        if existing is not None:
            existing_payload = json.loads(existing["payload_json"])
            article_payload = _article_payload(article)
            if any(
                existing_payload.get(field) != article_payload.get(field)
                for field in ("image_url", "content_blocks")
            ):
                connection.execute(
                    "UPDATE article_snapshots SET payload_json = ? WHERE id = ?",
                    (
                        json.dumps(article_payload, ensure_ascii=False, sort_keys=True),
                        existing["id"],
                    ),
                )
            if should_generate_article_summary(article.content):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO article_summaries (
                        snapshot_id, content_hash, status, summary, error_json,
                        attempts, created_at, updated_at, finished_at
                    ) VALUES (?, ?, 'pending', NULL, NULL, 0, ?, ?, NULL)
                    """,
                    (
                        existing["id"],
                        content_hash,
                        _format_datetime(project_now()),
                        _format_datetime(project_now()),
                    ),
                )
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
                id, article_id, content_hash, payload_json, collected_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                article.article_id,
                content_hash,
                json.dumps(_article_payload(article), ensure_ascii=False, sort_keys=True),
                _format_datetime(article.collected_at),
                created_at,
            ),
        )
        if should_generate_article_summary(article.content):
            connection.execute(
                """
                INSERT INTO article_summaries (
                    snapshot_id, content_hash, status, summary, error_json,
                    attempts, created_at, updated_at, finished_at
                ) VALUES (?, ?, 'pending', NULL, NULL, 0, ?, ?, NULL)
                """,
                (
                    snapshot_id,
                    content_hash,
                    created_at,
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
        connection.commit()
        return connection

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS information_agent_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS research_runs (
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

            CREATE TABLE IF NOT EXISTS articles (
                id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS article_snapshots (
                id TEXT PRIMARY KEY,
                article_id TEXT NOT NULL REFERENCES articles(id),
                content_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(article_id, content_hash)
            );

            CREATE TABLE IF NOT EXISTS article_summaries (
                snapshot_id TEXT PRIMARY KEY REFERENCES article_snapshots(id) ON DELETE CASCADE,
                content_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'running', 'completed', 'failed')
                ),
                summary TEXT,
                error_json TEXT,
                attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT,
                CHECK (
                    (status = 'completed' AND summary IS NOT NULL AND error_json IS NULL)
                    OR
                    (status IN ('pending', 'running') AND summary IS NULL)
                    OR
                    (status = 'failed' AND summary IS NULL AND error_json IS NOT NULL)
                )
            );

            CREATE TABLE IF NOT EXISTS reader_automation_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                dwell_seconds INTEGER NOT NULL CHECK (dwell_seconds > 0),
                read_ratio REAL NOT NULL CHECK (read_ratio > 0 AND read_ratio <= 1),
                agent_timeout_seconds INTEGER NOT NULL CHECK (agent_timeout_seconds > 0),
                max_searches INTEGER NOT NULL CHECK (max_searches > 0),
                max_attempts INTEGER NOT NULL CHECK (max_attempts > 0),
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS article_research_runs (
                id TEXT PRIMARY KEY REFERENCES research_runs(id) ON DELETE CASCADE,
                article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                snapshot_id TEXT NOT NULL REFERENCES article_snapshots(id) ON DELETE CASCADE,
                topic TEXT NOT NULL,
                mode TEXT NOT NULL CHECK (mode IN ('auto', 'manual')),
                status TEXT NOT NULL CHECK (
                    status IN ('queued', 'running', 'completed', 'partial', 'failed', 'cancelled')
                ),
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                agent_request_id TEXT NOT NULL UNIQUE,
                analysis_run_id TEXT,
                config_json TEXT NOT NULL,
                error_json TEXT
            );

            CREATE TABLE IF NOT EXISTS run_evidence (
                run_id TEXT NOT NULL REFERENCES research_runs(id),
                snapshot_id TEXT NOT NULL REFERENCES article_snapshots(id),
                evidence_no INTEGER,
                selected INTEGER NOT NULL CHECK (selected IN (0, 1)),
                PRIMARY KEY (run_id, snapshot_id),
                UNIQUE (run_id, evidence_no)
            );

            CREATE TABLE IF NOT EXISTS feeds (
                id TEXT PRIMARY KEY,
                feed_url TEXT NOT NULL UNIQUE,
                etag TEXT,
                last_modified TEXT,
                last_success_at TEXT
            );

            CREATE TABLE IF NOT EXISTS feed_entries (
                feed_id TEXT NOT NULL REFERENCES feeds(id),
                entry_key TEXT NOT NULL,
                article_url TEXT NOT NULL,
                article_id TEXT REFERENCES articles(id),
                updated_marker TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (feed_id, entry_key)
            );

            CREATE TABLE IF NOT EXISTS planning_runs (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES research_runs(id),
                status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed')),
                raw_response TEXT,
                errors_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                UNIQUE (id, run_id)
            );

            CREATE TABLE IF NOT EXISTS search_plans (
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

            CREATE TABLE IF NOT EXISTS search_queries (
                plan_id TEXT NOT NULL REFERENCES search_plans(id),
                position INTEGER NOT NULL,
                query TEXT NOT NULL,
                purpose TEXT NOT NULL,
                PRIMARY KEY (plan_id, position)
            );

            CREATE TABLE IF NOT EXISTS analysis_runs (
                id TEXT PRIMARY KEY,
                research_run_id TEXT NOT NULL REFERENCES research_runs(id),
                analysis_type TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'created', 'running', 'paused', 'interrupted',
                        'completed', 'partial', 'skipped', 'failed', 'cancelled'
                    )
                ),
                current_step_key TEXT,
                config_json TEXT NOT NULL,
                idempotency_key TEXT UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                errors_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS analysis_steps (
                id TEXT PRIMARY KEY,
                analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id),
                position INTEGER NOT NULL CHECK (position > 0),
                step_key TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'pending', 'running', 'succeeded', 'failed',
                        'interrupted', 'skipped', 'cancelled'
                    )
                ),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error_json TEXT,
                UNIQUE (analysis_run_id, position),
                UNIQUE (analysis_run_id, step_key)
            );

            CREATE TABLE IF NOT EXISTS analysis_attempts (
                id TEXT PRIMARY KEY,
                analysis_step_id TEXT NOT NULL REFERENCES analysis_steps(id),
                attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
                operation TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('started', 'succeeded', 'failed', 'interrupted', 'cancelled')
                ),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error_json TEXT,
                UNIQUE (analysis_step_id, attempt_no),
                UNIQUE (analysis_step_id, idempotency_key)
            );

            CREATE TABLE IF NOT EXISTS analysis_artifacts (
                id TEXT PRIMARY KEY,
                analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id),
                artifact_key TEXT NOT NULL,
                step_id TEXT REFERENCES analysis_steps(id),
                attempt_id TEXT REFERENCES analysis_attempts(id),
                kind TEXT NOT NULL,
                content_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (analysis_run_id, artifact_key)
            );

            CREATE TABLE IF NOT EXISTS feed_subscriptions (
                feed_id TEXT PRIMARY KEY REFERENCES feeds(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                site_url TEXT,
                subscribed_at TEXT NOT NULL,
                last_refreshed_at TEXT,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS reader_article_states (
                article_id TEXT PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
                is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1)),
                is_saved INTEGER NOT NULL DEFAULT 0 CHECK (is_saved IN (0, 1)),
                read_at TEXT,
                saved_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reader_deleted_articles (
                article_id TEXT PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
                deleted_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS opinion_plans (
                id TEXT PRIMARY KEY,
                planning_run_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                evidence_no INTEGER NOT NULL,
                trigger_quote TEXT NOT NULL,
                question TEXT NOT NULL,
                platform TEXT NOT NULL CHECK (platform = 'bilibili'),
                window_hours INTEGER NOT NULL CHECK (window_hours = 72),
                UNIQUE (planning_run_id, evidence_no),
                FOREIGN KEY (planning_run_id, run_id)
                    REFERENCES planning_runs(id, run_id),
                FOREIGN KEY (run_id, snapshot_id)
                    REFERENCES run_evidence(run_id, snapshot_id)
            );

            CREATE TABLE IF NOT EXISTS opinion_queries (
                plan_id TEXT NOT NULL REFERENCES opinion_plans(id),
                position INTEGER NOT NULL,
                query TEXT NOT NULL,
                purpose TEXT NOT NULL,
                PRIMARY KEY (plan_id, position)
            );

            CREATE TABLE IF NOT EXISTS opinion_runs (
                id TEXT PRIMARY KEY,
                article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                platform TEXT NOT NULL CHECK (platform = 'bilibili'),
                window_hours INTEGER NOT NULL CHECK (window_hours = 72),
                status TEXT NOT NULL CHECK (
                    status IN ('running', 'completed', 'partial', 'failed')
                ),
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                last_heartbeat_at TEXT,
                timeout_seconds REAL NOT NULL DEFAULT 300.0,
                article_snapshot_id TEXT,
                content_hash TEXT,
                requested_limit INTEGER,
                collected_count INTEGER NOT NULL DEFAULT 0,
                analyzed_count INTEGER NOT NULL DEFAULT 0,
                classification_total INTEGER NOT NULL DEFAULT 0,
                classified_count INTEGER NOT NULL DEFAULT 0,
                unclassified_count INTEGER NOT NULL DEFAULT 0,
                status_reason TEXT NOT NULL DEFAULT 'failed',
                errors_json TEXT NOT NULL,
                result_json TEXT
            );

            CREATE TABLE IF NOT EXISTS opinion_comments (
                run_id TEXT NOT NULL REFERENCES opinion_runs(id) ON DELETE CASCADE,
                comment_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                author TEXT NOT NULL,
                content TEXT NOT NULL,
                likes INTEGER NOT NULL CHECK (likes >= 0),
                published_at TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, comment_id)
            );

            CREATE TABLE IF NOT EXISTS opinion_classifications (
                run_id TEXT NOT NULL REFERENCES opinion_runs(id) ON DELETE CASCADE,
                evidence_id INTEGER NOT NULL CHECK (evidence_id > 0),
                comment_id TEXT NOT NULL,
                classification_status TEXT NOT NULL CHECK (
                    classification_status IN ('classified', 'unclassified')
                ),
                stance TEXT CHECK (
                    stance IS NULL OR stance IN ('support', 'oppose', 'mixed', 'unclear')
                ),
                error_code TEXT,
                PRIMARY KEY (run_id, evidence_id, comment_id),
                FOREIGN KEY (run_id, comment_id)
                    REFERENCES opinion_comments(run_id, comment_id),
                CHECK (
                    (classification_status = 'classified'
                     AND stance IS NOT NULL AND error_code IS NULL)
                    OR
                    (classification_status = 'unclassified'
                     AND stance IS NULL AND error_code IS NOT NULL)
                )
            );

            CREATE TABLE IF NOT EXISTS opinion_attempts (
                run_id TEXT NOT NULL REFERENCES opinion_runs(id) ON DELETE CASCADE,
                stage TEXT NOT NULL CHECK (
                    stage IN (
                        'opinion_planning', 'aid_resolution', 'comment_collection',
                        'opinion_analysis', 'classification'
                    )
                ),
                attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK (
                    outcome IN ('succeeded', 'failed', 'timed_out', 'skipped')
                ),
                error_code TEXT,
                error_summary TEXT,
                PRIMARY KEY (run_id, stage, attempt_no)
            );

            CREATE TABLE IF NOT EXISTS article_answer_requests (
                request_id TEXT PRIMARY KEY,
                article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                snapshot_id TEXT NOT NULL REFERENCES article_snapshots(id) ON DELETE CASCADE,
                content_hash TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT,
                status TEXT NOT NULL CHECK (status IN ('running', 'completed')),
                created_at TEXT NOT NULL,
                finished_at TEXT,
                CHECK (
                    (status = 'running' AND answer IS NULL AND finished_at IS NULL)
                    OR
                    (status = 'completed' AND answer IS NOT NULL AND finished_at IS NOT NULL)
                )
            );

            CREATE INDEX IF NOT EXISTS planning_runs_run_id_idx
                ON planning_runs(run_id);
            CREATE INDEX IF NOT EXISTS search_plans_planning_run_id_idx
                ON search_plans(planning_run_id);
            CREATE INDEX IF NOT EXISTS analysis_runs_research_run_id_idx
                ON analysis_runs(research_run_id);
            CREATE INDEX IF NOT EXISTS analysis_steps_run_id_idx
                ON analysis_steps(analysis_run_id, position);
            CREATE INDEX IF NOT EXISTS analysis_attempts_step_id_idx
                ON analysis_attempts(analysis_step_id, attempt_no);
            CREATE INDEX IF NOT EXISTS analysis_artifacts_run_id_idx
                ON analysis_artifacts(analysis_run_id, created_at);
            CREATE INDEX IF NOT EXISTS feed_entries_article_id_idx
                ON feed_entries(article_id);
            CREATE INDEX IF NOT EXISTS reader_article_states_updated_at_idx
                ON reader_article_states(updated_at);
            CREATE INDEX IF NOT EXISTS reader_deleted_articles_deleted_at_idx
                ON reader_deleted_articles(deleted_at);
            CREATE INDEX IF NOT EXISTS opinion_plans_planning_run_id_idx
                ON opinion_plans(planning_run_id);
            CREATE INDEX IF NOT EXISTS opinion_runs_article_id_idx
                ON opinion_runs(article_id, created_at);
            CREATE INDEX IF NOT EXISTS opinion_comments_run_id_idx
                ON opinion_comments(run_id, published_at);
            CREATE UNIQUE INDEX IF NOT EXISTS opinion_runs_active_target_idx
                ON opinion_runs(article_id, platform, window_hours)
                WHERE status = 'running';
            CREATE INDEX IF NOT EXISTS opinion_runs_identity_idx
                ON opinion_runs(
                    article_id, article_snapshot_id, content_hash,
                    platform, window_hours, requested_limit, created_at
                );
            CREATE INDEX IF NOT EXISTS opinion_classifications_run_id_idx
                ON opinion_classifications(run_id, evidence_id, comment_id);
            CREATE INDEX IF NOT EXISTS opinion_attempts_run_id_idx
                ON opinion_attempts(run_id, stage, attempt_no);
            CREATE INDEX IF NOT EXISTS article_answer_requests_article_idx
                ON article_answer_requests(article_id, snapshot_id, status, created_at);
            CREATE INDEX IF NOT EXISTS article_summaries_status_idx
                ON article_summaries(status, updated_at);
            CREATE INDEX IF NOT EXISTS article_research_runs_article_idx
                ON article_research_runs(article_id, created_at DESC, id DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS article_research_runs_auto_snapshot_idx
                ON article_research_runs(article_id, snapshot_id)
                WHERE mode = 'auto';
            CREATE UNIQUE INDEX IF NOT EXISTS article_research_runs_active_snapshot_idx
                ON article_research_runs(article_id, snapshot_id)
                WHERE status IN ('queued', 'running');
            """
        )
        migration_id = "20260824_remove_article_snapshot_normalizer_version"
        applied = connection.execute(
            "SELECT 1 FROM information_agent_migrations WHERE migration_id = ?",
            (migration_id,),
        ).fetchone()
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(article_snapshots)")
        }
        if applied is None and "normalizer_version" in columns:
            connection.execute("ALTER TABLE article_snapshots DROP COLUMN normalizer_version")
        if applied is None:
            connection.execute(
                "INSERT INTO information_agent_migrations (migration_id, applied_at) VALUES (?, ?)",
                (migration_id, _format_datetime(project_now())),
            )
        connection.execute(
            """
            INSERT OR IGNORE INTO reader_automation_settings (
                id, enabled, dwell_seconds, read_ratio, agent_timeout_seconds,
                max_searches, max_attempts, updated_at
            ) VALUES (1, 1, 15, 0.3333333333333333, 300, 3, 3, ?)
            """,
            (_format_datetime(project_now()),),
        )


def _article_payload(article: NormalizedArticle) -> dict[str, object]:
    payload = asdict(article)
    payload["content_type"] = article.content_type.value
    payload["categories"] = list(article.categories)
    payload["content_chunks"] = list(article.content_chunks)
    payload["content_blocks"] = content_blocks_to_payload(article.content_blocks)
    payload["processing_warnings"] = list(article.processing_warnings)
    payload["published_at"] = (
        _format_datetime(article.published_at) if article.published_at else None
    )
    payload["collected_at"] = _format_datetime(article.collected_at)
    return payload


def _opinion_comment_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("comments 必须是数组")
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("comments 中存在无效项目")
        comment_id = _opinion_required_text(item.get("comment_id"), "comment_id")
        if comment_id in seen_ids:
            raise ValueError("同一运行内 comment_id 不能重复")
        seen_ids.add(comment_id)
        source_url = _opinion_required_text(item.get("source_url"), "source_url")
        author = _opinion_required_text(item.get("author"), "author")
        content = _opinion_required_text(item.get("content"), "content")
        likes = item.get("likes")
        if type(likes) is not int or likes < 0:
            raise ValueError("likes 必须是非负整数")
        published_at = item.get("published_at")
        if published_at is not None:
            published_at = _opinion_required_text(published_at, "published_at")
        rows.append(
            {
                "comment_id": comment_id,
                "source_url": source_url,
                "author": author,
                "content": content,
                "likes": likes,
                "published_at": published_at,
            }
        )
    return rows


def _opinion_classification_rows(value: object, *, run_id: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("classifications 必须是数组")
    rows: list[dict[str, Any]] = []
    seen_relations: set[tuple[int, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("classifications 中存在无效项目")
        item_run_id = _opinion_required_text(item.get("run_id"), "run_id")
        if item_run_id != run_id:
            raise ValueError("分类关系不属于当前运行")
        evidence_id = item.get("evidence_id")
        if type(evidence_id) is not int or evidence_id <= 0:
            raise ValueError("evidence_id 必须是正整数")
        comment_id = _opinion_required_text(item.get("comment_id"), "comment_id")
        relation = (evidence_id, comment_id)
        if relation in seen_relations:
            raise ValueError("同一争议点-评论关系不能重复")
        seen_relations.add(relation)
        classification_status = _opinion_required_text(
            item.get("classification_status"), "classification_status"
        )
        if classification_status not in _OPINION_CLASSIFICATION_STATUSES:
            raise ValueError("classification_status 不是支持的分类状态")
        stance = item.get("stance")
        error_code = item.get("error_code")
        if classification_status == "classified":
            stance = _opinion_required_text(stance, "stance")
            if stance not in _OPINION_STANCES:
                raise ValueError("stance 不是支持的立场")
            if error_code is not None:
                raise ValueError("classified 分类不能包含 error_code")
        else:
            if stance is not None:
                raise ValueError("unclassified 分类不能包含 stance")
            error_code = _opinion_required_text(error_code, "error_code")
        rows.append(
            {
                "run_id": run_id,
                "evidence_id": evidence_id,
                "comment_id": comment_id,
                "classification_status": classification_status,
                "stance": stance,
                "error_code": error_code,
            }
        )
    return rows


def _opinion_attempt_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("attempts 必须是数组")
    rows: list[dict[str, Any]] = []
    seen_attempts: set[tuple[str, int]] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("attempts 中存在无效项目")
        stage = _opinion_required_text(item.get("stage"), "stage")
        if stage not in _OPINION_ATTEMPT_STAGES:
            raise ValueError("stage 不是支持的尝试阶段")
        attempt = item.get("attempt")
        if type(attempt) is not int or attempt < 1:
            raise ValueError("attempt 必须是正整数")
        key = (stage, attempt)
        if key in seen_attempts:
            raise ValueError("同一运行内尝试编号不能重复")
        seen_attempts.add(key)
        started_at = _opinion_required_text(item.get("started_at"), "started_at")
        finished_at = _opinion_required_text(item.get("finished_at"), "finished_at")
        outcome = _opinion_required_text(item.get("outcome"), "outcome")
        if outcome not in _OPINION_ATTEMPT_OUTCOMES:
            raise ValueError("outcome 不是支持的尝试结果")
        error_code = item.get("error_code")
        if error_code is not None:
            error_code = _opinion_required_text(error_code, "error_code")
        error_summary = item.get("error_summary")
        if error_summary is not None:
            error_summary = _opinion_required_text(error_summary, "error_summary")
        rows.append(
            {
                "stage": stage,
                "attempt": attempt,
                "started_at": started_at,
                "finished_at": finished_at,
                "outcome": outcome,
                "error_code": error_code,
                "error_summary": error_summary,
            }
        )
    return rows


def _opinion_required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value.strip()


def _opinion_error_rows(raw: object) -> list[dict[str, Any]]:
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            rows.append(dict(item))
        else:
            rows.append({"type": "OpinionError", "message": str(item)})
    return rows


def _opinion_nonnegative_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} 必须是非负整数")
    return value


def _opinion_optional_limit(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= 200:
        return None
    return value


def _default_opinion_status_reason(status: str) -> str:
    if status == "running":
        return "running"
    if status == "completed":
        return "completed"
    if status == "partial":
        return "partial_classification"
    return "failed"


def _opinion_run_from_row(row: sqlite3.Row) -> OpinionRunRecord:
    result_payload = (
        _load_json_object(row["result_json"], "opinion_runs.result_json")
        if row["result_json"] is not None
        else None
    )
    return OpinionRunRecord(
        id=str(row["id"]),
        article_id=str(row["article_id"]),
        platform=str(row["platform"]),
        window_hours=int(row["window_hours"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        started_at=_optional_text(row["started_at"]),
        finished_at=_optional_text(row["finished_at"]),
        errors=tuple(_opinion_error_rows(row["errors_json"])),
        result_payload=result_payload,
        last_heartbeat_at=_optional_text(row["last_heartbeat_at"]),
        timeout_seconds=float(row["timeout_seconds"]),
        article_snapshot_id=_optional_text(row["article_snapshot_id"]),
        content_hash=_optional_text(row["content_hash"]),
        requested_limit=_opinion_optional_limit(row["requested_limit"]),
        collected_count=int(row["collected_count"]),
        analyzed_count=int(row["analyzed_count"]),
        classification_total=int(row["classification_total"]),
        classified_count=int(row["classified_count"]),
        unclassified_count=int(row["unclassified_count"]),
        status_reason=str(row["status_reason"]),
        attempts=tuple(
            item for item in (result_payload or {}).get("attempts", []) if isinstance(item, dict)
        ),
    )


def _opinion_heartbeat(row: sqlite3.Row) -> datetime:
    value = row["last_heartbeat_at"] or row["started_at"] or row["created_at"]
    try:
        return _required_datetime(value)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=project_now().tzinfo)


def _opinion_timeout_seconds(row: sqlite3.Row) -> float:
    value = row["timeout_seconds"]
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 300.0
    if not math.isfinite(parsed) or parsed <= 0:
        return 300.0
    return parsed


def _mark_stale_opinion_run(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    now: datetime,
) -> None:
    finished_at = _format_datetime(now)
    stale_error = {
        "code": "stale_running",
        "stage": "persistence",
        "message": "运行心跳超过总时限和宽限期，已收口为遗留运行",
        "retryable": False,
        "attempt": None,
    }
    result_payload = (
        _load_json_object(row["result_json"], "opinion_runs.result_json")
        if row["result_json"] is not None
        else {}
    )
    result_payload.update(
        {
            "status": "failed",
            "status_reason": "stale_running",
            "finished_at": finished_at,
            "last_heartbeat_at": finished_at,
            "errors": [stale_error],
        }
    )
    connection.execute(
        """
        UPDATE opinion_runs
        SET status = 'failed', finished_at = ?, last_heartbeat_at = ?,
            status_reason = 'stale_running', errors_json = ?, result_json = ?
        WHERE id = ? AND status = 'running'
        """,
        (
            finished_at,
            finished_at,
            json.dumps([stale_error], ensure_ascii=False, sort_keys=True),
            json.dumps(result_payload, ensure_ascii=False, sort_keys=True),
            row["id"],
        ),
    )


def _subscription_from_row(row: sqlite3.Row) -> FeedSubscription:
    return FeedSubscription(
        feed_id=str(row["feed_id"]),
        feed_url=str(row["feed_url"]),
        title=str(row["title"]),
        site_url=_optional_text(row["site_url"]),
        subscribed_at=str(row["subscribed_at"]),
        last_refreshed_at=_optional_text(row["last_refreshed_at"]),
        last_error=_optional_text(row["last_error"]),
        article_count=int(row["article_count"]),
        unread_count=int(row["unread_count"]),
    )


def _reader_article_state_from_row(row: sqlite3.Row) -> ReaderArticleState:
    return ReaderArticleState(
        article_id=str(row["article_id"]),
        is_read=bool(row["is_read"]),
        is_saved=bool(row["is_saved"]),
        read_at=_optional_text(row["read_at"]),
        saved_at=_optional_text(row["saved_at"]),
        updated_at=str(row["updated_at"]),
    )


def _reader_article_from_row(row: sqlite3.Row) -> ReaderArticle:
    article = _article_from_payload(json.loads(row["payload_json"]))
    summary = _optional_text(row["summary"])
    summary_status = str(row["summary_status"] or "pending")
    if summary is None and not should_generate_article_summary(article.content):
        summary_status = "skipped"
    return ReaderArticle(
        feed_id=str(row["feed_id"]),
        article=article,
        is_read=bool(row["is_read"]),
        is_saved=bool(row["is_saved"]),
        snapshot_id=str(row["snapshot_id"]),
        content_hash=str(row["content_hash"]),
        summary=summary,
        summary_status=summary_status,
        summary_error=_summary_error_message(row["summary_error"]),
        research_status=str(row["research_status"] or "none"),
        research_mode=_optional_text(row["research_mode"]),
    )


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
    content = str(payload["content"])
    return NormalizedArticle(
        article_id=str(payload["article_id"]),
        source_url=str(payload["source_url"]),
        title=str(payload["title"]),
        content=content,
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
        image_url=_optional_text(payload.get("image_url")),
        content_blocks=content_blocks_from_payload(payload.get("content_blocks"), content),
    )
