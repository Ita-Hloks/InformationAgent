from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Event, Lock

from information_agent.collection import FeedFetchResult, RawFeedEntry
from information_agent.contracts import PROJECT_TIMEZONE, ContentType
from information_agent.orchestration.article_research_tasks import ArticleResearchTaskManager
from information_agent.orchestration.summary_tasks import SummaryTaskManager
from information_agent.reader import ReaderService


def _fetcher(feed_url: str, _timeout: float, **_: object) -> FeedFetchResult:
    return FeedFetchResult(
        feed_url=feed_url,
        etag='"v1"',
        last_modified=None,
        entries=[
            RawFeedEntry(
                source_url=f"https://example.com/article-{index}",
                title="自动研究文章" if index == 1 else f"自动研究文章 {index}",
                content=(
                    f"这是第 {index} 篇用于摘要和自动研究的正文内容，长度足以通过文章规范化边界。"
                    + "补充正文内容。" * 50
                ),
                feed_url=feed_url,
                content_type=ContentType.RSS_CONTENT,
                published_at=datetime(2026, 8, 24, tzinfo=PROJECT_TIMEZONE),
            )
            for index in range(1, 4)
        ],
    )


def _service(tmp_path: Path) -> ReaderService:
    service = ReaderService(tmp_path / "reader.db", fetcher=_fetcher)
    service.subscribe("https://example.com/rss.xml")
    return service


def _short_fetcher(feed_url: str, _timeout: float, **_: object) -> FeedFetchResult:
    return FeedFetchResult(
        feed_url=feed_url,
        etag='"v1"',
        last_modified=None,
        entries=[
            RawFeedEntry(
                source_url="https://example.com/short-article",
                title="短正文文章",
                content="短正文内容。" * 20,
                feed_url=feed_url,
                content_type=ContentType.RSS_CONTENT,
                published_at=datetime(2026, 8, 24, tzinfo=PROJECT_TIMEZONE),
            )
        ],
    )


def test_article_summary_is_cached_by_snapshot_and_can_retry(tmp_path: Path) -> None:
    service = _service(tmp_path)
    article = service.list_articles()[0]

    first = service.store.claim_summary_job(preferred_snapshot_id=article.snapshot_id)
    assert first is not None
    assert first.content == article.article.content
    other = service.store.claim_summary_job()
    assert other is not None
    assert other.snapshot_id != first.snapshot_id

    service.store.fail_summary_job(first.snapshot_id, RuntimeError("暂时失败"))
    failed = service.get_article(article.article.article_id)
    assert failed.summary_status == "failed"
    assert failed.summary_error == "暂时失败"

    service.store.retry_summary(article.article.article_id)
    retried = service.store.claim_summary_job(preferred_snapshot_id=first.snapshot_id)
    assert retried is not None
    service.store.complete_summary_job(retried.snapshot_id, "第一句摘要。第二句摘要。")

    completed = service.get_article(article.article.article_id)
    assert completed.summary == "第一句摘要。第二句摘要。"
    assert completed.summary_status == "completed"
    remaining = service.store.claim_summary_job()
    assert remaining is not None
    assert remaining.snapshot_id != first.snapshot_id


def test_short_article_skips_summary_generation(tmp_path: Path) -> None:
    service = ReaderService(tmp_path / "short-reader.db", fetcher=_short_fetcher)
    service.subscribe("https://example.com/short-rss.xml")
    article = service.list_articles()[0]
    assert len(article.article.content) < 300
    assert article.summary is None
    assert article.summary_status == "skipped"
    assert service.store.claim_summary_job(preferred_snapshot_id=article.snapshot_id) is None

    processed: list[str] = []
    manager = SummaryTaskManager(
        service.store,
        runner=lambda job: processed.append(job.snapshot_id) or "摘要第一句。摘要第二句。",
    )
    try:
        assert manager.wait_until_idle(1)
    finally:
        manager.shutdown()

    assert processed == []


def test_reader_automation_settings_persist_and_validate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    defaults = service.store.get_reader_automation_settings()

    assert defaults.enabled is True
    assert defaults.dwell_seconds == 15
    assert defaults.read_ratio == 1 / 3
    assert defaults.agent_timeout_seconds == 300
    assert defaults.max_searches == 3
    assert defaults.max_attempts == 3

    updated = service.store.update_reader_automation_settings(
        enabled=False,
        dwell_seconds=30,
        read_ratio=0.5,
        agent_timeout_seconds=240,
        max_searches=3,
        max_attempts=2,
    )
    restarted = ReaderService(service.store.database_path, fetcher=_fetcher)

    assert updated.enabled is False
    assert restarted.store.get_reader_automation_settings() == updated


def test_auto_article_research_is_idempotent_but_manual_runs_keep_history(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    article = service.list_articles()[0]
    config = {
        "timeout_seconds": 180,
        "max_steps": 3,
        "max_attempts": 3,
    }

    first = service.store.create_article_research_run(article, mode="auto", config=config)
    repeated = service.store.create_article_research_run(article, mode="auto", config=config)
    manual_while_active = service.store.create_article_research_run(
        article, mode="manual", config=config
    )
    service.store.update_article_research_run(
        first.id,
        status="completed",
        expected_status="queued",
    )
    manual_one = service.store.create_article_research_run(article, mode="manual", config=config)
    manual_while_active_again = service.store.create_article_research_run(
        article, mode="manual", config=config
    )
    service.store.update_article_research_run(
        manual_one.id,
        status="completed",
        expected_status="queued",
    )
    manual_two = service.store.create_article_research_run(article, mode="manual", config=config)

    assert repeated.id == first.id
    assert manual_while_active.id == first.id
    assert manual_while_active_again.id == manual_one.id
    assert first.topic == "核验《自动研究文章》中会显著影响读者判断的事实主张"
    assert manual_one.id != manual_two.id
    history = service.store.list_article_research_runs(article.article.article_id)
    assert [item.mode for item in history] == ["manual", "manual", "auto"]
    assert all(item.snapshot_id == article.snapshot_id for item in history)


class _BlockingAgentTasks:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.first_started = Event()
        self.release_first = Event()
        self.finished = Event()
        self._lock = Lock()

    def submit(self, research_run_id: str, **_: object) -> dict[str, object]:
        with self._lock:
            self.calls.append(research_run_id)
            position = len(self.calls)
        if position == 1:
            self.first_started.set()
        return {"analysis_run_id": f"analysis-{research_run_id}", "status": "running"}

    def wait(self, _request_id: str, timeout: float | None = None) -> dict[str, object]:
        with self._lock:
            position = len(self.calls)
            run_id = self.calls[-1]
        if position == 1:
            assert self.release_first.wait(timeout=2)
        if position == 3:
            self.finished.set()
        return {"analysis_run_id": f"analysis-{run_id}", "status": "completed"}

    def stop_and_wait(
        self,
        research_run_id: str,
        *,
        request_id: str | None = None,
        timeout: float = 2.0,
    ) -> dict[str, object]:
        self.release_first.set()
        return {
            "analysis_run_id": f"analysis-{research_run_id}",
            "request_id": request_id,
            "status": "cancelled",
        }

    def shutdown(self) -> None:
        return None


class _FailedAgentTasks:
    def submit(self, research_run_id: str, **_: object) -> dict[str, object]:
        return {"analysis_run_id": f"analysis-{research_run_id}", "status": "running"}

    def wait(self, _request_id: str, timeout: float | None = None) -> dict[str, object]:
        return {
            "analysis_run_id": "analysis-failed",
            "status": "failed",
            "error": {
                "type": "BadRequestError",
                "message": "文章研究请求参数无效",
            },
        }

    def shutdown(self) -> None:
        return None


def test_article_research_persists_agent_failure_and_error(tmp_path: Path) -> None:
    service = _service(tmp_path)
    article = service.list_articles()[0]
    settings = service.store.get_reader_automation_settings()
    manager = ArticleResearchTaskManager(
        service.store.database_path,
        store=service.store,
        agent_tasks=_FailedAgentTasks(),
    )
    run = service.store.create_article_research_run(
        article,
        mode="manual",
        config={
            "timeout_seconds": settings.agent_timeout_seconds,
            "max_steps": settings.max_searches,
            "max_attempts": settings.max_attempts,
        },
    )

    try:
        manager._run_one(run.id)
    finally:
        manager.shutdown()

    persisted = service.store.get_article_research_run(run.id)
    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.error == {
        "type": "BadRequestError",
        "message": "文章研究请求参数无效",
    }


def test_manual_article_research_precedes_queued_auto_work(tmp_path: Path) -> None:
    service = _service(tmp_path)
    articles = service.list_articles()
    settings = service.store.get_reader_automation_settings()
    agent_tasks = _BlockingAgentTasks()
    manager = ArticleResearchTaskManager(
        service.store.database_path,
        store=service.store,
        agent_tasks=agent_tasks,
    )

    first = manager.submit(articles[0], mode="auto", settings=settings)
    assert agent_tasks.first_started.wait(timeout=2)
    queued_auto = manager.submit(articles[1], mode="auto", settings=settings)
    manual = manager.submit(articles[2], mode="manual", settings=settings)
    agent_tasks.release_first.set()

    assert agent_tasks.finished.wait(timeout=2)
    manager.shutdown()
    assert agent_tasks.calls == [first.id, manual.id, queued_auto.id]


def test_article_research_stop_cancels_queued_and_running_runs(tmp_path: Path) -> None:
    service = _service(tmp_path)
    articles = service.list_articles()
    settings = service.store.get_reader_automation_settings()
    agent_tasks = _BlockingAgentTasks()
    manager = ArticleResearchTaskManager(
        service.store.database_path,
        store=service.store,
        agent_tasks=agent_tasks,
    )

    running = manager.submit(articles[0], mode="auto", settings=settings)
    assert agent_tasks.first_started.wait(timeout=2)
    queued = manager.submit(articles[1], mode="manual", settings=settings)

    stopped_queued = manager.stop(queued.id)
    assert stopped_queued is not None
    assert stopped_queued.status == "cancelled"

    stopped_running = manager.stop(running.id)
    assert stopped_running is not None
    assert stopped_running.status == "cancelled"
    manager.shutdown()
    assert agent_tasks.calls == [running.id]
