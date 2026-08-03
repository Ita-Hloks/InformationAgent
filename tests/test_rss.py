from urllib.error import HTTPError

from information_agent.collection.rss import _plain_text, fetch_feed, fetch_feed_with_cache
from information_agent.contracts import ContentType
from information_agent.normalization import normalize_evidence


def test_plain_text_removes_html_and_decodes_entities() -> None:
    assert _plain_text("<p>Agent &amp; RSS</p>") == "Agent & RSS"


def test_plain_text_preserves_article_block_boundaries() -> None:
    assert _plain_text("<h2>第一篇</h2><p>正文一</p><p>正文二</p>") == ("第一篇\n正文一\n正文二")


def test_plain_text_ignores_embedded_scripts_and_styles() -> None:
    assert (
        _plain_text("<p>正文</p><script>fake summary</script><style>fake style</style>") == "正文"
    )


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
