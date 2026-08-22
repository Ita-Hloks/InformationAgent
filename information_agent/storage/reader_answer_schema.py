from __future__ import annotations

import sqlite3

from ..contracts import project_now
from .common import _format_datetime


def migrate_reader_answer_schema(connection: sqlite3.Connection) -> None:
    applied_versions = {
        row[0] for row in connection.execute("SELECT version FROM schema_migrations")
    }
    articles_exist = (
        connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'articles'"
        ).fetchone()[0]
        == 1
    )
    if 12 in applied_versions or not articles_exist:
        return
    connection.executescript(
        """
        CREATE TABLE article_answer_requests (
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

        CREATE INDEX article_answer_requests_article_idx
            ON article_answer_requests(article_id, snapshot_id, status, created_at);
        """
    )
    connection.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (12, _format_datetime(project_now())),
    )
