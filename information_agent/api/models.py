from __future__ import annotations

from pydantic import BaseModel, Field

from ..storage import FeedSubscription, ReaderArticle


class FeedCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=120)


class FeedResponse(BaseModel):
    id: str
    url: str
    title: str
    site_url: str | None
    subscribed_at: str
    last_refreshed_at: str | None
    last_error: str | None
    article_count: int


class ArticleResponse(BaseModel):
    id: str
    feed_id: str
    source_url: str
    feed_url: str | None
    site_url: str | None
    title: str
    author: str | None
    categories: list[str]
    language: str | None
    content_type: str
    published_at: str | None
    collected_at: str
    content: str


def feed_response(subscription: FeedSubscription) -> FeedResponse:
    return FeedResponse(
        id=subscription.feed_id,
        url=subscription.feed_url,
        title=subscription.title,
        site_url=subscription.site_url,
        subscribed_at=subscription.subscribed_at,
        last_refreshed_at=subscription.last_refreshed_at,
        last_error=subscription.last_error,
        article_count=subscription.article_count,
    )


def article_response(reader_article: ReaderArticle) -> ArticleResponse:
    article = reader_article.article
    return ArticleResponse(
        id=article.article_id,
        feed_id=reader_article.feed_id,
        source_url=article.source_url,
        feed_url=article.feed_url,
        site_url=article.site_url,
        title=article.title,
        author=article.author,
        categories=list(article.categories),
        language=article.language,
        content_type=article.content_type.value,
        published_at=article.published_at.isoformat() if article.published_at else None,
        collected_at=article.collected_at.isoformat(),
        content=article.content,
    )
