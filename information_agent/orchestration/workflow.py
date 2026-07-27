from __future__ import annotations

from ..collection import fetch_feed
from ..contracts import Report
from .collection import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_WORKERS,
    DEFAULT_SOURCE_TIMEOUT_SECONDS,
    Collector,
)
from .runner import Analyst, WorkflowRunner


def run(
    topic: str,
    feeds: list[str],
    *,
    timeout_seconds: float = 60,
    limit: int = 20,
    collector: Collector = fetch_feed,
    analyst: Analyst | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    source_timeout_seconds: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
) -> Report:
    return WorkflowRunner(
        topic=topic,
        feeds=feeds,
        timeout_seconds=timeout_seconds,
        limit=limit,
        collector=collector,
        analyst=analyst,
        max_workers=max_workers,
        max_attempts=max_attempts,
        source_timeout_seconds=source_timeout_seconds,
    ).run()
