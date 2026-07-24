from information_agent.collection import RawFeedEntry
from information_agent.collection.normalization import normalize_evidence
from information_agent.processing.relevance import filter_evidence


def normalized(source_url: str, title: str, content: str):
    return normalize_evidence([RawFeedEntry(source_url, title, content)], min_content_chars=1)[0]


def test_title_match_has_more_weight_than_content_match() -> None:
    items = [
        normalized("https://example.com/content", "普通更新", "正文提到了 AI 的最新进展"),
        normalized("https://example.com/title", "AI 更新", "正文没有主题词"),
    ]

    selected = filter_evidence("AI", items, limit=1)

    assert selected[0].source_url == "https://example.com/title"
    assert selected[0].relevance_score == 0.6667


def test_filter_deduplicates_normalized_urls() -> None:
    items = [
        normalized("https://example.com/article", "AI 更新", "第一份内容"),
        normalized("https://example.com/article", "AI 更新", "第二份内容"),
    ]

    assert len(filter_evidence("AI", items)) == 1
