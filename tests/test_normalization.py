from datetime import datetime

from information_agent.collection import RawFeedEntry
from information_agent.common import normalize_url
from information_agent.contracts import PROJECT_TIMEZONE
from information_agent.normalization import normalize_evidence, parse_published_at


def test_normalize_url_removes_tracking_and_rejects_non_http() -> None:
    assert (
        normalize_url(
            "HTTPS://Example.COM:443/article?id=7&utm_source=rss&fbclid=tracking#comments"
        )
        == "https://example.com/article?id=7"
    )
    assert normalize_url("mailto:editor@example.com") is None


def test_normalize_url_canonicalizes_equivalent_idna_hostnames() -> None:
    unicode_url = "HTTPS://B\u00dcCHER.Example:8443/article?item=1"
    ascii_url = "https://XN--BCHER-KVA.example:8443/article?item=1"

    assert normalize_url(unicode_url) == "https://xn--bcher-kva.example:8443/article?item=1"
    assert normalize_url(ascii_url) == normalize_url(unicode_url)


def test_normalize_url_rejects_invalid_idna_hostnames() -> None:
    assert normalize_url("https://xn--.example/article") is None
    assert normalize_url("https://xn--a.example/article") is None
    assert normalize_url("https://xn--fa-hia.example/article") is None
    assert normalize_url("https://\ud800.example/article") is None


def test_normalize_evidence_filters_short_content_and_batches_long_content() -> None:
    items = [
        RawFeedEntry("https://example.com/short", "短内容", "不足二十字"),
        RawFeedEntry(
            "https://example.com/long?utm_medium=rss",
            " 长文章 ",
            "正文。" * 800,
            published_at=parse_published_at("Fri, 17 Jul 2026 10:30:00 +0800"),
        ),
    ]

    normalized = normalize_evidence(items)

    assert len(normalized) == 1
    assert normalized[0].source_url == "https://example.com/long"
    assert normalized[0].article_id.startswith("article-")
    assert len(normalized[0].content) > 2_000
    assert len(normalized[0].content_chunks) == 2
    assert all(len(chunk) <= 2_000 for chunk in normalized[0].content_chunks)
    assert "".join(normalized[0].content_chunks) == normalized[0].content
    assert normalized[0].processing_warnings == ("正文已拆分为 2 个批次，每批最多 2000 字",)

    assert normalized[0].published_at == datetime(2026, 7, 17, 10, 30, tzinfo=PROJECT_TIMEZONE)


def test_content_batches_prefer_natural_boundaries_without_changing_text() -> None:
    content = "甲" * 300 + "。" + "乙" * 300 + "。"
    normalized = normalize_evidence(
        [RawFeedEntry("https://example.com/article", "文章", content)],
        min_content_chars=1,
        content_batch_chars=500,
    )

    assert normalized[0].content == content
    assert normalized[0].content_chunks[0].endswith("。")
    assert "".join(normalized[0].content_chunks) == content


def test_published_time_is_normalized_to_project_timezone() -> None:
    parsed = parse_published_at("2026-07-17T10:30:45.123456+08:00")

    assert parsed == datetime(2026, 7, 17, 10, 30, 45, tzinfo=PROJECT_TIMEZONE)
