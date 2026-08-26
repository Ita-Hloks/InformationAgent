"""外部信息采集。"""

from .models import FeedFetchResult, RawFeedEntry
from .rss import fetch_feed, fetch_feed_async, fetch_feed_with_cache
from .web import augment_evidence, fetch_article

__all__ = [
    "fetch_feed",
    "fetch_feed_async",
    "fetch_feed_with_cache",
    "FeedFetchResult",
    "RawFeedEntry",
    "fetch_article",
    "augment_evidence",
]
