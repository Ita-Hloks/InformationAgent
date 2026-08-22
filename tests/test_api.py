from __future__ import annotations

import importlib
from dataclasses import replace
from datetime import datetime
from math import inf, nan
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from information_agent import settings as settings_module
from information_agent.agent import AgentReport, AgentStopReason
from information_agent.api import create_app
from information_agent.collection import FeedFetchResult, RawFeedEntry
from information_agent.collection.web import MAX_PAGE_BYTES
from information_agent.common import CONTENT_BATCH_CHARS
from information_agent.contracts import PROJECT_TIMEZONE, CollectionReport, ContentType, RunStatus
from information_agent.reader import (
    ArticleAssistant,
    ArticleContextService,
    ArticleContextUnavailableError,
    ArticleNotFoundError,
    ReaderService,
)
from information_agent.reader.context import ArticleContextUrlError, fetch_public_article
from information_agent.storage import (
    AnalysisRunStatus,
    PersistedCollection,
    SQLiteCollectionStore,
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

    response = _client(tmp_path).post(
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
            self.calls: list[tuple[str, str]] = []

        def answer(self, article, question: str) -> str:
            self.calls.append((article.article.article_id, question))
            return "基于文章的回答"

    assistant = RecordingAssistant()
    client = TestClient(create_app(service, article_assistant=assistant))
    response = client.post(
        f"/api/articles/{article_id}/ask",
        json={"question": "  当前文章的核心是什么？  "},
    )

    assert response.status_code == 200
    assert response.json() == {"article_id": article_id, "answer": "基于文章的回答"}
    assert assistant.calls == [(article_id, "当前文章的核心是什么？")]
    assert (
        client.post(f"/api/articles/{article_id}/ask", json={"question": "  "}).status_code == 422
    )


class _RecordingAssistant:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def answer(self, article, question: str) -> str:
        self.calls.append((article.article.article_id, question))
        return "基于上下文的回答"


def _public_resolver(*_: object, **__: object) -> list[tuple[object, ...]]:
    return [(0, 0, 0, "", ("93.184.216.34", 443))]


def _private_resolver(*_: object, **__: object) -> list[tuple[object, ...]]:
    return [(0, 0, 0, "", ("127.0.0.1", 80))]


def test_article_context_reuses_local_article_and_requires_confirmation(tmp_path: Path) -> None:
    service = ReaderService(tmp_path / "api.db", fetcher=_fetcher)
    service.subscribe("https://example.com/rss.xml")
    fetch_calls: list[str] = []

    def fetcher(*args: object, **kwargs: object) -> object:
        fetch_calls.append(str(args[0]))
        raise AssertionError("本地文章不应重复抓取")

    context_service = ArticleContextService(
        service,
        fetcher=fetcher,
        resolver=_public_resolver,
    )
    assistant = _RecordingAssistant()
    client = TestClient(
        create_app(service, article_assistant=assistant, article_context=context_service)
    )

    resolved = client.post("/api/article-context", json={"url": "https://example.com/article-1"})
    assert resolved.status_code == 200
    payload = resolved.json()
    assert payload["is_local"] is True
    assert payload["confirmed"] is False
    context_id = payload["context_id"]
    assert fetch_calls == []

    blocked = client.post(
        f"/api/article-context/{context_id}/ask",
        json={"question": "文章的核心是什么？"},
    )
    assert blocked.status_code == 409
    assert assistant.calls == []

    confirmed = client.post(f"/api/article-context/{context_id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmed"] is True

    answer = client.post(
        f"/api/article-context/{context_id}/ask",
        json={"question": "  文章的核心是什么？  "},
    )
    assert answer.status_code == 200
    assert answer.json()["answer"] == "基于上下文的回答"
    assert assistant.calls == [
        (service.list_articles()[0].article.article_id, "文章的核心是什么？")
    ]


def test_article_context_external_url_is_temporary_and_confirms_before_llm(
    tmp_path: Path,
) -> None:
    service = ReaderService(tmp_path / "api.db", fetcher=_fetcher)
    fetch_calls: list[str] = []

    def fetcher(url: str, **_: object) -> object:
        fetch_calls.append(url)
        return SimpleNamespace(
            title="外部文章标题",
            content="外部文章正文内容足够长，用于测试临时上下文。",
        )

    context_service = ArticleContextService(
        service,
        fetcher=fetcher,
        resolver=_public_resolver,
    )
    assistant = _RecordingAssistant()
    client = TestClient(
        create_app(service, article_assistant=assistant, article_context=context_service)
    )

    resolved = client.post("/api/article-context", json={"url": "https://news.example.com/story"})
    assert resolved.status_code == 200
    payload = resolved.json()
    assert payload["title"] == "外部文章标题"
    assert payload["is_local"] is False
    assert fetch_calls == ["https://news.example.com/story"]
    assert service.list_articles() == []

    blocked = client.post(
        f"/api/article-context/{payload['context_id']}/ask",
        json={"question": "这篇文章讲了什么？"},
    )
    assert blocked.status_code == 409
    assert assistant.calls == []

    client.post(f"/api/article-context/{payload['context_id']}/confirm")
    answer = client.post(
        f"/api/article-context/{payload['context_id']}/ask",
        json={"question": "这篇文章讲了什么？"},
    )
    assert answer.status_code == 200
    assert assistant.calls[0][1] == "这篇文章讲了什么？"
    assert service.list_articles() == []


def test_article_context_rejects_private_resolution_without_calling_llm(tmp_path: Path) -> None:
    service = ReaderService(tmp_path / "api.db", fetcher=_fetcher)
    fetch_calls: list[str] = []

    def fetcher(url: str, **_: object) -> object:
        fetch_calls.append(url)
        raise AssertionError("不安全 URL 不应抓取")

    context_service = ArticleContextService(
        service,
        fetcher=fetcher,
        resolver=_private_resolver,
    )
    assistant = _RecordingAssistant()
    client = TestClient(
        create_app(service, article_assistant=assistant, article_context=context_service)
    )

    response = client.post("/api/article-context", json={"url": "https://news.example.com/story"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsafe_url"
    assert fetch_calls == []
    assert assistant.calls == []

    local_service = ReaderService(tmp_path / "local-api.db", fetcher=_fetcher)
    local_service.subscribe("https://example.com/rss.xml")
    local_context_service = ArticleContextService(
        local_service,
        fetcher=fetcher,
        resolver=_private_resolver,
    )
    local_client = TestClient(
        create_app(
            local_service,
            article_assistant=assistant,
            article_context=local_context_service,
        )
    )
    local_response = local_client.post(
        "/api/article-context",
        json={"url": "https://example.com/article-1"},
    )
    assert local_response.status_code == 422
    assert local_response.json()["detail"]["code"] == "unsafe_url"

    for url in (
        "http://localhost/story",
        "file:///tmp/story",
        "https://user:pass@example.com/story",
        "https://@example.com/story",
        "https://example.com:0/story",
    ):
        assert client.post("/api/article-context", json={"url": url}).status_code == 422


def test_article_context_maps_fetch_failure_without_calling_llm(tmp_path: Path) -> None:
    service = ReaderService(tmp_path / "api.db", fetcher=_fetcher)

    def fetcher(*_: object, **__: object) -> object:
        raise ArticleContextUnavailableError("网页正文为空")

    context_service = ArticleContextService(
        service,
        fetcher=fetcher,
        resolver=_public_resolver,
    )
    assistant = _RecordingAssistant()
    client = TestClient(
        create_app(service, article_assistant=assistant, article_context=context_service)
    )

    response = client.post("/api/article-context", json={"url": "https://news.example.com/empty"})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "context_unavailable"
    assert assistant.calls == []


def test_public_article_fetch_parses_title_and_rejects_unsafe_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __init__(self) -> None:
            self._payload = (
                "<html><head><title>网页标题</title></head>"
                "<body><article>正文</article></body></html>"
            ).encode()

        def read(self, _limit: int) -> bytes:
            payload, self._payload = self._payload, b""
            return payload

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "information_agent.reader.context._extract_text",
        lambda _html: "解析后的网页正文内容足够长，用于验证外部页面正文提取。",
    )
    result = fetch_public_article(
        "https://news.example.com/story",
        resolver=_public_resolver,
        opener=lambda *_args, **_kwargs: Response(),
    )
    assert result.title == "网页标题"
    assert result.content == "解析后的网页正文内容足够长，用于验证外部页面正文提取。"

    class RedirectResponse:
        status = 302
        headers = {"Location": "http://127.0.0.1/private"}

        def close(self) -> None:
            return None

    calls: list[str] = []

    def redirect_opener(request, **_kwargs: object) -> RedirectResponse:
        calls.append(request.full_url)
        return RedirectResponse()

    with pytest.raises(ArticleContextUrlError):
        fetch_public_article(
            "https://news.example.com/story",
            resolver=_public_resolver,
            opener=redirect_opener,
        )
    assert calls == ["https://news.example.com/story"]


def test_public_article_fetch_rejects_oversized_response_without_reading_body() -> None:
    class OversizedResponse:
        status = 200
        headers = {
            "content-type": "text/html",
            "content-length": str(MAX_PAGE_BYTES + 1),
        }

        def read(self, _limit: int) -> bytes:
            raise AssertionError("超限响应不应读取正文")

        def close(self) -> None:
            return None

    with pytest.raises(ArticleContextUnavailableError, match="超过大小限制"):
        fetch_public_article(
            "https://news.example.com/story",
            resolver=_public_resolver,
            opener=lambda *_args, **_kwargs: OversizedResponse(),
        )


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


def test_research_runs_api_lists_persisted_runs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "api.db"
    run_id = SQLiteCollectionStore(database_path).start_run("AI", ["https://example.com/rss.xml"])

    response = _client(tmp_path).get("/api/research/runs")

    assert response.status_code == 200
    assert response.json()["runs"][0]["run_id"] == run_id
    assert response.json()["runs"][0]["topic"] == "AI"


def test_research_ingest_api_returns_persisted_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_ingest(topic: str, feeds: list[str], **kwargs: object) -> PersistedCollection:
        calls.append({"topic": topic, "feeds": feeds, **kwargs})
        return PersistedCollection(
            run_id="run-api",
            report=CollectionReport(topic, RunStatus.COMPLETED, []),
        )

    api_app_module = importlib.import_module("information_agent.api.app")
    monkeypatch.setattr(api_app_module, "ingest", fake_ingest)

    response = _client(tmp_path).post(
        "/api/research/ingest",
        json={
            "topic": "AI",
            "feeds": ["https://example.com/rss.xml"],
            "timeout_seconds": 12,
            "limit": 3,
        },
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-api"
    assert response.json()["status"] == "completed"
    assert calls == [
        {
            "topic": "AI",
            "feeds": ["https://example.com/rss.xml"],
            "database_path": tmp_path / "api.db",
            "timeout_seconds": 12.0,
            "limit": 3,
        }
    ]


def test_research_agent_api_returns_agent_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_agent_run(run_id: str, **kwargs: object) -> AgentReport:
        calls.append({"run_id": run_id, **kwargs})
        return AgentReport(
            run_id=run_id,
            topic="AI",
            status=RunStatus.COMPLETED,
            articles=[],
            plans=[],
            answers=[],
            final_answer="已完成核查。",
            evidence_ids=(),
            uncertainties=(),
            steps=1,
            stop_reason=AgentStopReason.FINISHED,
            analysis_run_id="analysis-api",
        )

    api_app_module = importlib.import_module("information_agent.api.app")
    monkeypatch.setattr(api_app_module, "agent_run", fake_agent_run)

    response = _client(tmp_path).post(
        "/api/research/runs/run-api/agent",
        json={"timeout_seconds": 30, "max_steps": 2, "max_attempts": 1},
    )

    assert response.status_code == 200
    assert response.json()["analysis_run_id"] == "analysis-api"
    assert response.json()["final_answer"] == "已完成核查。"
    assert calls == [
        {
            "run_id": "run-api",
            "database_path": tmp_path / "api.db",
            "timeout_seconds": 30.0,
            "max_steps": 2,
            "max_attempts": 1,
        }
    ]


def test_research_agent_api_restores_latest_persisted_report(tmp_path: Path) -> None:
    database_path = tmp_path / "api.db"
    store = SQLiteCollectionStore(database_path)
    run_id = store.start_run("AI", ["https://example.com/rss.xml"])
    store.complete_run(run_id, CollectionReport("AI", RunStatus.COMPLETED, []), [])
    analysis_run = store.create_analysis_run(run_id, "agent_research", {})
    store.record_analysis_artifact(
        analysis_run.id,
        "agent_report",
        {
            "run_id": run_id,
            "topic": "AI",
            "status": "completed",
            "articles": [],
            "plans": [],
            "answers": [],
            "final_answer": "已保存的结论",
            "evidence_ids": [],
            "citations": [],
            "uncertainties": [],
            "steps": 1,
            "stop_reason": "finished",
            "errors": [],
        },
        artifact_key="finalize:attempt-1:result",
    )
    store.set_analysis_run_status(analysis_run.id, AnalysisRunStatus.COMPLETED)

    response = _client(tmp_path).get(f"/api/research/runs/{run_id}/agent")

    assert response.status_code == 200
    assert response.json()["analysis_run_id"] == analysis_run.id
    assert response.json()["final_answer"] == "已保存的结论"


def test_research_agent_api_returns_null_without_persisted_report(tmp_path: Path) -> None:
    store = SQLiteCollectionStore(tmp_path / "api.db")
    run_id = store.start_run("AI", ["https://example.com/rss.xml"])

    response = _client(tmp_path).get(f"/api/research/runs/{run_id}/agent")

    assert response.status_code == 200
    assert response.json() is None


def test_research_agent_api_distinguishes_missing_and_unready_runs(tmp_path: Path) -> None:
    client = _client(tmp_path)
    missing = client.post("/api/research/runs/missing/agent", json={})
    run_id = SQLiteCollectionStore(tmp_path / "api.db").start_run(
        "AI", ["https://example.com/rss.xml"]
    )
    unready = client.post(f"/api/research/runs/{run_id}/agent", json={})

    assert missing.status_code == 404
    assert unready.status_code == 409


def test_research_agent_api_rejects_domain_value_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_agent_run(*_: object, **__: object) -> AgentReport:
        raise ValueError("Agent 参数组合无效")

    api_app_module = importlib.import_module("information_agent.api.app")
    monkeypatch.setattr(api_app_module, "agent_run", invalid_agent_run)

    response = _client(tmp_path).post("/api/research/runs/run-api/agent", json={})

    assert response.status_code == 422
