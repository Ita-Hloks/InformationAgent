from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from ..common import content_blocks_to_text, llm_safe_text, normalize_url, parse_content_blocks
from ..contracts import PROJECT_TIMEZONE
from ..investigation import OpinionPlan
from ..normalization import NormalizedArticle
from ..search import SearchSource
from ..storage import SQLiteCollectionStore, default_database_path
from .bilibili import BilibiliTargetError, parse_bilibili_target
from .llm import LLMOpinionAnalyzer
from .service import OpinionArticleNotFoundError, OpinionSnapshotMismatchError

MAX_REFERENCE_PLANS = 1
MAX_BILIBILI_RESULTS = 5
MAX_VIDEO_TITLE_CHARS = 500
MAX_VIDEO_SNIPPET_CHARS = 4_000
MAX_VIDEO_AUTHOR_CHARS = 200
MAX_VIDEO_TAG_CHARS = 500


class ReferenceDiscoveryStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class BilibiliVideoCandidate:
    video_id: str
    bvid: str | None
    url: str
    title: str
    search_query: str
    snippet: str | None = None
    site_name: str | None = None
    published_at: str | None = None
    reference: str | None = None
    author: str | None = None
    tag: str | None = None


@dataclass(frozen=True, slots=True)
class ReferenceDiscoveryResult:
    article_id: str
    snapshot_id: str
    content_hash: str
    plans: tuple[OpinionPlan, ...]
    candidates: tuple[BilibiliVideoCandidate, ...]
    status: ReferenceDiscoveryStatus
    status_reason: str
    errors: tuple[str, ...] = field(default_factory=tuple)


class ControversyPlanner(Protocol):
    def detect_controversies(
        self,
        article: NormalizedArticle,
        timeout: float,
    ) -> list[OpinionPlan]: ...


class VideoSearcher(Protocol):
    def search(self, query: str, timeout: float) -> object: ...


SearchByType = Callable[..., Awaitable[object]]


class BilibiliVideoSearcher:
    """Synchronously wait for bilibili-api-python's video search coroutine."""

    def __init__(self, search_by_type: SearchByType | None = None) -> None:
        self._search_by_type = search_by_type

    def search(self, query: str, timeout: float) -> object:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("视频搜索时限必须大于 0")
        return asyncio.run(self._search_async(query, timeout))

    async def _search_async(self, query: str, timeout: float) -> object:
        search_by_type = self._search_by_type
        if search_by_type is None:
            from bilibili_api import search as bilibili_search
            from bilibili_api.search import SearchObjectType

            search_by_type = bilibili_search.search_by_type
        else:
            from bilibili_api.search import SearchObjectType

        return await asyncio.wait_for(
            search_by_type(
                query,
                search_type=SearchObjectType.VIDEO,
                page=1,
                page_size=MAX_BILIBILI_RESULTS,
            ),
            timeout=timeout,
        )


class ReferenceDiscoveryService:
    """Generate Bilibili queries and discover video sources without reading comments."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        store: SQLiteCollectionStore | None = None,
        planner: ControversyPlanner | None = None,
        video_searcher: VideoSearcher | None = None,
        timeout_seconds: float = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive finite number")
        self.store = store or SQLiteCollectionStore(database_path or default_database_path())
        self.planner = planner
        self.video_searcher = video_searcher
        self.timeout_seconds = timeout_seconds
        self.clock = clock

    def discover(self, article_id: str) -> ReferenceDiscoveryResult:
        article = self.store.get_reader_article(article_id)
        if article is None:
            raise OpinionArticleNotFoundError(f"不存在的文章：{article_id}")
        if article.snapshot_id is None or article.content_hash is None:
            raise OpinionSnapshotMismatchError("文章缺少可复用的快照身份")

        snapshot_id = article.snapshot_id
        content_hash = article.content_hash
        deadline = self.clock() + self.timeout_seconds
        try:
            planner = self.planner or LLMOpinionAnalyzer()
            plans = tuple(planner.detect_controversies(article.article, self._remaining(deadline)))
            _validate_plans(article.article, plans)
        except Exception as exc:
            return ReferenceDiscoveryResult(
                article_id=article_id,
                snapshot_id=snapshot_id,
                content_hash=content_hash,
                plans=(),
                candidates=(),
                status=ReferenceDiscoveryStatus.PARTIAL,
                status_reason="planning_failed",
                errors=(f"关键词生成失败：{exc}",),
            )

        if not plans:
            return ReferenceDiscoveryResult(
                article_id=article_id,
                snapshot_id=snapshot_id,
                content_hash=content_hash,
                plans=(),
                candidates=(),
                status=ReferenceDiscoveryStatus.COMPLETED,
                status_reason="no_queries",
            )

        try:
            video_searcher = self.video_searcher or BilibiliVideoSearcher()
        except Exception as exc:
            return ReferenceDiscoveryResult(
                article_id=article_id,
                snapshot_id=snapshot_id,
                content_hash=content_hash,
                plans=plans,
                candidates=(),
                status=ReferenceDiscoveryStatus.PARTIAL,
                status_reason="search_failed",
                errors=(f"视频搜索初始化失败：{exc}",),
            )

        candidates: list[BilibiliVideoCandidate] = []
        seen_video_ids: set[str] = set()
        seen_urls: set[str] = set()
        errors: list[str] = []
        for plan in plans:
            for query in plan.queries:
                remaining = self._remaining(deadline)
                if remaining <= 0:
                    errors.append("视频搜索在任务时限内未完成")
                    break
                try:
                    payload = video_searcher.search(query.query, remaining)
                    query_candidates = _candidates_from_bilibili_response(
                        payload,
                        query.query,
                    )
                except Exception as exc:
                    errors.append(f"视频搜索失败：{exc}")
                    continue
                for candidate in query_candidates:
                    if candidate.video_id in seen_video_ids or candidate.url in seen_urls:
                        continue
                    seen_video_ids.add(candidate.video_id)
                    seen_urls.add(candidate.url)
                    candidates.append(candidate)

        if errors:
            status = ReferenceDiscoveryStatus.PARTIAL
            status_reason = "partial_search" if candidates else "search_failed"
        else:
            status = ReferenceDiscoveryStatus.COMPLETED
            status_reason = "completed" if candidates else "no_candidates"
        return ReferenceDiscoveryResult(
            article_id=article_id,
            snapshot_id=snapshot_id,
            content_hash=content_hash,
            plans=plans,
            candidates=tuple(candidates),
            status=status,
            status_reason=status_reason,
            errors=tuple(errors),
        )

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self.clock()
        return remaining if remaining > 0 else 0.0


def _validate_plans(article: NormalizedArticle, plans: tuple[OpinionPlan, ...]) -> None:
    if len(plans) > MAX_REFERENCE_PLANS:
        raise ValueError("每篇文章最多生成一个舆情查询计划")
    article_content = llm_safe_text(article.content)
    for plan in plans:
        if plan.evidence_id != 1:
            raise ValueError("舆情查询计划必须引用当前文章")
        if plan.trigger_quote not in article_content:
            raise ValueError("舆情查询计划的原文锚点不在当前文章正文中")


def _candidates_from_bilibili_response(
    payload: object,
    search_query: str,
) -> tuple[BilibiliVideoCandidate, ...]:
    if not isinstance(payload, dict):
        raise ValueError("哔哩哔哩搜索响应必须是对象")
    raw_results = payload.get("result")
    if not isinstance(raw_results, list):
        raise ValueError("哔哩哔哩搜索响应缺少 result 数组")

    candidates: list[BilibiliVideoCandidate] = []
    for item in raw_results[:MAX_BILIBILI_RESULTS]:
        candidate = _candidate_from_bilibili_item(item, search_query)
        if candidate is not None:
            candidates.append(candidate)
    return tuple(candidates)


def _candidate_from_bilibili_item(
    item: object,
    search_query: str,
) -> BilibiliVideoCandidate | None:
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    if item_type not in (None, "video"):
        return None

    title = _result_text(item.get("title"), MAX_VIDEO_TITLE_CHARS, html=True)
    url = item.get("arcurl")
    if title is None or not isinstance(url, str) or not url.strip():
        return None

    raw_bvid = item.get("bvid")
    bvid = _parse_result_bvid(raw_bvid) if raw_bvid is not None else None
    if raw_bvid is not None and bvid is None:
        return None

    author = _result_text(item.get("author"), MAX_VIDEO_AUTHOR_CHARS)
    source = SearchSource(
        title=title,
        url=url,
        site_name=author,
        published_at=_published_at(item.get("pubdate")),
        snippet=_result_text(item.get("description"), MAX_VIDEO_SNIPPET_CHARS),
    )
    return _candidate_from_source(
        source,
        search_query,
        bvid=bvid,
        author=author,
        tag=_result_text(item.get("tag"), MAX_VIDEO_TAG_CHARS),
    )


def _result_text(value: object, maximum_length: int, *, html: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    text = content_blocks_to_text(parse_content_blocks(value)) if html else value
    normalized = " ".join(text.split())
    return normalized[:maximum_length] or None


def _parse_result_bvid(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        target = parse_bilibili_target(f"https://www.bilibili.com/video/{value.strip()}")
    except BilibiliTargetError:
        return None
    return target.bvid


def _published_at(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return None
        try:
            return datetime.fromtimestamp(value, tz=PROJECT_TIMEZONE).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None
    return _result_text(value, 100)


def _candidate_from_source(
    source: SearchSource,
    search_query: str,
    *,
    bvid: str | None = None,
    author: str | None = None,
    tag: str | None = None,
) -> BilibiliVideoCandidate | None:
    normalized_url = normalize_url(source.url)
    if normalized_url is None:
        return None
    try:
        target = parse_bilibili_target(normalized_url)
    except BilibiliTargetError:
        return None
    if target.comment_type != 1:
        return None

    if target.bvid is not None:
        if bvid is not None and bvid != target.bvid:
            return None
        video_id = target.bvid
        bvid = target.bvid
    elif target.oid is not None:
        video_id = f"av{target.oid}"
        if bvid is not None:
            video_id = bvid
    else:
        return None
    return BilibiliVideoCandidate(
        video_id=video_id,
        bvid=bvid,
        url=normalized_url,
        title=source.title,
        search_query=search_query,
        snippet=source.snippet,
        site_name=source.site_name,
        published_at=source.published_at,
        reference=source.reference,
        author=author,
        tag=tag,
    )
