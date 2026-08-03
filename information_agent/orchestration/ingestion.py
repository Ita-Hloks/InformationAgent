from __future__ import annotations

from pathlib import Path
from threading import Lock

from ..collection import FeedFetchResult, fetch_feed, fetch_feed_with_cache
from ..common import normalize_url
from ..selection import RelevanceSelector
from ..storage import (
    FeedObservation,
    PersistedCollection,
    SQLiteCollectionStore,
    default_database_path,
)
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
    timeout_seconds: float = 300,
    limit: int = 20,
    relevance_selector: RelevanceSelector | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    source_timeout_seconds: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
) -> PersistedCollection:
    """执行 RSS 粗处理、LLM 语义筛选并保存结果。"""

    store = SQLiteCollectionStore(database_path or default_database_path())
    run_id = store.start_run(topic, feeds)
    observations: list[FeedObservation] = []
    observation_lock = Lock()

    def cached_collector(feed_url: str, timeout: float):
        normalized_feed_url = normalize_url(feed_url)
        if normalized_feed_url is None:
            raise ValueError("RSS 地址必须使用 http 或 https")
        state = store.feed_state(normalized_feed_url)
        result = fetch_feed_with_cache(
            normalized_feed_url,
            timeout,
            etag=state.etag,
            last_modified=state.last_modified,
        )
        new_entries = store.new_feed_entries(state, result.entries)
        with observation_lock:
            observations.append(_feed_observation(state, result, new_entries))
        return new_entries

    active_collector = cached_collector if collector is fetch_feed else collector
    try:
        execution = _execute_collection_with_details(
            topic,
            feeds,
            budget=ExecutionBudget.start(timeout_seconds),
            limit=limit,
            collector=active_collector,
            relevance_selector=relevance_selector,
            max_workers=max_workers,
            max_attempts=max_attempts,
            source_timeout_seconds=source_timeout_seconds,
        )
        store.complete_run(
            run_id,
            execution.report,
            execution.normalized_articles,
            feed_observations=observations,
        )
    except Exception as exc:
        store.fail_run(run_id, exc)
        raise
    return PersistedCollection(run_id=run_id, report=execution.report)


def _feed_observation(
    state,
    result: FeedFetchResult,
    new_entries,
) -> FeedObservation:
    return FeedObservation(
        state=state,
        etag=result.etag,
        last_modified=result.last_modified,
        not_modified=result.not_modified,
        new_entries=new_entries,
    )
