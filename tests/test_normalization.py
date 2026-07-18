from datetime import datetime

from information_agent.collection.normalization import (
    normalize_evidence,
    normalize_url,
    parse_published_at,
)
from information_agent.contracts import PROJECT_TIMEZONE, Evidence


def test_normalize_url_removes_tracking_and_rejects_non_http() -> None:
    assert (
        normalize_url(
            "HTTPS://Example.COM:443/article?id=7&utm_source=rss&fbclid=tracking#comments"
        )
        == "https://example.com/article?id=7"
    )
    assert normalize_url("mailto:editor@example.com") is None


def test_normalize_evidence_filters_short_content_and_batches_long_content() -> None:
    items = [
        Evidence("https://example.com/short", "短内容", "不足二十字"),
        Evidence(
            "https://example.com/long?utm_medium=rss",
            " 长文章 ",
            "正文 " * 200,
            published_at=parse_published_at("Fri, 17 Jul 2026 10:30:00 +0800"),
        ),
    ]

    normalized = normalize_evidence(items)

    assert len(normalized) == 1
    assert normalized[0].source_url == "https://example.com/long"
    assert normalized[0].article_id.startswith("article-")
    assert len(normalized[0].content) > 500
    assert len(normalized[0].content_chunks) == 2
    assert all(len(chunk) <= 500 for chunk in normalized[0].content_chunks)
    assert "".join(normalized[0].content_chunks) == normalized[0].content
    assert normalized[0].processing_warnings == ["正文已拆分为 2 个批次，每批最多 500 字"]

    renormalized = normalize_evidence(normalized)
    assert renormalized[0].processing_warnings == normalized[0].processing_warnings
    assert normalized[0].published_at == datetime(2026, 7, 17, 10, 30, tzinfo=PROJECT_TIMEZONE)


def test_published_time_is_normalized_to_project_timezone() -> None:
    parsed = parse_published_at("2026-07-17T10:30:45.123456+08:00")

    assert parsed == datetime(2026, 7, 17, 10, 30, 45, tzinfo=PROJECT_TIMEZONE)
