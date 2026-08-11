import asyncio
import math
from urllib.error import HTTPError

import pytest

from information_agent.collection._http import content_length_exceeds_limit
from information_agent.collection.rss import (
    MAX_FEED_BYTES,
    _entry_content,
    _plain_text,
    fetch_feed,
    fetch_feed_async,
    fetch_feed_with_cache,
)
from information_agent.contracts import ContentType
from information_agent.normalization import normalize_evidence


@pytest.mark.parametrize("timeout", [0, -1, math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("fetcher", [fetch_feed, fetch_feed_with_cache])
def test_sync_fetch_helpers_reject_invalid_timeouts_before_urlopen(
    monkeypatch, fetcher, timeout
) -> None:
    network_calls: list[float] = []

    def fake_urlopen(*_args, **kwargs) -> None:
        network_calls.append(kwargs["timeout"])
        raise HTTPError("https://example.com/rss.xml", 304, "Not Modified", {}, None)

    monkeypatch.setattr("information_agent.collection.rss.urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="timeout must be a positive finite number"):
        fetcher("https://example.com/rss.xml", timeout=timeout)

    assert network_calls == []

    fetcher("https://example.com/rss.xml", timeout=1)
    assert network_calls == [1]


@pytest.mark.parametrize("timeout", [0, -1, math.nan, math.inf, -math.inf])
def test_fetch_feed_async_rejects_invalid_timeouts_before_session_access(
    monkeypatch, timeout
) -> None:
    request_timeout_calls: list[float] = []
    session_calls: list[tuple[str, object]] = []

    class FakeContent:
        async def iter_chunked(self, _: int):
            yield b"<rss version='2.0'><channel /></rss>"

    class FakeResponse:
        headers = {"Content-Length": "0"}
        content = FakeContent()

        def raise_for_status(self) -> None:
            return None

    class RequestContext:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *args) -> None:
            return None

    class FakeSession:
        def get(self, url: str, **kwargs) -> RequestContext:
            session_calls.append((url, kwargs["timeout"]))
            return RequestContext()

    def fake_client_timeout(*, total: float) -> object:
        request_timeout_calls.append(total)
        return object()

    monkeypatch.setattr(
        "information_agent.collection.rss.aiohttp.ClientTimeout", fake_client_timeout
    )

    with pytest.raises(ValueError, match="timeout must be a positive finite number"):
        asyncio.run(fetch_feed_async("https://example.com/rss.xml", timeout, session=FakeSession()))

    assert request_timeout_calls == []
    assert session_calls == []

    valid_result = asyncio.run(
        fetch_feed_async("https://example.com/rss.xml", 1, session=FakeSession())
    )
    assert valid_result == []
    assert request_timeout_calls == [1]
    assert session_calls[0][0] == "https://example.com/rss.xml"


def test_plain_text_removes_html_and_decodes_entities() -> None:
    assert _plain_text("<p>Agent &amp; RSS</p>") == "Agent & RSS"


def test_plain_text_preserves_article_block_boundaries() -> None:
    assert _plain_text("<h2>第一篇</h2><p>正文一</p><p>正文二</p>") == ("第一篇\n正文一\n正文二")


def test_plain_text_ignores_embedded_scripts_and_styles() -> None:
    assert (
        _plain_text("<p>正文</p><script>fake summary</script><style>fake style</style>") == "正文"
    )


def test_plain_text_ignores_code_elements() -> None:
    html = "<p>正文</p><pre>print('secret')</pre><p>结论 <code>x &lt; 1</code></p>"
    assert _plain_text(html) == "正文\n结论"


def test_entry_content_uses_later_non_empty_content_block() -> None:
    content, content_type = _entry_content(
        {"content": [{"value": "<p> </p>"}, {"value": "<p>完整正文</p>"}]}
    )

    assert content == "完整正文"
    assert content_type is ContentType.RSS_CONTENT


def test_entry_content_falls_back_to_summary_when_blocks_are_empty() -> None:
    content, content_type = _entry_content(
        {
            "content": [{"value": ""}, {"value": "<script>empty</script>"}],
            "summary": "<p>摘要正文</p>",
        }
    )

    assert content == "摘要正文"
    assert content_type is ContentType.RSS_SUMMARY


def test_entry_content_uses_earliest_non_empty_content_block() -> None:
    content, content_type = _entry_content(
        {"content": [{"value": "<p>第一个正文</p>"}, {"value": "<p>第二个正文</p>"}]}
    )

    assert content == "第一个正文"
    assert content_type is ContentType.RSS_CONTENT


def test_fetch_feed_keeps_each_entry_as_a_separate_article(monkeypatch) -> None:
    payload = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>中文科技源</title>
        <link>https://example.com/</link>
        <item>
          <title>第一篇文章</title>
          <guid>entry-1</guid>
          <link>https://example.com/article-1</link>
          <description><![CDATA[<p>第一篇正文。</p>]]></description>
        </item>
        <item>
          <title>第二篇文章</title>
          <guid>entry-2</guid>
          <link>https://example.com/article-2</link>
          <description><![CDATA[<p>第二篇正文。</p>]]></description>
        </item>
      </channel>
    </rss>""".encode()

    class FakeResponse:
        headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def read(self, _: int) -> bytes:
            return payload

    monkeypatch.setattr(
        "information_agent.collection.rss.urlopen",
        lambda request, timeout: FakeResponse(),
    )

    items = fetch_feed("https://example.com/rss.xml", timeout=5)

    assert [(item.source_url, item.title, item.content) for item in items] == [
        ("https://example.com/article-1", "第一篇文章", "第一篇正文。"),
        ("https://example.com/article-2", "第二篇文章", "第二篇正文。"),
    ]


def test_fetch_feed_populates_article_and_source_fields(monkeypatch) -> None:
    payload = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"
      xmlns:content="http://purl.org/rss/1.0/modules/content/"
      xmlns:dc="http://purl.org/dc/elements/1.1/">
      <channel>
        <title>示例科技</title>
        <link>https://example.com/</link>
        <language>zh-CN</language>
        <item>
          <title>人工智能模型发布</title>
          <guid>article-guid-1</guid>
          <link>https://example.com/article?id=1&amp;utm_source=rss</link>
          <pubDate>Thu, 17 Jul 2025 09:30:00 +0800</pubDate>
          <dc:creator>示例作者</dc:creator>
          <category>人工智能</category>
          <content:encoded><![CDATA[
            <p>这是一篇用于测试 RSS 完整正文和元数据提取的文章内容。</p>
          ]]></content:encoded>
        </item>
      </channel>
    </rss>""".encode()

    class FakeResponse:
        headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def read(self, _: int) -> bytes:
            return payload

    def fake_urlopen(request, timeout: float):
        assert request.full_url == "https://example.com/rss.xml"
        assert timeout == 5
        return FakeResponse()

    monkeypatch.setattr("information_agent.collection.rss.urlopen", fake_urlopen)

    raw_items = fetch_feed("https://example.com/rss.xml?utm_source=test", timeout=5)
    items = normalize_evidence(raw_items)

    assert raw_items[0].entry_id == "article-guid-1"
    assert len(items) == 1
    item = items[0]
    assert item.source_url == "https://example.com/article?id=1"
    assert item.feed_url == "https://example.com/rss.xml"
    assert item.site_url == "https://example.com/"
    assert item.author == "示例作者"
    assert item.categories == ("人工智能",)
    assert item.language == "zh-cn"
    assert item.content_type is ContentType.RSS_CONTENT
    assert item.article_id.startswith("article-")


def test_fetch_feed_with_cache_sends_validators_and_handles_not_modified(monkeypatch) -> None:
    def fake_urlopen(request, timeout: float):
        assert request.get_header("If-none-match") == '"feed-v1"'
        assert request.get_header("If-modified-since") == "Thu, 17 Jul 2025 09:30:00 GMT"
        assert timeout == 5
        raise HTTPError(request.full_url, 304, "Not Modified", {}, None)

    monkeypatch.setattr("information_agent.collection.rss.urlopen", fake_urlopen)

    result = fetch_feed_with_cache(
        "https://example.com/rss.xml",
        timeout=5,
        etag='"feed-v1"',
        last_modified="Thu, 17 Jul 2025 09:30:00 GMT",
    )

    assert result.feed_url == "https://example.com/rss.xml"
    assert result.not_modified is True
    assert result.entries == []


def test_content_length_only_accepts_ascii_decimal_and_compares_without_int_conversion() -> None:
    assert not content_length_exceeds_limit("0005", 5)
    assert content_length_exceeds_limit("0006", 5)
    assert content_length_exceeds_limit("9" * 10_000, MAX_FEED_BYTES)

    for value in (None, "", "+6", "-6", " 6", "6 ", "0x6", "\u0666", "6.0"):
        assert not content_length_exceeds_limit(value, 5)


def test_fetch_feed_rejects_oversized_ascii_content_length_before_read(monkeypatch) -> None:
    class FakeResponse:
        headers = {"Content-Length": str(MAX_FEED_BYTES + 1)}

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def read(self, _: int) -> bytes:
            raise AssertionError("body should not be read")

    monkeypatch.setattr(
        "information_agent.collection.rss.urlopen", lambda *args, **kwargs: FakeResponse()
    )

    with pytest.raises(ValueError, match="5 MiB"):
        fetch_feed("https://example.com/rss.xml")


def test_fetch_feed_rejects_oversized_body_when_content_length_is_malformed(monkeypatch) -> None:
    class FakeResponse:
        headers = {"Content-Length": "+1"}

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == MAX_FEED_BYTES + 1
            return b"x" * limit

    monkeypatch.setattr(
        "information_agent.collection.rss.urlopen", lambda *args, **kwargs: FakeResponse()
    )

    with pytest.raises(ValueError, match="5 MiB"):
        fetch_feed("https://example.com/rss.xml")


def test_fetch_feed_async_rejects_oversized_content_length_before_read() -> None:
    class FakeContent:
        def iter_chunked(self, _: int):
            raise AssertionError("body should not be read")

    class FakeResponse:
        headers = {"Content-Length": str(MAX_FEED_BYTES + 1)}
        content = FakeContent()

        def raise_for_status(self) -> None:
            return None

    class RequestContext:
        async def __aenter__(self) -> FakeResponse:
            return FakeResponse()

        async def __aexit__(self, *args) -> None:
            return None

    class FakeSession:
        def get(self, *args, **kwargs) -> RequestContext:
            return RequestContext()

    with pytest.raises(ValueError, match="5 MiB"):
        asyncio.run(fetch_feed_async("https://example.com/rss.xml", 5, session=FakeSession()))


def test_fetch_feed_async_rejects_oversized_body_with_malformed_content_length() -> None:
    class FakeContent:
        async def iter_chunked(self, limit: int):
            assert limit == 64 * 1024
            yield b"x" * (MAX_FEED_BYTES + 1)

    class FakeResponse:
        headers = {"Content-Length": "0x1"}
        content = FakeContent()

        def raise_for_status(self) -> None:
            return None

    class RequestContext:
        async def __aenter__(self) -> FakeResponse:
            return FakeResponse()

        async def __aexit__(self, *args) -> None:
            return None

    class FakeSession:
        def get(self, *args, **kwargs) -> RequestContext:
            return RequestContext()

    with pytest.raises(ValueError, match="5 MiB"):
        asyncio.run(fetch_feed_async("https://example.com/rss.xml", 5, session=FakeSession()))
