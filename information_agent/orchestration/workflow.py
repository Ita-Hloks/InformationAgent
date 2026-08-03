from __future__ import annotations

from ..collection import fetch_feed
from ..common import DEFAULT_LLM_TIMEOUT_SECONDS
from ..contracts import Report
from ..selection import RelevanceSelector
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
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    limit: int = 20,
    collector: Collector = fetch_feed,
    relevance_selector: RelevanceSelector | None = None,
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
        relevance_selector=relevance_selector,
        analyst=analyst,
        max_workers=max_workers,
        max_attempts=max_attempts,
        source_timeout_seconds=source_timeout_seconds,
    ).run()
