from __future__ import annotations

from datetime import datetime
from math import inf, nan
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from information_agent.api import create_app
from information_agent.collection import FeedFetchResult, RawFeedEntry
from information_agent.contracts import PROJECT_TIMEZONE, ContentType
from information_agent.reader import ArticleNotFoundError, ReaderService


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
