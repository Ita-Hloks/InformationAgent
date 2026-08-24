from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from information_agent.api import create_app
from information_agent.collection import FeedFetchResult, RawFeedEntry
from information_agent.contracts import PROJECT_TIMEZONE, ContentType
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
                content="这是用于 Reader API 测试的正文，包含足够的信息供摘要和研究任务使用。",
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

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def _app(
    tmp_path: Path,
    *,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[TestClient, SQLiteCollectionStore, _FakeSummaryManager, _FakeResearchManager]:
    if monkeypatch is not None:
        monkeypatch.setenv("LLM_API_KEY", "test-main")
        monkeypatch.setenv("SEARCH_LLM_API_KEY", "test-search")
        monkeypatch.setenv("SEARCH_LLM_MODEL", "search-model")
        monkeypatch.setenv("SEARCH_LLM_BASE_URL", "https://search.example/v1")
    service = ReaderService(tmp_path / "reader-api.db", fetcher=_fetcher)
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


def test_article_research_api_is_idempotent_for_auto_and_replayable_for_manual(
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
    manual_one = client.post(f"/api/articles/{article_id}/research", json={"mode": "manual"})
    manual_two = client.post(f"/api/articles/{article_id}/research", json={"mode": "manual"})

    assert [response.status_code for response in (auto_one, auto_two, manual_one, manual_two)] == [
        200,
        200,
        200,
        200,
    ]
    assert auto_one.json()["run_id"] == auto_two.json()["run_id"]
    assert manual_one.json()["run_id"] != manual_two.json()["run_id"]
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

    unified = client.get("/api/research/runs", params={"mode": "auto"})
    assert unified.status_code == 200
    assert unified.json()["runs"]
    assert all(item["mode"] == "auto" for item in unified.json()["runs"])

    invalid = client.post(f"/api/articles/{article_id}/research", json={"mode": "other"})
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_request"

    normal_run_id = store.start_run("普通研究工作台", [])
    manual_runs = client.get("/api/research/runs", params={"mode": "manual"})
    assert manual_runs.status_code == 200
    assert any(
        item["run_id"] == normal_run_id and item["mode"] == "manual"
        for item in manual_runs.json()["runs"]
    )


def test_create_app_shuts_down_injected_background_managers(tmp_path: Path) -> None:
    client, _, summary, research = _app(tmp_path)
    with client:
        assert client.get("/api/health").status_code == 200

    assert summary.shutdown_calls == [False]
    assert research.shutdown_calls == 1
