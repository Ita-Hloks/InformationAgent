from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

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
                title="测试文章",
                content="用于测试文章助手持久化的正文内容。" * 10,
                feed_url=feed_url,
                content_type=ContentType.RSS_CONTENT,
            )
        ],
    )


def test_article_answers_are_bound_to_snapshot_and_support_pagination_and_cleanup(
    tmp_path: Path,
) -> None:
    service = ReaderService(tmp_path / "answers.db", fetcher=_fetcher)
    service.subscribe("https://example.com/rss.xml")
    article = service.list_articles()[0]
    store = service.store

    for request_id, question in (("q-1", "第一个问题"), ("q-2", "第二个问题")):
        claim = store.claim_article_answer(article, request_id=request_id, question=question)
        assert claim.owner is True
        store.complete_article_answer(request_id, f"{question}的回答")

    second_article = replace(
        article.article,
        content="更新后的正文内容，用于验证不同快照不会共享问答历史。",
        collected_at=datetime(2026, 8, 23, tzinfo=PROJECT_TIMEZONE),
    )
    with store._connect() as connection:
        store._upsert_snapshot(connection, second_article)
    current = store.get_reader_article(article.article.article_id)
    assert current is not None
    assert current.snapshot_id != article.snapshot_id

    claim = store.claim_article_answer(current, request_id="q-3", question="更新后的问题")
    assert claim.owner is True
    store.complete_article_answer("q-3", "更新快照的回答")

    first_page, has_more = store.list_article_answers(
        article.article.article_id,
        article.snapshot_id,
        limit=1,
    )
    assert has_more is True
    assert [item.request_id for item in first_page] == ["q-2"]
    current_page, current_has_more = store.list_article_answers(
        article.article.article_id,
        current.snapshot_id,
    )
    assert current_has_more is False
    assert [item.request_id for item in current_page] == ["q-3"]

    assert (
        store.clear_article_answers(
            article.article.article_id,
            snapshot_id=current.snapshot_id,
        )
        == 1
    )
    assert store.clear_article_answers(article.article.article_id) == 2
