from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from math import inf, nan
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from information_agent import settings as settings_module
from information_agent.api import create_app
from information_agent.collection import FeedFetchResult, RawFeedEntry
from information_agent.common import CONTENT_BATCH_CHARS
from information_agent.contracts import PROJECT_TIMEZONE, ContentType
from information_agent.reader import (
    ArticleAssistant,
    ArticleNotFoundError,
    ReaderService,
)


def _fetcher(feed_url: str, timeout: float, **_: object) -> FeedFetchResult:
    return FeedFetchResult(
        feed_url=feed_url,
        etag='"v1"',
        last_modified=None,
        entries=[
            RawFeedEntry(
                source_url="https://example.com/article-1",
                title="一篇用于 API 测试的文章标题",
                content="这是一段足够长的 RSS 文章正文，用于验证文章订阅和获取接口。",
                feed_url=feed_url,
                site_url="https://example.com/",
                content_type=ContentType.RSS_CONTENT,
                published_at=datetime(2026, 8, 10, tzinfo=PROJECT_TIMEZONE),
            )
        ],
    )


def _client(tmp_path: Path) -> TestClient:
    service = ReaderService(tmp_path / "api.db", fetcher=_fetcher)
    return TestClient(create_app(service))


def test_settings_api_returns_only_redacted_main_llm_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "main-secret")
    monkeypatch.setenv("LLM_MODEL", "local-model")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("SEARCH_LLM_API_KEY", "search-secret")

    response = _client(tmp_path).get("/api/settings")

    assert response.status_code == 200
    assert response.json() == {
        "api_key_configured": True,
        "model": "local-model",
        "base_url": "http://127.0.0.1:11434/v1",
        "available": True,
    }
    assert "main-secret" not in response.text
    assert "search-secret" not in response.text


def test_settings_api_reports_unavailable_without_exposing_key_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "local-model")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:11434/v1")

    response = _client(tmp_path).get("/api/settings")

    assert response.status_code == 200
    assert response.json()["api_key_configured"] is False
    assert response.json()["available"] is False


def test_settings_api_redacts_key_if_it_appears_in_other_main_config_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "embedded-secret")
    monkeypatch.setenv("LLM_MODEL", "embedded-secret-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1?token=embedded-secret")

    response = _client(tmp_path).get("/api/settings")

    assert response.status_code == 200
    assert response.json()["model"] == "[已隐藏]-model"
    assert response.json()["base_url"] == "https://provider.example/v1"
    assert response.json()["available"] is False
    assert "embedded-secret" not in response.text


def test_open_env_api_uses_fixed_project_path_without_accepting_client_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("LLM_API_KEY=main-secret\n", encoding="utf-8")
    client_path = tmp_path / "outside.env"
    opened_paths: list[str] = []
    monkeypatch.setattr(settings_module, "PROJECT_ENV_PATH", env_path)
    monkeypatch.setattr(
        settings_module.os,
        "startfile",
        lambda path: opened_paths.append(path),
        raising=False,
    )

    client = _client(tmp_path)
    response = client.post(
        "/api/settings/env/open",
        json={"path": str(client_path)},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "opened"}
    assert opened_paths == [str(env_path)]
    assert "main-secret" not in response.text
    assert str(client_path) not in response.text


def test_open_env_api_returns_safe_error_for_missing_project_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / "missing.env"
    monkeypatch.setattr(settings_module, "PROJECT_ENV_PATH", env_path)

    response = _client(tmp_path).post("/api/settings/env/open")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "env_open_failed",
            "message": "无法打开项目 .env 文件，请确认文件存在并已安装默认编辑器",
        }
    }
    assert str(tmp_path) not in response.text


class _FakeCompletionClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def with_options(self, *, timeout: float) -> _FakeCompletionClient:
        return self

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"已确认"}'))]
        )


def test_article_assistant_limits_context_and_returns_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(tmp_path / "logs"))
    service = ReaderService(tmp_path / "api.db", fetcher=_fetcher)
    service.subscribe("https://example.com/rss.xml")
    article = service.list_articles()[0]
    long_content = "头" * 1500 + "尾" * 1500
    article = replace(article, article=replace(article.article, content=long_content))
    client = _FakeCompletionClient()

    assert ArticleAssistant(client).answer(article, "文章说了什么？") == "已确认"

    user_message = client.calls[0]["messages"][1]["content"]
    context = user_message.split("正文：\n", 1)[1].split("</article>", 1)[0].strip()
    assert len(context) == CONTENT_BATCH_CHARS
    assert context.startswith("头")
    assert context.endswith("尾")


def test_article_ask_api_reads_article_by_id_and_normalizes_question(tmp_path: Path) -> None:
    service = ReaderService(tmp_path / "api.db", fetcher=_fetcher)
    service.subscribe("https://example.com/rss.xml")
    article_id = service.list_articles()[0].article.article_id

    class RecordingAssistant:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        def answer(self, article, question: str, *, request_id: str) -> str:
            self.calls.append((article.article.article_id, question, request_id))
            return "基于文章的回答"

    assistant = RecordingAssistant()
    client = TestClient(create_app(service, article_assistant=assistant))
    response = client.post(
        f"/api/articles/{article_id}/ask",
        json={
            "question": "  当前文章的核心是什么？  ",
            "request_id": "api-answer-1",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "completed",
        "article_id": article_id,
        "request_id": "api-answer-1",
        "snapshot_id": service.list_articles()[0].snapshot_id,
        "question": "当前文章的核心是什么？",
        "answer": "基于文章的回答",
        "created_at": response.json()["created_at"],
        "finished_at": response.json()["finished_at"],
    }
    assert assistant.calls == [(article_id, "当前文章的核心是什么？", "api-answer-1")]
    assert (
        client.post(f"/api/articles/{article_id}/ask", json={"question": "  "}).status_code == 422
    )


def test_article_answers_survive_reader_restart_and_reuse_request_id(tmp_path: Path) -> None:
    database_path = tmp_path / "api.db"
    service = ReaderService(database_path, fetcher=_fetcher)
    service.subscribe("https://example.com/rss.xml")
    article_id = service.list_articles()[0].article.article_id

    first_assistant = _RecordingAssistantForAnswers("持久化回答")
    first_client = TestClient(create_app(service, article_assistant=first_assistant))
    first = first_client.post(
        f"/api/articles/{article_id}/ask",
        json={"question": "文章的核心是什么？", "request_id": "restart-answer-1"},
    )
    assert first.status_code == 200

    restarted = ReaderService(database_path, fetcher=_fetcher)
    second_assistant = _RecordingAssistantForAnswers("不应再次调用")
    second_client = TestClient(create_app(restarted, article_assistant=second_assistant))
    repeated = second_client.post(
        f"/api/articles/{article_id}/ask",
        json={"question": "文章的核心是什么？", "request_id": "restart-answer-1"},
    )

    assert repeated.status_code == 200
    assert repeated.json()["answer"] == "持久化回答"
    assert second_assistant.calls == []
    history = second_client.get(f"/api/articles/{article_id}/answers")
    assert history.status_code == 200
    assert [item["request_id"] for item in history.json()["answers"]] == ["restart-answer-1"]


def test_reader_restart_recovers_running_article_answers_and_allows_retry(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "api.db"
    service = ReaderService(database_path, fetcher=_fetcher)
    service.subscribe("https://example.com/rss.xml")
    article = service.list_articles()[0]
    request_ids = ("recovery-answer-1", "recovery-answer-2")

    for request_id in request_ids:
        claim = service.store.claim_article_answer(
            article,
            request_id=request_id,
            question="文章的核心是什么？",
        )
        assert claim.owner is True
        assert claim.record.status == "running"

    restarted = ReaderService(database_path, fetcher=_fetcher)
    client = TestClient(
        create_app(
            restarted,
            article_assistant=_RecordingAssistantForAnswers("重启后回答"),
        )
    )

    for request_id in request_ids:
        assert (
            client.get(f"/api/articles/{article.article.article_id}/ask/{request_id}").status_code
            == 404
        )
        assert restarted.store.get_article_answer(request_id) is None

    retried = client.post(
        f"/api/articles/{article.article.article_id}/ask",
        json={"question": "文章的核心是什么？", "request_id": request_ids[0]},
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "completed"
    assert retried.json()["answer"] == "重启后回答"

    repeated = client.post(
        f"/api/articles/{article.article.article_id}/ask",
        json={"question": "文章的核心是什么？", "request_id": request_ids[0]},
    )
    assert repeated.status_code == 200
    assert repeated.json()["answer"] == "重启后回答"


def test_failed_article_answer_is_not_saved_and_can_retry_same_request_id(tmp_path: Path) -> None:
    service = ReaderService(tmp_path / "api.db", fetcher=_fetcher)
    service.subscribe("https://example.com/rss.xml")
    article_id = service.list_articles()[0].article.article_id
    assistant = _FailOnceAssistant()
    client = TestClient(create_app(service, article_assistant=assistant))

    failed = client.post(
        f"/api/articles/{article_id}/ask",
        json={"question": "请验证这条信息", "request_id": "retry-answer-1"},
    )
    assert failed.status_code == 502
    assert client.get(f"/api/articles/{article_id}/answers").json()["answers"] == []

    retried = client.post(
        f"/api/articles/{article_id}/ask",
        json={"question": "请验证这条信息", "request_id": "retry-answer-1"},
    )
    assert retried.status_code == 200
    assert retried.json()["answer"] == "重试成功"
    assert len(client.get(f"/api/articles/{article_id}/answers").json()["answers"]) == 1


class _RecordingAssistantForAnswers:
    def __init__(self, answer: str) -> None:
        self.answer_text = answer
        self.calls: list[tuple[str, str]] = []

    def answer(self, _article, question: str, *, request_id: str) -> str:
        self.calls.append((question, request_id))
        return self.answer_text


class _FailOnceAssistant(_RecordingAssistantForAnswers):
    def __init__(self) -> None:
        super().__init__("重试成功")
        self.failed = False

    def answer(self, article, question: str, *, request_id: str) -> str:
        if not self.failed:
            self.failed = True
            raise ValueError("模拟模型输出错误")
        return super().answer(article, question, request_id=request_id)


def test_feed_subscription_and_article_fetch(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/feeds",
        json={"url": "https://example.com/rss.xml", "title": "示例来源"},
    )
    assert response.status_code == 200
    feed = response.json()
    assert feed["title"] == "示例来源"
    assert feed["article_count"] == 1
    assert feed["unread_count"] == 1

    feeds = client.get("/api/feeds")
    assert feeds.status_code == 200
    assert feeds.json()[0]["url"] == "https://example.com/rss.xml"

    articles = client.get("/api/articles")
    assert articles.status_code == 200
    assert articles.json()[0]["title"] == "一篇用于 API 测试的文章标题"
    assert articles.json()[0]["is_read"] is False
    assert articles.json()[0]["is_saved"] is False

    article_id = articles.json()[0]["id"]
    detail = client.get(f"/api/articles/{article_id}")
    assert detail.status_code == 200
    assert "足够长的 RSS 文章正文" in detail.json()["content"]


def test_article_state_round_trips_through_sqlite(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post(
        "/api/feeds",
        json={"url": "https://example.com/rss.xml", "title": "示例来源"},
    )
    article = client.get("/api/articles").json()[0]

    update = client.put(
        "/api/articles/state",
        json={"article_ids": [article["id"]], "is_read": True, "is_saved": True},
    )
    assert update.status_code == 200
    assert update.json()[0]["is_read"] is True
    assert update.json()[0]["is_saved"] is True

    current = client.get("/api/articles").json()[0]
    assert current["is_read"] is True
    assert current["is_saved"] is True
    assert client.get("/api/feeds").json()[0]["unread_count"] == 0

    restarted_client = _client(tmp_path)
    persisted = restarted_client.get("/api/articles").json()[0]
    assert persisted["is_read"] is True
    assert persisted["is_saved"] is True


def test_feed_api_reports_invalid_and_unavailable_sources(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/feeds", json={"url": "file:///tmp/feed.xml"}).status_code == 422

    missing = client.post("/api/feeds", json={"url": "https://example.com/invalid"})
    assert missing.status_code == 200

    unknown = client.get("/api/articles", params={"feed_id": "missing"})
    assert unknown.status_code == 404


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (201, 0), (1, -1), (200, -1)],
)
def test_reader_rejects_invalid_pagination_before_listing(
    tmp_path: Path, limit: int, offset: int
) -> None:
    service = ReaderService(tmp_path / "api.db", fetcher=_fetcher)
    listing_calls = []

    def list_reader_articles(**kwargs: object) -> list[object]:
        listing_calls.append(kwargs)
        return []

    service.store.list_reader_articles = list_reader_articles

    with pytest.raises(ValueError):
        service.list_articles(limit=limit, offset=offset)

    service.list_articles(limit=1, offset=0)
    service.list_articles(limit=200, offset=0)
    assert len(listing_calls) == 2


@pytest.mark.parametrize("timeout", [0, -1, nan, inf, -inf])
def test_reader_rejects_invalid_feed_timeout_before_fetch(tmp_path: Path, timeout: float) -> None:
    fetcher_called = False

    def fetcher(*_: object, **__: object) -> FeedFetchResult:
        nonlocal fetcher_called
        fetcher_called = True
        return _fetcher("https://example.com/rss.xml", 1)

    with pytest.raises(ValueError):
        ReaderService(tmp_path / "api.db", feed_timeout_seconds=timeout, fetcher=fetcher)

    assert fetcher_called is False
    service = ReaderService(tmp_path / "api.db", feed_timeout_seconds=1, fetcher=fetcher)
    service.subscribe("https://example.com/rss.xml")
    assert fetcher_called is True


def test_missing_article_uses_article_not_found_error_and_returns_404(
    tmp_path: Path,
) -> None:
    service = ReaderService(tmp_path / "api.db", fetcher=_fetcher)

    with pytest.raises(ArticleNotFoundError):
        service.get_article("missing")

    response = TestClient(create_app(service)).get("/api/articles/missing")
    assert response.status_code == 404


def test_opinion_api_uses_contract_error_objects(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/api/feeds", json={"url": "https://example.com/rss.xml"})
    article_id = client.get("/api/articles").json()[0]["id"]

    unsupported = client.post(f"/api/articles/{article_id}/opinion")
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"]["code"] == "unsupported_target"

    invalid_body = client.post(
        f"/api/articles/{article_id}/opinion",
        json={"force_refresh": "true"},
    )
    assert invalid_body.status_code == 422
    assert invalid_body.json()["detail"]["code"] == "invalid_request"


def test_legacy_topic_research_routes_are_removed(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert client.get("/api/research/runs").status_code == 404
    assert client.post("/api/research/ingest", json={}).status_code == 404
    assert client.post("/api/research/runs/run-id/agent", json={}).status_code == 404
    assert client.get("/api/research/runs/run-id/agent/status").status_code == 404
    assert client.post("/api/research/runs/run-id/agent/stop", json={}).status_code == 404
