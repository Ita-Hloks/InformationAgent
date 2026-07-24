from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.error import HTTPError, URLError

import aiohttp

from ..collection import fetch_feed, fetch_feed_async, normalize_evidence
from ..contracts import CollectionReport, Evidence, RunStatus
from ..processing import filter_evidence

Collector = Callable[[str, float], list[Evidence]]
SourceResult = tuple[str, list[Evidence], Exception | None]
DEFAULT_MAX_WORKERS = 6
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_SOURCE_TIMEOUT_SECONDS = 15.0
INITIAL_RETRY_DELAY_SECONDS = 0.1


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
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    source_timeout_seconds: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
) -> CollectionReport:
    return _execute_collection(
        topic,
        feeds,
        timeout_seconds=timeout_seconds,
        limit=limit,
        collector=collector,
        max_workers=max_workers,
        max_attempts=max_attempts,
        source_timeout_seconds=source_timeout_seconds,
    ).report


def _execute_collection(
    topic: str,
    feeds: list[str],
    *,
    timeout_seconds: float,
    limit: int,
    collector: Collector,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    source_timeout_seconds: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
) -> _CollectionExecution:
    _validate_input(
        topic,
        feeds,
        timeout_seconds,
        limit,
        max_workers,
        max_attempts,
        source_timeout_seconds,
    )
    return asyncio.run(
        _execute_collection_async(
            topic,
            feeds,
            timeout_seconds=timeout_seconds,
            limit=limit,
            collector=collector,
            max_workers=max_workers,
            max_attempts=max_attempts,
            source_timeout_seconds=source_timeout_seconds,
        )
    )


async def _execute_collection_async(
    topic: str,
    feeds: list[str],
    *,
    timeout_seconds: float,
    limit: int,
    collector: Collector,
    max_workers: int,
    max_attempts: int,
    source_timeout_seconds: float,
) -> _CollectionExecution:
    deadline = time.monotonic() + timeout_seconds
    semaphore = asyncio.Semaphore(max_workers)
    timeout_error = TimeoutError("任务在完成 RSS 采集前超时")

    async with aiohttp.ClientSession() as session:
        tasks = {
            asyncio.create_task(
                _collect_source(
                    feed_url,
                    deadline=deadline,
                    collector=collector,
                    session=session,
                    semaphore=semaphore,
                    max_attempts=max_attempts,
                    source_timeout_seconds=source_timeout_seconds,
                )
            ): feed_url
            for feed_url in feeds
        }
        source_results = await _await_source_results(tasks, deadline, timeout_error)

    errors: list[str] = []
    collected: list[Evidence] = []
    successful_sources = 0
    for feed_url, source_items, error in source_results:
        if error is not None:
            errors.append(f"{feed_url}：{error}")
            continue
        collected.extend(source_items)
        successful_sources += 1

    articles = filter_evidence(topic, normalize_evidence(collected), limit=limit)
    status = _collection_status(errors, successful_sources)
    report = CollectionReport(topic, status, articles, errors)
    return _CollectionExecution(report, max(0.0, deadline - time.monotonic()))


async def _collect_source(
    feed_url: str,
    *,
    deadline: float,
    collector: Collector,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    max_attempts: int,
    source_timeout_seconds: float,
) -> SourceResult:
    async with semaphore:
        for attempt in range(max_attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return feed_url, [], TimeoutError("任务在完成 RSS 采集前超时")
            try:
                request_timeout = min(source_timeout_seconds, remaining)
                if collector is fetch_feed:
                    items = await fetch_feed_async(
                        feed_url,
                        request_timeout,
                        session=session,
                    )
                else:
                    # A synchronous injected collector cannot be force-cancelled.
                    items = await asyncio.to_thread(collector, feed_url, request_timeout)
                return feed_url, items, None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not _is_retryable_error(exc) or attempt + 1 == max_attempts:
                    return feed_url, [], exc
                retry_delay = min(
                    INITIAL_RETRY_DELAY_SECONDS * (2**attempt),
                    max(0.0, deadline - time.monotonic()),
                )
                if retry_delay <= 0:
                    return feed_url, [], TimeoutError("任务在完成 RSS 采集前超时")
                await asyncio.sleep(retry_delay)
    raise AssertionError("重试循环必须返回结果")


async def _await_source_results(
    tasks: dict[asyncio.Task[SourceResult], str],
    deadline: float,
    timeout_error: TimeoutError,
) -> list[SourceResult]:
    """Return completed source results and turn unfinished tasks into timeout errors."""
    try:
        async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
            return list(await asyncio.gather(*tasks))
    except TimeoutError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    results: list[SourceResult] = []
    for task, feed_url in tasks.items():
        if task.cancelled():
            results.append((feed_url, [], timeout_error))
            continue
        exception = task.exception()
        if exception is not None:
            results.append((feed_url, [], exception))
            continue
        results.append(task.result())
    return results


def _collection_status(errors: list[str], successful_sources: int) -> RunStatus:
    if not errors:
        return RunStatus.COMPLETED
    if successful_sources:
        return RunStatus.PARTIAL
    return RunStatus.FAILED


def _is_retryable_error(error: Exception) -> bool:
    if isinstance(error, TimeoutError):
        return True
    if isinstance(error, HTTPError):
        return error.code in {408, 429} or 500 <= error.code < 600
    if isinstance(error, URLError):
        return True
    if isinstance(error, aiohttp.ClientResponseError):
        return error.status in {408, 429} or 500 <= error.status < 600
    if isinstance(error, aiohttp.ClientError):
        return True
    return False


def _validate_input(
    topic: str,
    feeds: list[str],
    timeout_seconds: float,
    limit: int,
    max_workers: int,
    max_attempts: int,
    source_timeout_seconds: float,
) -> None:
    if not topic.strip():
        raise ValueError("研究主题不能为空")
    if not feeds:
        raise ValueError("至少需要一个 RSS 地址")
    if timeout_seconds <= 0:
        raise ValueError("任务时限必须大于 0 秒")
    if limit <= 0:
        raise ValueError("证据数量上限必须大于 0")
    if max_workers <= 0:
        raise ValueError("并发数量必须大于 0")
    if max_attempts <= 0:
        raise ValueError("单个来源的尝试次数必须大于 0")
    if source_timeout_seconds <= 0:
        raise ValueError("单个来源的请求时限必须大于 0 秒")
