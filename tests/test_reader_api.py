from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from information_agent.api import create_app
from information_agent.collection import FeedFetchResult, RawFeedEntry
from information_agent.contracts import PROJECT_TIMEZONE, ContentType
from information_agent.normalization import normalize_evidence
from information_agent.reader import ReaderService
from information_agent.storage import ArticleResearchRun, ReaderArticle, SQLiteCollectionStore


def _fetcher(feed_url: str, _timeout: float, **_: object) -> FeedFetchResult:
    return FeedFetchResult(
        feed_url=feed_url,
        etag='"v1"',
        last_modified=None,
        entries=[
            RawFeedEntry(
                source_url="https://example.com/reader-api-article",
                title="Reader API 测试文章",
                content=(
                    "这是用于 Reader API 测试的正文，包含足够的信息供摘要和研究任务使用。"
                    + "补充正文内容。" * 50
                ),
                feed_url=feed_url,
                content_type=ContentType.RSS_CONTENT,
                published_at=datetime(2026, 8, 24, tzinfo=PROJECT_TIMEZONE),
            )
        ],
    )


class _FakeSummaryManager:
    def __init__(self, store: SQLiteCollectionStore) -> None:
        self.store = store
        self.submitted: list[tuple[str, str]] = []
        self.retried: list[str] = []
        self.shutdown_calls: list[bool] = []

    def submit(self, article_id: str, snapshot_id: str) -> None:
        self.submitted.append((article_id, snapshot_id))

    def wake(self) -> None:
        return None

    def retry(self, article_id: str) -> str:
        self.retried.append(article_id)
        return self.store.retry_summary(article_id)

    def shutdown(self, *, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)


class _FakeResearchManager:
    def __init__(self, store: SQLiteCollectionStore) -> None:
        self.store = store
        self.submitted: list[dict[str, Any]] = []
        self.shutdown_calls = 0

    def submit(
        self,
        article: ReaderArticle,
        *,
        mode: str,
        settings: object,
        request_id: str | None = None,
    ) -> ArticleResearchRun:
        assert settings == self.store.get_reader_automation_settings()
        run = self.store.create_article_research_run(
            article,
            mode=mode,
            config={
                "timeout_seconds": settings.agent_timeout_seconds,
                "max_steps": settings.max_searches,
                "max_attempts": settings.max_attempts,
            },
            request_id=request_id,
        )
        self.submitted.append(
            {
                "article_id": article.article.article_id,
                "mode": mode,
                "config": run.config,
            }
        )
        return run

    def agent_snapshot(self, _run: ArticleResearchRun) -> None:
        return None

    def stop(self, run_id: str) -> ArticleResearchRun | None:
        run = self.store.get_article_research_run(run_id)
        if run is None:
            return None
        return self.store.update_article_research_run(
            run_id,
            status="cancelled",
            error={
                "type": "ArticleResearchStopped",
                "message": "用户请求停止文章研究",
            },
            expected_status=run.status,
        )

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def _app(
    tmp_path: Path,
    *,
    monkeypatch: pytest.MonkeyPatch | None = None,
    fetcher: Any = _fetcher,
) -> tuple[TestClient, SQLiteCollectionStore, _FakeSummaryManager, _FakeResearchManager]:
    if monkeypatch is not None:
        monkeypatch.setenv("LLM_API_KEY", "test-main")
        monkeypatch.setenv("SEARCH_LLM_API_KEY", "test-search")
        monkeypatch.setenv("SEARCH_LLM_MODEL", "search-model")
        monkeypatch.setenv("SEARCH_LLM_BASE_URL", "https://search.example/v1")
    service = ReaderService(tmp_path / "reader-api.db", fetcher=fetcher)
    store = service.store
    summary = _FakeSummaryManager(store)
    research = _FakeResearchManager(store)
    client = TestClient(
        create_app(
            service,
            summary_task_manager=summary,  # type: ignore[arg-type]
            article_research_task_manager=research,  # type: ignore[arg-type]
        )
    )
    return client, store, summary, research


def _create_article(client: TestClient) -> str:
    assert client.post("/api/feeds", json={"url": "https://example.com/rss.xml"}).status_code == 200
    article = client.get("/api/articles").json()[0]
    return str(article["id"])


def test_feed_unsubscribe_preserves_data_and_allows_resubscribe(tmp_path: Path) -> None:
    client, store, _, _ = _app(tmp_path)
    created = client.post("/api/feeds", json={"url": "https://example.com/rss.xml"})
    assert created.status_code == 200
    feed_id = created.json()["id"]
    article_id = client.get("/api/articles").json()[0]["id"]
    assert (
        client.put(
            "/api/articles/state",
            json={"article_ids": [article_id], "is_read": True, "is_saved": True},
        ).status_code
        == 200
    )

    deleted = client.delete(f"/api/feeds/{feed_id}")
    assert deleted.status_code == 204
    assert client.get("/api/feeds").json() == []
    assert client.get("/api/articles").json() == []
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM feeds").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM feed_entries").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM article_snapshots").fetchone()[0] == 1
        assert connection.execute(
            "SELECT is_read, is_saved FROM reader_article_states WHERE article_id = ?",
            (article_id,),
        ).fetchone() == (1, 1)

    resubscribed = client.post("/api/feeds", json={"url": "https://example.com/rss.xml"})
    assert resubscribed.status_code == 200
    assert resubscribed.json()["id"] == feed_id
    assert client.get("/api/articles").json()[0]["id"] == article_id

    missing = client.delete("/api/feeds/missing")
    assert missing.status_code == 404


def test_article_delete_hides_article_and_preserves_source_data(tmp_path: Path) -> None:
    client, store, _, _ = _app(tmp_path)
    article_id = _create_article(client)

    deleted = client.delete(f"/api/articles/{article_id}")

    assert deleted.status_code == 204
    assert client.get("/api/articles").json() == []
    assert client.get(f"/api/articles/{article_id}").status_code == 404
    feed = client.get("/api/feeds").json()[0]
    assert feed["article_count"] == 0
    assert feed["unread_count"] == 0
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM article_snapshots").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM feed_entries").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM reader_deleted_articles WHERE article_id = ?",
                (article_id,),
            ).fetchone()[0]
            == 1
        )

    assert client.delete(f"/api/articles/{article_id}").status_code == 204
    assert client.delete("/api/articles/missing").status_code == 404


def test_feed_refresh_updates_entries_and_preserves_not_modified_articles(tmp_path: Path) -> None:
    feed_url = "https://example.com/rss.xml"
    first_entry = RawFeedEntry(
        source_url="https://example.com/reader-api-article",
        title="Reader API 刷新文章",
        content="这是 RSS 刷新前的文章正文，内容足够长并且会被保存为初始快照。",
        feed_url=feed_url,
        entry_id="entry-1",
        updated_at="2026-08-24T10:00:00+08:00",
        content_type=ContentType.RSS_CONTENT,
    )
    updated_entry = RawFeedEntry(
        source_url=first_entry.source_url,
        title=first_entry.title,
        content="这是 RSS 刷新后的文章正文，内容已经变化并且应该生成新的文章快照。",
        feed_url=feed_url,
        entry_id=first_entry.entry_id,
        updated_at="2026-08-24T11:00:00+08:00",
        content_type=ContentType.RSS_CONTENT,
    )
    responses = [
        FeedFetchResult(feed_url, [first_entry], '"v1"', None),
        FeedFetchResult(feed_url, [updated_entry], '"v2"', None),
        FeedFetchResult(feed_url, [], '"v2"', None, not_modified=True),
    ]
    requests: list[tuple[str | None, str | None]] = []

    def fetcher(url: str, _timeout: float, **kwargs: object) -> FeedFetchResult:
        assert url == feed_url
        requests.append((kwargs.get("etag"), kwargs.get("last_modified")))
        return responses.pop(0)

    client, store, _, _ = _app(tmp_path, fetcher=fetcher)
    created = client.post("/api/feeds", json={"url": feed_url})
    assert created.status_code == 200
    feed_id = created.json()["id"]
    initial_article = client.get("/api/articles").json()[0]

    refreshed = client.post(f"/api/feeds/{feed_id}/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["article_count"] == 1
    refreshed_article = client.get("/api/articles").json()[0]
    assert refreshed_article["content"] == updated_entry.content
    assert refreshed_article["snapshot_id"] != initial_article["snapshot_id"]

    unchanged = client.post(f"/api/feeds/{feed_id}/refresh")
    assert unchanged.status_code == 200
    assert client.get("/api/articles").json()[0]["content"] == updated_entry.content
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM article_snapshots").fetchone()[0] == 2

    assert requests == [(None, None), ('"v1"', None), ('"v2"', None)]


def test_article_research_status_is_bound_to_current_snapshot(tmp_path: Path) -> None:
    feed_url = "https://example.com/rss.xml"
    entries = [
        RawFeedEntry(
            source_url="https://example.com/reader-api-article",
            title="快照状态测试文章",
            content=content,
            feed_url=feed_url,
            entry_id="entry-1",
            updated_at=marker,
            collected_at=collected_at,
            content_type=ContentType.RSS_CONTENT,
        )
        for content, marker, collected_at in (
            (
                "这是旧快照正文，内容足够长并且会保存研究状态。" * 8,
                "2026-08-24T10:00:00+08:00",
                datetime(2026, 8, 24, 10, tzinfo=PROJECT_TIMEZONE),
            ),
            (
                "这是新快照正文，当前文章不应继承旧快照的研究状态。" * 8,
                "2026-08-24T11:00:00+08:00",
                datetime(2026, 8, 24, 11, tzinfo=PROJECT_TIMEZONE),
            ),
        )
    ]
    responses = [
        FeedFetchResult(feed_url, [entries[0]], '"v1"', None),
        FeedFetchResult(feed_url, [entries[1]], '"v2"', None),
    ]

    def fetcher(_url: str, _timeout: float, **_: object) -> FeedFetchResult:
        return responses.pop(0)

    client, store, _, _ = _app(tmp_path, fetcher=fetcher)
    created = client.post("/api/feeds", json={"url": feed_url})
    assert created.status_code == 200
    article_id = client.get("/api/articles").json()[0]["id"]
    old_article = store.get_reader_article(article_id)
    assert old_article is not None
    old_run = store.create_article_research_run(
        old_article,
        mode="manual",
        config={"timeout_seconds": 180, "max_steps": 1, "max_attempts": 1},
    )
    store.update_article_research_run(old_run.id, status="partial", expected_status="queued")

    store.save_subscription(
        feed_url=feed_url,
        title="快照状态测试文章",
        site_url=None,
        result_etag='"v2"',
        result_last_modified=None,
        entries=[entries[1]],
        articles=normalize_evidence([entries[1]]),
    )

    current = client.get("/api/articles").json()[0]
    assert current["snapshot_id"] != old_article.snapshot_id
    assert current["research_status"] == "none"
    assert current["research_mode"] is None


def test_reader_automation_settings_api_persists_and_rejects_strict_input(
    tmp_path: Path,
) -> None:
    client, _, _, _ = _app(tmp_path)

    defaults = client.get("/api/settings/reader-automation")
    assert defaults.status_code == 200
    assert defaults.json()["dwell_seconds"] == 15

    updated = client.put(
        "/api/settings/reader-automation",
        json={
            "enabled": False,
            "dwell_seconds": 30,
            "read_ratio": 0.5,
            "agent_timeout_seconds": 240,
            "max_searches": 3,
            "max_attempts": 2,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False

    invalid = client.put(
        "/api/settings/reader-automation",
        json={
            "enabled": True,
            "dwell_seconds": 0,
            "read_ratio": 0.5,
            "agent_timeout_seconds": 240,
            "max_searches": 3,
            "max_attempts": 2,
            "unexpected": True,
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_request"


def test_summary_retry_api_requeues_failed_snapshot_and_checks_missing_article(
    tmp_path: Path,
) -> None:
    client, store, summary, _ = _app(tmp_path)
    article_id = _create_article(client)
    article = store.get_reader_article(article_id)
    assert article is not None and article.snapshot_id is not None
    job = store.claim_summary_job(preferred_snapshot_id=article.snapshot_id)
    assert job is not None
    store.fail_summary_job(job.snapshot_id, RuntimeError("暂时失败"))

    retried = client.post(f"/api/articles/{article_id}/summary/retry")
    assert retried.status_code == 200
    assert retried.json()["summary_status"] == "pending"
    assert summary.retried == [article_id]

    missing = client.post("/api/articles/missing/summary/retry")
    assert missing.status_code == 404


def test_article_research_api_deduplicates_active_runs_and_replays_manual_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store, _, research = _app(tmp_path, monkeypatch=monkeypatch)
    article_id = _create_article(client)

    settings = client.put(
        "/api/settings/reader-automation",
        json={
            "enabled": True,
            "dwell_seconds": 20,
            "read_ratio": 0.4,
            "agent_timeout_seconds": 240,
            "max_searches": 3,
            "max_attempts": 2,
        },
    )
    assert settings.status_code == 200

    auto_one = client.post(f"/api/articles/{article_id}/research", json={"mode": "auto"})
    auto_two = client.post(f"/api/articles/{article_id}/research", json={"mode": "auto"})
    manual_while_active = client.post(
        f"/api/articles/{article_id}/research", json={"mode": "manual"}
    )

    assert [response.status_code for response in (auto_one, auto_two, manual_while_active)] == [
        200,
        200,
        200,
    ]
    assert auto_one.json()["run_id"] == auto_two.json()["run_id"]
    assert manual_while_active.json()["run_id"] == auto_one.json()["run_id"]
    store.update_article_research_run(
        auto_one.json()["run_id"], status="completed", expected_status="queued"
    )

    manual_one = client.post(f"/api/articles/{article_id}/research", json={"mode": "manual"})
    manual_while_active_again = client.post(
        f"/api/articles/{article_id}/research", json={"mode": "manual"}
    )
    assert manual_one.status_code == 200
    assert manual_while_active_again.status_code == 200
    assert manual_while_active_again.json()["run_id"] == manual_one.json()["run_id"]
    store.update_article_research_run(
        manual_one.json()["run_id"], status="completed", expected_status="queued"
    )
    manual_two = client.post(f"/api/articles/{article_id}/research", json={"mode": "manual"})
    assert manual_two.status_code == 200
    assert manual_two.json()["run_id"] != manual_one.json()["run_id"]
    assert research.submitted[0]["config"] == {
        "timeout_seconds": 240,
        "max_steps": 3,
        "max_attempts": 2,
    }

    auto_history = client.get(f"/api/articles/{article_id}/research", params={"mode": "auto"})
    manual_history = client.get(f"/api/articles/{article_id}/research", params={"mode": "manual"})
    assert auto_history.status_code == 200
    assert manual_history.status_code == 200
    assert len(auto_history.json()["runs"]) == 1
    assert len(manual_history.json()["runs"]) == 2
    assert all(item["mode"] == "auto" for item in auto_history.json()["runs"])
    assert all(item["mode"] == "manual" for item in manual_history.json()["runs"])
    assert all("agent" not in item for item in auto_history.json()["runs"])

    invalid = client.post(f"/api/articles/{article_id}/research", json={"mode": "other"})
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_request"


def test_article_research_detail_and_stop_are_scoped_to_article(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _, _ = _app(tmp_path, monkeypatch=monkeypatch)
    article_id = _create_article(client)
    created = client.post(f"/api/articles/{article_id}/research", json={"mode": "manual"})
    run_id = created.json()["run_id"]

    detail = client.get(f"/api/articles/{article_id}/research/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["run_id"] == run_id
    assert detail.json()["agent"] is None

    stopped = client.post(f"/api/articles/{article_id}/research/{run_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "cancelled"

    missing_run = client.get(f"/api/articles/{article_id}/research/missing")
    assert missing_run.status_code == 404
    wrong_article = client.get(f"/api/articles/missing/research/{run_id}")
    assert wrong_article.status_code == 404


def test_article_research_history_can_delete_finished_runs_but_not_active_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store, _, _ = _app(tmp_path, monkeypatch=monkeypatch)
    article_id = _create_article(client)
    created = client.post(f"/api/articles/{article_id}/research", json={"mode": "manual"})
    run_id = created.json()["run_id"]

    active_delete = client.delete(f"/api/articles/{article_id}/research/{run_id}")
    assert active_delete.status_code == 409
    assert active_delete.json()["detail"]["code"] == "article_research_active"

    store.update_article_research_run(run_id, status="completed", expected_status="queued")
    analysis = store.create_analysis_run(
        run_id,
        "agent_research",
        {"max_attempts": 1},
        idempotency_key="delete-history-analysis",
    )
    step = store.create_analysis_step(analysis.id, 1, "decision-1")
    attempt = store.create_analysis_attempt(
        step.id,
        "agent_decision",
        "request-hash",
        "attempt-1",
    )
    store.record_analysis_artifact(
        analysis.id,
        "request",
        {"topic": "删除测试"},
        artifact_key="attempt-1:request",
        step_id=step.id,
        attempt_id=attempt.id,
    )
    deleted = client.delete(f"/api/articles/{article_id}/research/{run_id}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(f"/api/articles/{article_id}/research/{run_id}").status_code == 404
    assert client.get(f"/api/articles/{article_id}/research").json()["runs"] == []

    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM article_research_runs WHERE id = ?", (run_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM research_runs WHERE id = ?", (run_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM run_evidence WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM analysis_runs WHERE id = ?", (analysis.id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM analysis_steps WHERE id = ?", (step.id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM analysis_attempts WHERE id = ?", (attempt.id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM analysis_artifacts WHERE analysis_run_id = ?",
                (analysis.id,),
            ).fetchone()[0]
            == 0
        )

    assert client.delete(f"/api/articles/{article_id}/research/{run_id}").status_code == 404


def test_create_app_shuts_down_injected_background_managers(tmp_path: Path) -> None:
    client, _, summary, research = _app(tmp_path)
    with client:
        assert client.get("/api/health").status_code == 200

    assert summary.shutdown_calls == [False]
    assert research.shutdown_calls == 1
