from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..collection import fetch_feed, normalize_evidence
from ..contracts import CollectionReport, Evidence, RunStatus
from ..processing import filter_evidence

Collector = Callable[[str, float], list[Evidence]]


@dataclass(slots=True)
class _CollectionExecution:
    report: CollectionReport
    remaining_seconds: float


def collect(
    topic: str,
    feeds: list[str],
    *,
    timeout_seconds: float = 60,
    limit: int = 20,
    collector: Collector = fetch_feed,
) -> CollectionReport:
    return _execute_collection(
        topic,
        feeds,
        timeout_seconds=timeout_seconds,
        limit=limit,
        collector=collector,
    ).report


def _execute_collection(
    topic: str,
    feeds: list[str],
    *,
    timeout_seconds: float,
    limit: int,
    collector: Collector,
) -> _CollectionExecution:
    _validate_input(topic, feeds, timeout_seconds, limit)
    deadline = time.monotonic() + timeout_seconds
    errors: list[str] = []
    collected: list[Evidence] = []
    successful_sources = 0

    for feed_url in feeds:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append("任务在完成 RSS 采集前超时")
            break
        try:
            collected.extend(collector(feed_url, min(15.0, remaining)))
            successful_sources += 1
        except Exception as exc:
            errors.append(f"{feed_url}：{exc}")

    articles = filter_evidence(topic, normalize_evidence(collected), limit=limit)
    status = _collection_status(errors, successful_sources)
    report = CollectionReport(topic, status, articles, errors)
    return _CollectionExecution(report, max(0.0, deadline - time.monotonic()))


def _collection_status(errors: list[str], successful_sources: int) -> RunStatus:
    if not errors:
        return RunStatus.COMPLETED
    if successful_sources:
        return RunStatus.PARTIAL
    return RunStatus.FAILED


def _validate_input(topic: str, feeds: list[str], timeout_seconds: float, limit: int) -> None:
    if not topic.strip():
        raise ValueError("研究主题不能为空")
    if not feeds:
        raise ValueError("至少需要一个 RSS 地址")
    if timeout_seconds <= 0:
        raise ValueError("任务时限必须大于 0 秒")
    if limit <= 0:
        raise ValueError("证据数量上限必须大于 0")
