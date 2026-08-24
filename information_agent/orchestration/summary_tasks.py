from __future__ import annotations

import threading
from collections import deque
from typing import Protocol

from ..reader.summary import ArticleSummaryAssistant
from ..storage import ArticleSummaryJob, SQLiteCollectionStore


class SummaryRunner(Protocol):
    def __call__(self, job: ArticleSummaryJob) -> str: ...


class SummaryStore(Protocol):
    def ensure_pending_summaries(self) -> int: ...

    def claim_summary_job(
        self,
        *,
        preferred_snapshot_id: str | None = None,
    ) -> ArticleSummaryJob | None: ...

    def complete_summary_job(self, snapshot_id: str, summary: str) -> None: ...

    def fail_summary_job(self, snapshot_id: str, error: Exception) -> None: ...

    def retry_summary(self, article_id: str) -> str: ...


class SummaryTaskManager:
    """串行处理持久化的文章摘要任务。"""

    def __init__(
        self,
        store: SQLiteCollectionStore | SummaryStore,
        *,
        runner: SummaryRunner | None = None,
    ) -> None:
        self._store = store
        self._runner = runner or ArticleSummaryAssistant().summarize
        self._condition = threading.Condition(threading.RLock())
        self._preferred_snapshots: deque[str] = deque()
        self._idle = threading.Event()
        self._wake_requested = True
        self._closed = False
        self._store.ensure_pending_summaries()
        self._thread = threading.Thread(
            target=self._worker,
            name="information-agent-summary",
            daemon=True,
        )
        self._thread.start()

    def submit(self, article_id: str, snapshot_id: str) -> None:
        _, normalized_snapshot_id = _task_ids(article_id, snapshot_id)
        self._store.ensure_pending_summaries()
        self._promote(normalized_snapshot_id)

    def wake(self) -> None:
        self._store.ensure_pending_summaries()
        with self._condition:
            self._idle.clear()
            self._wake_requested = True
            self._condition.notify()

    def retry(self, article_id: str) -> str:
        normalized_article_id = article_id.strip()
        if not normalized_article_id:
            raise ValueError("文章 ID 不能为空")
        snapshot_id = self._store.retry_summary(normalized_article_id)
        self._promote(snapshot_id)
        return snapshot_id

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def shutdown(self, *, wait: bool = True) -> None:
        if wait:
            self._idle.wait()
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if wait:
            self._thread.join()

    def _promote(self, snapshot_id: str) -> None:
        with self._condition:
            try:
                self._preferred_snapshots.remove(snapshot_id)
            except ValueError:
                pass
            self._preferred_snapshots.appendleft(snapshot_id)
            self._idle.clear()
            self._wake_requested = True
            self._condition.notify()

    def _worker(self) -> None:
        while True:
            with self._condition:
                while not self._wake_requested and not self._closed:
                    self._idle.set()
                    self._condition.wait()
                if self._closed:
                    self._idle.set()
                    return
                self._wake_requested = False
                self._idle.clear()

            self._drain()

    def _drain(self) -> None:
        while True:
            job = self._store.claim_summary_job(
                preferred_snapshot_id=self._next_preferred_snapshot()
            )
            if job is None:
                return
            try:
                summary = self._runner(job)
            except Exception as exc:
                self._store.fail_summary_job(job.snapshot_id, exc)
                continue
            self._store.complete_summary_job(job.snapshot_id, summary)

    def _next_preferred_snapshot(self) -> str | None:
        with self._condition:
            if not self._preferred_snapshots:
                return None
            return self._preferred_snapshots.popleft()


def _task_ids(article_id: str, snapshot_id: str) -> tuple[str, str]:
    normalized_article_id = article_id.strip()
    normalized_snapshot_id = snapshot_id.strip()
    if not normalized_article_id:
        raise ValueError("文章 ID 不能为空")
    if not normalized_snapshot_id:
        raise ValueError("正文快照 ID 不能为空")
    return normalized_article_id, normalized_snapshot_id
