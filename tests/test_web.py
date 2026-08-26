import math
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError

import pytest

from information_agent.collection import RawFeedEntry
from information_agent.collection.web import (
    MAX_PAGE_BYTES,
    ArticleFetchResult,
    _extract_text,
    _guess_encoding,
    augment_evidence,
    fetch_article,
)
from information_agent.common import ContentBlock
from information_agent.contracts import ContentType


def test_extract_text_strips_script_and_style() -> None:
    html = (
        "<html><head>"
        "<script>alert('xss')</script>"
        "<style>.nav{color:red}</style>"
        "</head><body>"
        "<article><p>正文 &amp; 内容。这篇正文用于测试提取功能是否正常工作。</p></article>"
        "</body></html>"
    )
    result = _extract_text(html)
    assert result is not None
    assert "正文" in result
    assert "&" in result
    assert "内容" in result
    assert "xss" not in result
    assert "nav" not in result


def test_extract_text_decodes_html_entities() -> None:
    html = (
        "<html><body><article>"
        "<p>&lt;tag&gt; &amp; &quot;text&quot; 这篇正文用于测试实体解码功能。</p>"
        "</article></body></html>"
    )
    result = _extract_text(html)
    assert result is not None
    assert "<tag>" in result
    assert "&" in result
    assert '"text"' in result


def test_fetch_article_returns_text_for_valid_html(monkeypatch) -> None:
    class FakeResponse:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def read(self, _: int) -> bytes:
            return (
                b"<html><body><article><p>"
                b"\xe8\xbf\x99\xe6\x98\xaf\xe4\xb8\x80\xe7\xaf\x87"
                b"\xe7\x94\xa8\xe4\xba\x8e\xe6\xb5\x8b\xe8\xaf\x95"
                b"\xe6\x96\x87\xe7\xab\xa0\xe6\xad\xa3\xe6\x96\x87"
                b"\xe6\x8a\x93\xe5\x8f\x96\xe5\x8a\x9f\xe8\x83\xbd"
                b"\xe7\x9a\x84\xe5\xae\x8c\xe6\x95\xb4\xe5\x86\x85"
                b"\xe5\xae\xb9\xef\xbc\x8c\xe5\x8c\x85\xe5\x90\xab"
                b"\xe8\xb6\xb3\xe5\xa4\x9f\xe9\x95\xbf\xe5\xba\xa6"
                b"\xe4\xbb\xa5\xe9\x80\x9a\xe8\xbf\x87\xe6\x9c\x80"
                b"\xe5\xb0\x8f\xe5\xad\x97\xe6\x95\xb0\xe9\x99\x90"
                b"\xe5\x88\xb6\xe7\x9a\x84\xe9\xaa\x8c\xe8\xaf\x81"
                b"\xe3\x80\x82</p></article></body></html>"
            )

    def fake_urlopen(request, timeout: float) -> FakeResponse:
        assert request.full_url == "https://example.com/article"
        assert timeout == 15
        return FakeResponse()

    monkeypatch.setattr("information_agent.collection.web.urlopen", fake_urlopen)

    result = fetch_article("https://example.com/article")
    assert result is not None
    assert "这是一篇用于测试文章正文抓取功能的完整内容" in result


def test_fetch_article_decodes_gbk(monkeypatch) -> None:
    class FakeResponse:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def read(self, _: int) -> bytes:
            return (
                "<html><body><article><p>这是一篇用于测试中文编码的完整文章正文内容"
                "包含足够长度以通过最小字数限制的验证。</p></article></body></html>"
            ).encode("gbk")

    def fake_urlopen(request, timeout: float) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr("information_agent.collection.web.urlopen", fake_urlopen)

    result = fetch_article("https://example.com/gbk-page")
    assert result is not None
    assert "这是一篇用于测试中文编码的" in result


def test_fetch_article_returns_none_for_short_content(monkeypatch) -> None:
    class FakeResponse:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def read(self, _: int) -> bytes:
            return b"<html><body><article><p>a</p></article></body></html>"

    def fake_urlopen(request, timeout: float) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr("information_agent.collection.web.urlopen", fake_urlopen)

    assert fetch_article("https://example.com/short") is None


def test_fetch_article_returns_none_for_non_http_url() -> None:
    assert fetch_article("ftp://example.com/file") is None
    assert fetch_article("not-a-url") is None


@pytest.mark.parametrize("timeout", [0, -1, math.nan, math.inf, -math.inf])
def test_fetch_article_rejects_invalid_timeout_before_downstream_calls(
    monkeypatch, timeout
) -> None:
    rate_limiter_calls: list[str] = []
    network_calls: list[float] = []

    def fake_rate_limiter(domain: str) -> None:
        rate_limiter_calls.append(domain)

    def fake_urlopen(request, timeout: float) -> None:
        network_calls.append(timeout)
        raise URLError("unavailable")

    monkeypatch.setattr(
        "information_agent.collection.web._rate_limiter.wait_if_needed", fake_rate_limiter
    )
    monkeypatch.setattr("information_agent.collection.web.urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="timeout must be a positive finite number"):
        fetch_article("https://example.com/article", timeout=timeout)

    assert rate_limiter_calls == []
    assert network_calls == []

    assert fetch_article("https://example.com/article", timeout=1) is None
    assert rate_limiter_calls == ["example.com"]
    assert network_calls == [1]


def test_fetch_article_returns_none_on_network_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout: float) -> None:
        raise URLError("连接失败")

    monkeypatch.setattr("information_agent.collection.web.urlopen", fake_urlopen)

    assert fetch_article("https://example.com/error") is None


def test_fetch_article_returns_none_on_timeout(monkeypatch) -> None:
    def fake_urlopen(request, timeout: float) -> None:
        raise OSError("超时")

    monkeypatch.setattr("information_agent.collection.web.urlopen", fake_urlopen)

    assert fetch_article("https://example.com/timeout") is None


def test_fetch_article_respects_max_page_bytes(monkeypatch) -> None:
    class FakeResponse:
        headers = {"Content-Length": "99999999"}

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def read(self, _: int) -> bytes:
            raise AssertionError("body should not be read")

    def fake_urlopen(request, timeout: float) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr("information_agent.collection.web.urlopen", fake_urlopen)

    assert fetch_article("https://example.com/huge") is None


def test_fetch_article_rejects_oversized_body_with_malformed_content_length(monkeypatch) -> None:
    class FakeResponse:
        headers = {"Content-Length": " 1"}

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == MAX_PAGE_BYTES + 1
            return b"x" * limit

    monkeypatch.setattr(
        "information_agent.collection.web.urlopen", lambda *args, **kwargs: FakeResponse()
    )

    assert fetch_article("https://example.com/malformed-content-length") is None


def test_augment_evidence_skips_non_summary_items() -> None:
    items = [
        RawFeedEntry(
            "https://example.com/a",
            "已有正文",
            "这是完整的正文内容。",
            content_type=ContentType.RSS_CONTENT,
        ),
    ]
    result = augment_evidence(items)
    assert len(result) == 1
    assert result[0].content_type is ContentType.RSS_CONTENT
    assert result[0].content == "这是完整的正文内容。"


@pytest.mark.parametrize(
    "timeout,max_workers",
    [(0, 1), (-1, 1), (math.nan, 1), (math.inf, 1), (-math.inf, 1), (1, 0), (1, -1)],
)
def test_augment_evidence_rejects_invalid_resources_before_downstream_calls(
    monkeypatch, timeout, max_workers
) -> None:
    pool_calls: list[int] = []

    def fake_pool(*args, **kwargs):
        pool_calls.append(kwargs["max_workers"])
        return ThreadPoolExecutor(*args, **kwargs)

    monkeypatch.setattr("information_agent.collection.web.ThreadPoolExecutor", fake_pool)
    all_content = RawFeedEntry(
        "https://example.com/content",
        "Complete",
        "Full article",
        content_type=ContentType.RSS_CONTENT,
    )

    for items in ([], [object()], [all_content]):
        with pytest.raises(ValueError):
            augment_evidence(items, timeout=timeout, max_workers=max_workers)

    assert pool_calls == []

    monkeypatch.setattr(
        "information_agent.collection.web.fetch_article",
        lambda *_args, **_kwargs: "Fetched article",
    )
    result = augment_evidence(
        [
            RawFeedEntry(
                "https://example.com/summary",
                "Summary",
                "Brief",
                content_type=ContentType.RSS_SUMMARY,
            )
        ],
        timeout=1,
        max_workers=1,
    )

    assert pool_calls == [1]
    assert result[0].content == "Fetched article"
    assert result[0].content_type is ContentType.RSS_CONTENT


def test_augment_evidence_fetches_for_summary_items(monkeypatch) -> None:
    def fake_fetch(url: str, **kwargs) -> str | None:
        return "这是从网页抓取到的完整正文内容。"

    monkeypatch.setattr("information_agent.collection.web.fetch_article", fake_fetch)

    items = [
        RawFeedEntry(
            "https://example.com/a",
            "标题1",
            "原始摘要",
            content_type=ContentType.RSS_SUMMARY,
        ),
    ]
    result = augment_evidence(items)
    assert len(result) == 1
    assert result[0].content == "这是从网页抓取到的完整正文内容。"
    assert result[0].content_type is ContentType.RSS_CONTENT


def test_augment_evidence_prefers_fetched_image_over_feed_image(monkeypatch) -> None:
    fetched_image = "https://imgslim.example/image.jpg"

    def fake_fetch(url: str, **kwargs) -> ArticleFetchResult:
        assert url == "https://example.com/article"
        assert kwargs["_return_details"] is True
        return ArticleFetchResult(
            content="这是从网页抓取到的完整正文内容。",
            content_blocks=(ContentBlock(type="image", url=fetched_image),),
            image_url=fetched_image,
        )

    monkeypatch.setattr("information_agent.collection.web.fetch_article", fake_fetch)

    result = augment_evidence(
        [
            RawFeedEntry(
                source_url="https://example.com/article",
                title="标题",
                content="原始摘要",
                content_type=ContentType.RSS_SUMMARY,
                image_url="https://toolkit.example/feishu-image?token=feed-image",
            )
        ]
    )

    assert result[0].image_url == fetched_image
    assert result[0].content_blocks[0].url == fetched_image


def test_augment_evidence_preserves_other_fields(monkeypatch) -> None:
    def fake_fetch(url: str, **kwargs) -> str | None:
        return "补上的正文内容。"

    monkeypatch.setattr("information_agent.collection.web.fetch_article", fake_fetch)

    items = [
        RawFeedEntry(
            source_url="https://example.com/article",
            title="原始标题",
            content="摘要",
            feed_url="https://example.com/rss",
            site_url="https://example.com",
            author="作者",
            categories=("科技",),
            language="zh-cn",
            content_type=ContentType.RSS_SUMMARY,
        ),
    ]
    result = augment_evidence(items)
    assert len(result) == 1
    item = result[0]
    assert item.source_url == "https://example.com/article"
    assert item.title == "原始标题"
    assert item.feed_url == "https://example.com/rss"
    assert item.site_url == "https://example.com"
    assert item.author == "作者"
    assert item.categories == ("科技",)
    assert item.language == "zh-cn"
    assert item.content_type is ContentType.RSS_CONTENT


def test_augment_evidence_falls_back_when_fetch_fails(monkeypatch) -> None:
    def fake_fetch(url: str, **kwargs) -> str | None:
        return None

    monkeypatch.setattr("information_agent.collection.web.fetch_article", fake_fetch)

    items = [
        RawFeedEntry(
            "https://example.com/fail",
            "标题",
            "原始摘要内容",
            content_type=ContentType.RSS_SUMMARY,
        ),
    ]
    result = augment_evidence(items)
    assert len(result) == 1
    assert result[0].content == "原始摘要内容"
    assert result[0].content_type is ContentType.RSS_SUMMARY


def test_augment_evidence_mixed_items(monkeypatch) -> None:
    fetched_urls: list[str] = []

    def fake_fetch(url: str, **kwargs) -> str | None:
        fetched_urls.append(url)
        if "success" in url:
            return f"来自 {url} 的正文。"
        return None

    monkeypatch.setattr("information_agent.collection.web.fetch_article", fake_fetch)

    items = [
        RawFeedEntry(
            "https://example.com/has-content", "A", "已有正文", content_type=ContentType.RSS_CONTENT
        ),
        RawFeedEntry(
            "https://example.com/success", "B", "摘要", content_type=ContentType.RSS_SUMMARY
        ),
        RawFeedEntry("https://example.com/fail", "C", "摘要", content_type=ContentType.RSS_SUMMARY),
    ]
    result = augment_evidence(items)
    assert len(result) == 3
    assert result[0].content == "已有正文"
    assert result[1].content == "来自 https://example.com/success 的正文。"
    assert result[1].content_type is ContentType.RSS_CONTENT
    assert result[2].content == "摘要"
    assert result[2].content_type is ContentType.RSS_SUMMARY
    assert set(fetched_urls) == {"https://example.com/success", "https://example.com/fail"}


def test_fetch_article_returns_none_on_http_403(monkeypatch) -> None:
    def fake_urlopen(request, timeout: float) -> None:
        raise HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr("information_agent.collection.web.urlopen", fake_urlopen)

    assert fetch_article("https://example.com/forbidden") is None


def test_fetch_article_returns_none_on_http_429(monkeypatch) -> None:
    def fake_urlopen(request, timeout: float) -> None:
        raise HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("information_agent.collection.web.urlopen", fake_urlopen)

    assert fetch_article("https://example.com/rate-limited") is None


def test_domain_rate_limiter_allows_fast_requests() -> None:
    from information_agent.collection.web import DomainRateLimiter

    limiter = DomainRateLimiter(requests_per_second=10)
    for _ in range(10):
        limiter.wait_if_needed("example.com")


def test_domain_rate_limiter_blocks_excessive_requests() -> None:
    from information_agent.collection.web import DomainRateLimiter

    limiter = DomainRateLimiter(requests_per_second=1)
    limiter.wait_if_needed("slow.example")
    start = time.monotonic()
    limiter.wait_if_needed("slow.example")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.5


def test_domain_rate_limiter_separates_domains() -> None:
    from information_agent.collection.web import DomainRateLimiter

    limiter = DomainRateLimiter(requests_per_second=1)
    limiter.wait_if_needed("a.example")
    start = time.monotonic()
    limiter.wait_if_needed("b.example")
    assert time.monotonic() - start < 0.5


def test_guess_encoding_accepts_case_insensitive_whitespace_padded_name() -> None:
    class FakeResponse:
        headers = {"Content-Type": "text/html;  ChArSeT = gbk"}

    assert _guess_encoding(FakeResponse()) == "gbk"


def test_guess_encoding_unwraps_quoted_value() -> None:
    class FakeResponse:
        headers = {"Content-Type": 'text/html; charset = "  gbk  "'}

    assert _guess_encoding(FakeResponse()) == "gbk"
    FakeResponse.headers = {"Content-Type": "text/html; charset = '  gbk  '"}
    assert _guess_encoding(FakeResponse()) == "gbk"


def test_fetch_article_falls_back_from_empty_or_unsupported_charset(monkeypatch) -> None:
    class FakeResponse:
        headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def read(self, _: int) -> bytes:
            return (
                b"<html><body><article><p>This UTF-8 text is long enough to extract."
                b"</p></article></body></html>"
            )

    monkeypatch.setattr(
        "information_agent.collection.web.urlopen", lambda *args, **kwargs: FakeResponse()
    )

    for declared_charset in ("", "unsupported-charset"):
        FakeResponse.headers = {"Content-Type": f"text/html; charset={declared_charset}"}
        assert fetch_article(f"https://{declared_charset or 'empty'}.example/fallback") is not None
