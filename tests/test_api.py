from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from information_agent.api import create_app
from information_agent.collection import FeedFetchResult, RawFeedEntry
from information_agent.contracts import PROJECT_TIMEZONE, ContentType
from information_agent.reader import ReaderService


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

    feeds = client.get("/api/feeds")
    assert feeds.status_code == 200
    assert feeds.json()[0]["url"] == "https://example.com/rss.xml"

    articles = client.get("/api/articles")
    assert articles.status_code == 200
    assert articles.json()[0]["title"] == "一篇用于 API 测试的文章标题"

    article_id = articles.json()[0]["id"]
    detail = client.get(f"/api/articles/{article_id}")
    assert detail.status_code == 200
    assert "足够长的 RSS 文章正文" in detail.json()["content"]


def test_feed_api_reports_invalid_and_unavailable_sources(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/feeds", json={"url": "file:///tmp/feed.xml"}).status_code == 422

    missing = client.post("/api/feeds", json={"url": "https://example.com/invalid"})
    assert missing.status_code == 200

    unknown = client.get("/api/articles", params={"feed_id": "missing"})
    assert unknown.status_code == 404
