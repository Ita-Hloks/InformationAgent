from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from ..collection import FeedFetchResult, fetch_feed_with_cache
from ..common import normalize_url
from ..normalization import normalize_evidence
from ..storage import (
    FeedSubscription,
    ReaderArticle,
    ReaderArticleState,
    SQLiteCollectionStore,
    default_database_path,
)

FeedFetcher = Callable[..., FeedFetchResult]


class FeedNotFoundError(LookupError):
    pass


class FeedUnavailableError(RuntimeError):
    pass


class ArticleNotFoundError(LookupError):
    pass


class ReaderService:
    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        feed_timeout_seconds: float = 15,
        fetcher: FeedFetcher = fetch_feed_with_cache,
    ) -> None:
        if not math.isfinite(feed_timeout_seconds) or feed_timeout_seconds <= 0:
            raise ValueError("feed_timeout_seconds must be a finite positive number")
        self.store = SQLiteCollectionStore(database_path or default_database_path())
        self.feed_timeout_seconds = feed_timeout_seconds
        self.fetcher = fetcher

    def subscribe(self, feed_url: str, *, title: str | None = None) -> FeedSubscription:
        normalized_url = normalize_url(feed_url)
        if normalized_url is None:
            raise ValueError("RSS 地址必须是无账号信息的 http 或 https URL")
        result = self._fetch(normalized_url)
        display_title = _display_title(normalized_url, title)
        site_url = next((entry.site_url for entry in result.entries if entry.site_url), None)
        articles = normalize_evidence(result.entries)
        return self.store.save_subscription(
            feed_url=normalized_url,
            title=display_title,
            site_url=site_url,
            result_etag=result.etag,
            result_last_modified=result.last_modified,
            entries=result.entries,
            articles=articles,
        )

    def refresh(self, feed_id: str) -> FeedSubscription:
        subscription = self.store.get_subscription(feed_id)
        if subscription is None:
            raise FeedNotFoundError(f"不存在的订阅：{feed_id}")
        state = self.store.feed_state(subscription.feed_url)
        try:
            result = self._fetch(
                subscription.feed_url,
                etag=state.etag,
                last_modified=state.last_modified,
            )
        except FeedUnavailableError as exc:
            self.store.record_subscription_error(feed_id, exc)
            raise
        articles = normalize_evidence(result.entries)
        return self.store.save_subscription(
            feed_url=subscription.feed_url,
            title=subscription.title,
            site_url=subscription.site_url,
            result_etag=result.etag,
            result_last_modified=result.last_modified,
            entries=result.entries,
            articles=articles,
        )

    def list_subscriptions(self) -> list[FeedSubscription]:
        return self.store.list_subscriptions()

    def list_articles(
        self,
        *,
        feed_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ReaderArticle]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if feed_id is not None and self.store.get_subscription(feed_id) is None:
            raise FeedNotFoundError(f"不存在的订阅：{feed_id}")
        return self.store.list_reader_articles(feed_id=feed_id, limit=limit, offset=offset)

    def get_article(self, article_id: str) -> ReaderArticle:
        article = self.store.get_reader_article(article_id)
        if article is None:
            raise ArticleNotFoundError(f"不存在的文章：{article_id}")
        return article

    def get_article_by_source_url(self, source_url: str) -> ReaderArticle | None:
        return self.store.get_reader_article_by_source_url(source_url)

    def update_article_states(
        self,
        article_ids: list[str],
        *,
        is_read: bool | None = None,
        is_saved: bool | None = None,
    ) -> list[ReaderArticleState]:
        try:
            return self.store.update_reader_article_states(
                article_ids,
                is_read=is_read,
                is_saved=is_saved,
            )
        except KeyError as exc:
            raise ArticleNotFoundError(f"不存在的文章：{exc.args[0]}") from exc

    def _fetch(
        self,
        feed_url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FeedFetchResult:
        try:
            return self.fetcher(
                feed_url,
                self.feed_timeout_seconds,
                etag=etag,
                last_modified=last_modified,
            )
        except Exception as exc:
            raise FeedUnavailableError(f"RSS 获取或解析失败：{exc}") from exc


def _display_title(feed_url: str, title: str | None) -> str:
    normalized_title = " ".join((title or "").split())
    if normalized_title:
        return normalized_title[:120]
    hostname = urlsplit(feed_url).hostname
    if hostname is None:
        raise ValueError("RSS 地址缺少主机名")
    return hostname.removeprefix("www.")
