from __future__ import annotations

import sqlite3

from ..contracts import project_now
from .common import _format_datetime


def migrate_analysis_schema(connection: sqlite3.Connection) -> None:
    applied_versions = {
        row[0] for row in connection.execute("SELECT version FROM schema_migrations")
    }
    if 5 not in applied_versions:
        connection.executescript(
            """
            CREATE TABLE analysis_runs (
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

            CREATE TABLE analysis_steps (
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

            CREATE TABLE analysis_attempts (
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

            CREATE TABLE analysis_artifacts (
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

            CREATE INDEX analysis_runs_research_run_id_idx
                ON analysis_runs(research_run_id);
            CREATE INDEX analysis_steps_run_id_idx
                ON analysis_steps(analysis_run_id, position);
            CREATE INDEX analysis_attempts_step_id_idx
                ON analysis_attempts(analysis_step_id, attempt_no);
            CREATE INDEX analysis_artifacts_run_id_idx
                ON analysis_artifacts(analysis_run_id, created_at);
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (5, _format_datetime(project_now())),
        )
