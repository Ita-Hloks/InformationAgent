from __future__ import annotations

from pathlib import Path

from ..collection import fetch_feed
from ..storage import PersistedCollection, SQLiteCollectionStore, default_database_path
from .collection import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_WORKERS,
    DEFAULT_SOURCE_TIMEOUT_SECONDS,
    Collector,
    _execute_collection_with_details,
)
from .execution import ExecutionBudget


def ingest(
    topic: str,
    feeds: list[str],
    *,
    database_path: str | Path | None = None,
    collector: Collector = fetch_feed,
    timeout_seconds: float = 60,
    limit: int = 20,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    source_timeout_seconds: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
) -> PersistedCollection:
    """执行粗处理并保存结果；本入口不触发任何 LLM 调用。"""

    store = SQLiteCollectionStore(database_path or default_database_path())
    run_id = store.start_run(topic, feeds)
    try:
        execution = _execute_collection_with_details(
            topic,
            feeds,
            budget=ExecutionBudget.start(timeout_seconds),
            limit=limit,
            collector=collector,
            max_workers=max_workers,
            max_attempts=max_attempts,
            source_timeout_seconds=source_timeout_seconds,
        )
        store.complete_run(run_id, execution.report, execution.normalized_articles)
    except Exception as exc:
        store.fail_run(run_id, exc)
        raise
    return PersistedCollection(run_id=run_id, report=execution.report)
