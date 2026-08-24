from __future__ import annotations

import threading

import pytest

from information_agent.orchestration.summary_tasks import SummaryTaskManager
from information_agent.reader.summary import parse_article_summary
from information_agent.storage import ArticleSummaryJob


def test_parse_article_summary_accepts_exact_summary_payload() -> None:
    assert (
        parse_article_summary('{"summary":" 文章解释了测试条件。结果显示主要差异来自样本范围。 "}')
        == "文章解释了测试条件。结果显示主要差异来自样本范围。"
    )


@pytest.mark.parametrize(
    "raw, message",
    [
        ('{"summary":"第一句。第二句。","reasoning":"内部过程"}', "只包含 summary"),
        ('{"summary":"只有一句。"}', "2 至 3 个句子"),
        ('{"summary":"第一句。第二句。第三句。第四句。"}', "2 至 3 个句子"),
        ('{"summary":"' + "甲" * 179 + '。乙。"}', "不能超过 180"),
    ],
)
def test_parse_article_summary_rejects_invalid_contract(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_article_summary(raw)


def test_summary_manager_processes_backlog_in_store_order() -> None:
    store = _SummaryStore([_summary_job("newer"), _summary_job("older")])
    processed: list[str] = []
    manager = SummaryTaskManager(
        store,
        runner=lambda job: _record_summary(job, processed),
    )
    try:
        assert manager.wait_until_idle(1)
    finally:
        manager.shutdown()

    assert processed == ["newer", "older"]
    assert store.completed == ["newer-snapshot", "older-snapshot"]


def test_submit_passes_current_snapshot_as_next_claim_preference() -> None:
    store = _SummaryStore([_summary_job("blocker"), _summary_job("newer"), _summary_job("older")])
    started = threading.Event()
    release = threading.Event()
    processed: list[str] = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    def runner(job: ArticleSummaryJob) -> str:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            if job.snapshot_id == "blocker-snapshot":
                started.set()
                assert release.wait(1)
            processed.append(job.article_id)
            return f"{job.article_id}摘要第一句。摘要第二句。"
        finally:
            with lock:
                active -= 1

    manager = SummaryTaskManager(store, runner=runner)
    try:
        assert started.wait(1)
        manager.submit("newer", "newer-snapshot")
        manager.submit("older", "older-snapshot")
        release.set()
        assert manager.wait_until_idle(1)
    finally:
        release.set()
        manager.shutdown()

    assert processed == ["blocker", "older", "newer"]
    assert store.claim_preferences[:3] == [None, "older-snapshot", "newer-snapshot"]
    assert max_active == 1


def test_summary_manager_persists_runner_failure_and_retries() -> None:
    store = _SummaryStore([_summary_job("failed")])
    attempts = 0

    def runner(_: ArticleSummaryJob) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("摘要服务不可用")
        return "重试生成第一句。重试生成第二句。"

    manager = SummaryTaskManager(store, runner=runner)
    try:
        assert manager.wait_until_idle(1)
        assert len(store.failed) == 1
        assert isinstance(store.failed[0], RuntimeError)

        assert manager.retry("failed") == "failed-snapshot"
        assert manager.wait_until_idle(1)
    finally:
        manager.shutdown()

    assert attempts == 2
    assert store.retried == ["failed"]
    assert store.completed == ["failed-snapshot"]


class _SummaryStore:
    def __init__(self, jobs: list[ArticleSummaryJob]) -> None:
        self._lock = threading.Lock()
        self._jobs = {job.snapshot_id: job for job in jobs}
        self._order = [job.snapshot_id for job in jobs]
        self._statuses = {job.snapshot_id: "pending" for job in jobs}
        self.ensure_calls = 0
        self.claim_preferences: list[str | None] = []
        self.completed: list[str] = []
        self.failed: list[Exception] = []
        self.retried: list[str] = []

    def ensure_pending_summaries(self) -> int:
        with self._lock:
            self.ensure_calls += 1
        return 0

    def claim_summary_job(
        self,
        *,
        preferred_snapshot_id: str | None = None,
    ) -> ArticleSummaryJob | None:
        with self._lock:
            self.claim_preferences.append(preferred_snapshot_id)
            candidates = list(self._order)
            if preferred_snapshot_id in candidates:
                candidates.remove(preferred_snapshot_id)
                candidates.insert(0, preferred_snapshot_id)
            for snapshot_id in candidates:
                if self._statuses[snapshot_id] != "pending":
                    continue
                self._statuses[snapshot_id] = "running"
                return self._jobs[snapshot_id]
        return None

    def complete_summary_job(self, snapshot_id: str, summary: str) -> None:
        assert summary
        with self._lock:
            self._statuses[snapshot_id] = "completed"
            self.completed.append(snapshot_id)

    def fail_summary_job(self, snapshot_id: str, error: Exception) -> None:
        with self._lock:
            self._statuses[snapshot_id] = "failed"
            self.failed.append(error)

    def retry_summary(self, article_id: str) -> str:
        snapshot_id = f"{article_id}-snapshot"
        with self._lock:
            self._statuses[snapshot_id] = "pending"
            self.retried.append(article_id)
        return snapshot_id


def _summary_job(article_id: str) -> ArticleSummaryJob:
    return ArticleSummaryJob(
        snapshot_id=f"{article_id}-snapshot",
        article_id=article_id,
        title=f"文章 {article_id}",
        content="正文第一段。正文第二段。",
        content_hash=f"{article_id}-hash",
        status="pending",
        attempts=0,
    )


def _record_summary(job: ArticleSummaryJob, processed: list[str]) -> str:
    processed.append(job.article_id)
    return "摘要第一句。摘要第二句。"
