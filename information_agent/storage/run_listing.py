from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .common import _parse_datetime, _required_datetime
from .models import ResearchRunSummary

RESEARCH_RUN_STATUSES = ("collecting", "completed", "partial", "failed")


class ResearchRunListingMixin:
    """Read aggregate research-run metadata from a stable SQLite snapshot."""

    database_path: Path

    def list_runs(
        self,
        *,
        limit: int,
        status: str | None = None,
        mode: str | None = None,
    ) -> list[ResearchRunSummary]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if status is not None and status not in RESEARCH_RUN_STATUSES:
            raise ValueError(f"unknown research run status: {status}")
        if mode is not None and mode not in {"auto", "manual"}:
            raise ValueError(f"unknown research run mode: {mode}")

        query = """
            SELECT runs.id AS run_id, runs.topic, runs.status,
                runs.created_at AS started_at, runs.finished_at,
                json_array_length(runs.feeds_json) AS feed_count,
                COUNT(evidence.snapshot_id) AS snapshot_count,
                COALESCE(SUM(CASE WHEN evidence.selected = 1 THEN 1 ELSE 0 END), 0)
                    AS selected_evidence_count,
                json_array_length(runs.errors_json) AS collection_error_count,
                COALESCE(article_research.mode, 'manual') AS mode
            FROM research_runs AS runs
            LEFT JOIN run_evidence AS evidence ON evidence.run_id = runs.id
            LEFT JOIN article_research_runs AS article_research
                ON article_research.id = runs.id
        """
        parameters: list[object] = []
        if status is not None:
            query += " WHERE runs.status = ?"
            parameters.append(status)
        if mode is not None:
            query += " AND " if status is not None else " WHERE "
            query += "COALESCE(article_research.mode, 'manual') = ?"
            parameters.append(mode)
        query += """
            GROUP BY runs.id
            ORDER BY runs.created_at DESC, runs.id ASC
            LIMIT ?
        """
        parameters.append(limit)

        with _read_only_snapshot_connection(self.database_path) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            ResearchRunSummary(
                run_id=str(row["run_id"]),
                topic=str(row["topic"]),
                status=str(row["status"]),
                started_at=_required_datetime(row["started_at"]),
                finished_at=_parse_datetime(row["finished_at"]),
                feed_count=int(row["feed_count"]),
                snapshot_count=int(row["snapshot_count"]),
                selected_evidence_count=int(row["selected_evidence_count"]),
                collection_error_count=int(row["collection_error_count"]),
                mode=str(row["mode"]),
            )
            for row in rows
        ]


@contextmanager
def _read_only_snapshot_connection(database_path: Path):
    """Open one SQLite-managed read-only snapshot for inspection."""
    if not database_path.is_file():
        raise FileNotFoundError(f"database does not exist: {database_path}")
    database_uri = f"{database_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(database_uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()
