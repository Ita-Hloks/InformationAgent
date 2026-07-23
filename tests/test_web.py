from urllib.error import URLError

from information_agent.collection.web import _extract_text, augment_evidence, fetch_article
from information_agent.contracts import ContentType, Evidence


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
            return b""

    def fake_urlopen(request, timeout: float) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr("information_agent.collection.web.urlopen", fake_urlopen)

    assert fetch_article("https://example.com/huge") is None


def test_augment_evidence_skips_non_summary_items() -> None:
    items = [
        Evidence(
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


def test_augment_evidence_fetches_for_summary_items(monkeypatch) -> None:
    def fake_fetch(url: str, **kwargs) -> str | None:
        return "这是从网页抓取到的完整正文内容。"

    monkeypatch.setattr("information_agent.collection.web.fetch_article", fake_fetch)

    items = [
        Evidence(
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


def test_augment_evidence_preserves_other_fields(monkeypatch) -> None:
    def fake_fetch(url: str, **kwargs) -> str | None:
        return "补上的正文内容。"

    monkeypatch.setattr("information_agent.collection.web.fetch_article", fake_fetch)

    items = [
        Evidence(
            source_url="https://example.com/article",
            title="原始标题",
            content="摘要",
            feed_url="https://example.com/rss",
            site_url="https://example.com",
            author="作者",
            categories=["科技"],
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
    assert item.categories == ["科技"]
    assert item.language == "zh-cn"
    assert item.content_type is ContentType.RSS_CONTENT


def test_augment_evidence_falls_back_when_fetch_fails(monkeypatch) -> None:
    def fake_fetch(url: str, **kwargs) -> str | None:
        return None

    monkeypatch.setattr("information_agent.collection.web.fetch_article", fake_fetch)

    items = [
        Evidence(
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
        Evidence("https://example.com/has-content", "A", "已有正文", content_type=ContentType.RSS_CONTENT),
        Evidence("https://example.com/success", "B", "摘要", content_type=ContentType.RSS_SUMMARY),
        Evidence("https://example.com/fail", "C", "摘要", content_type=ContentType.RSS_SUMMARY),
    ]
    result = augment_evidence(items)
    assert len(result) == 3
    assert result[0].content == "已有正文"
    assert result[1].content == "来自 https://example.com/success 的正文。"
    assert result[1].content_type is ContentType.RSS_CONTENT
    assert result[2].content == "摘要"
    assert result[2].content_type is ContentType.RSS_SUMMARY
    assert fetched_urls == ["https://example.com/success", "https://example.com/fail"]
