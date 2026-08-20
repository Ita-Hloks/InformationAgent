from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from ..contracts import PROJECT_TIMEZONE, project_now
from .models import BilibiliComment
from .runtime import (
    Clock,
    OpinionRetryExhaustedError,
    OpinionTimeoutError,
    remaining_time,
    retry_delay,
    run_attempt,
    validate_deadline,
)

MAX_COMMENT_LIMIT = 200
PAGE_SIZE = 20
MAX_COMMENT_PAGES = MAX_COMMENT_LIMIT // PAGE_SIZE
DEFAULT_COMMENT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_ATTEMPTS = 2
_VIDEO_PATH = re.compile(r"^/video/(BV[0-9A-Za-z]+|av(\d+))/?$")
_ARTICLE_PATH = re.compile(r"^/read/cv(\d+)/?$")
_BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}

JsonFetcher = Callable[[str, float, str], object]


class BilibiliSourceError(RuntimeError):
    code = "bilibili_response_invalid"


class BilibiliTargetError(BilibiliSourceError):
    code = "unsupported_target"


class BilibiliRetryExhaustedError(BilibiliSourceError):
    code = "retry_exhausted"

    def __init__(self, stage: str, cause: BaseException) -> None:
        self.stage = stage
        self.cause = cause
        super().__init__(f"{stage} 阶段重试耗尽：{cause}")


@dataclass(frozen=True, slots=True)
class BilibiliTarget:
    source_url: str
    comment_type: int
    bvid: str | None = None
    oid: int | None = None


def parse_bilibili_target(source_url: str) -> BilibiliTarget:
    if not isinstance(source_url, str) or not source_url.strip():
        raise BilibiliTargetError("文章来源必须是哔哩哔哩 URL")
    normalized_url = source_url.strip()
    try:
        parsed = urlsplit(normalized_url)
        hostname = (parsed.hostname or "").casefold()
    except ValueError as exc:
        raise BilibiliTargetError("文章来源 URL 无效") from exc
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise BilibiliTargetError("首版只支持 http 或 https 的哔哩哔哩 URL")
    if hostname not in _BILIBILI_HOSTS:
        raise BilibiliTargetError("首版只支持直接关联的哔哩哔哩内容")

    video_match = _VIDEO_PATH.fullmatch(parsed.path)
    if video_match:
        identifier = video_match.group(1)
        if identifier.casefold().startswith("av"):
            return BilibiliTarget(normalized_url, 1, oid=int(video_match.group(2)))
        return BilibiliTarget(normalized_url, 1, bvid=identifier)

    article_match = _ARTICLE_PATH.fullmatch(parsed.path)
    if article_match:
        return BilibiliTarget(normalized_url, 12, oid=int(article_match.group(1)))

    raise BilibiliTargetError("首版只支持哔哩哔哩视频或专栏 URL")


class BilibiliCommentCollector:
    """从哔哩哔哩公开评论接口读取一篇内容的时间窗内评论。"""

    def __init__(
        self,
        *,
        request_json: JsonFetcher | None = None,
        cookie: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        clock: Clock = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cookie = cookie.strip() if isinstance(cookie, str) and cookie.strip() else None
        self.request_json = request_json or self._request_json
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self.max_attempts = max_attempts
        self.clock = clock
        self.sleep = sleep
        self.last_attempts = []
        self.last_comments = []

    def collect(
        self,
        source_url: str,
        *,
        window_hours: int = 72,
        limit: int = 100,
        timeout: float = DEFAULT_COMMENT_TIMEOUT_SECONDS,
        deadline: float | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> list[BilibiliComment]:
        if window_hours <= 0:
            raise ValueError("window_hours must be positive")
        if not 1 <= limit <= MAX_COMMENT_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_COMMENT_LIMIT}")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")

        target = parse_bilibili_target(source_url)
        run_deadline = validate_deadline(timeout, clock=self.clock, deadline=deadline)
        self.last_attempts = []
        self.last_comments = []
        oid = target.oid
        if oid is None:
            if target.bvid is None:
                raise BilibiliSourceError("哔哩哔哩内容缺少可解析的标识")
            oid = self._resolve_video_aid(target.bvid, source_url, run_deadline, heartbeat)

        since = project_now() - timedelta(hours=window_hours)
        comments: list[BilibiliComment] = []
        seen_ids: set[str] = set()
        for page in range(1, min(MAX_COMMENT_PAGES, math.ceil(limit / PAGE_SIZE)) + 1):
            query = urlencode(
                {"type": target.comment_type, "oid": oid, "sort": 2, "pn": page, "ps": PAGE_SIZE}
            )
            data = self._request_data(
                f"https://api.bilibili.com/x/v2/reply?{query}",
                source_url,
                stage="comment_collection",
                deadline=run_deadline,
                parser=_parse_comment_data,
                heartbeat=heartbeat,
            )
            replies = data.get("replies")
            if replies is None:
                break

            page_comments: list[BilibiliComment] = []
            for raw in replies:
                comment = parse_bilibili_comment(raw, source_url)
                if comment is None or comment.comment_id in seen_ids:
                    continue
                seen_ids.add(comment.comment_id)
                if comment.published_at is not None and comment.published_at >= since:
                    page_comments.append(comment)

            comments.extend(page_comments)
            self.last_comments = list(comments)
            comments.sort(
                key=lambda item: (
                    item.published_at
                    if isinstance(item.published_at, datetime)
                    else datetime.min.replace(tzinfo=PROJECT_TIMEZONE)
                ),
                reverse=True,
            )
            if len(comments) >= limit:
                return comments[:limit]
            if not replies or len(replies) < PAGE_SIZE:
                break
            parsed_times = [
                item.published_at
                for item in page_comments
                if isinstance(item.published_at, datetime)
            ]
            if parsed_times and min(parsed_times) < since:
                break
        self.last_comments = list(comments[:limit])
        return self.last_comments

    def _request_json(self, url: str, timeout: float, referer: str) -> object:
        return _request_json(url, timeout, referer, cookie=self.cookie)

    def _resolve_video_aid(
        self,
        bvid: str,
        source_url: str,
        deadline: float,
        heartbeat: Callable[[], None] | None,
    ) -> int:
        query = urlencode({"bvid": bvid})
        data = self._request_data(
            f"https://api.bilibili.com/x/web-interface/view?{query}",
            source_url,
            stage="aid_resolution",
            deadline=deadline,
            parser=require_bilibili_api_data,
            heartbeat=heartbeat,
        )
        aid = data.get("aid")
        if type(aid) is not int or aid <= 0:
            raise BilibiliSourceError("哔哩哔哩视频信息缺少 aid")
        return aid

    def _request_data(
        self,
        url: str,
        referer: str,
        *,
        stage: str,
        deadline: float,
        parser: Callable[[object], object],
        heartbeat: Callable[[], None] | None,
    ) -> dict[str, object]:
        last_error: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            if heartbeat is not None:
                heartbeat()
            try:
                parsed = run_attempt(
                    stage=stage,
                    attempt=attempt,
                    deadline=deadline,
                    clock=self.clock,
                    operation=lambda timeout: parser(self.request_json(url, timeout, referer)),
                    attempts=self.last_attempts,
                    secrets=(self.cookie,) if self.cookie else (),
                )
                if not isinstance(parsed, dict):
                    raise BilibiliSourceError("哔哩哔哩接口 data 必须是对象")
                return parsed
            except OpinionTimeoutError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt == self.max_attempts:
                    raise BilibiliRetryExhaustedError(stage, exc) from exc
                remaining = remaining_time(deadline, clock=self.clock)
                if remaining <= 0:
                    raise OpinionTimeoutError(stage) from exc
                self.sleep(retry_delay(attempt, remaining))
        raise OpinionRetryExhaustedError(stage, last_error or RuntimeError("未知错误"))


def _request_json(url: str, timeout: float, referer: str, *, cookie: str | None = None) -> object:
    headers = {
        "Accept": "application/json",
        "Referer": referer,
        "User-Agent": "InformationAgent/0.1 OpinionCollector",
    }
    if cookie is not None:
        headers["Cookie"] = cookie
    request = Request(
        url,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
    except (HTTPError, URLError, OSError) as exc:
        raise BilibiliSourceError(f"哔哩哔哩接口请求失败：{exc}") from exc
    if len(payload) > 2 * 1024 * 1024:
        raise BilibiliSourceError("哔哩哔哩接口响应过大")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BilibiliSourceError("哔哩哔哩接口没有返回合法 JSON") from exc


def require_bilibili_api_data(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or type(payload.get("code")) is not int:
        raise BilibiliSourceError("哔哩哔哩接口返回结构无效")
    if payload["code"] != 0:
        message = payload.get("message") or payload.get("msg") or "未知错误"
        raise BilibiliSourceError(f"哔哩哔哩接口拒绝请求：{message}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise BilibiliSourceError("哔哩哔哩接口缺少 data")
    return data


def _parse_comment_data(payload: object) -> dict[str, object]:
    data = require_bilibili_api_data(payload)
    replies = data.get("replies")
    if replies is not None and not isinstance(replies, list):
        raise BilibiliSourceError("哔哩哔哩评论接口返回了无效的 replies")
    return data


def parse_bilibili_comment(raw: object, source_url: str) -> BilibiliComment | None:
    if not isinstance(raw, dict):
        return None
    comment_id = raw.get("rpid")
    content_payload = raw.get("content")
    member_payload = raw.get("member")
    ctime = raw.get("ctime")
    if (
        comment_id is None
        or not isinstance(content_payload, dict)
        or not isinstance(member_payload, dict)
    ):
        return None
    message = content_payload.get("message")
    author = member_payload.get("uname")
    if not isinstance(message, str) or not message.strip():
        return None
    if not isinstance(author, str) or not author.strip():
        author = "匿名用户"
    if type(ctime) is not int or ctime <= 0:
        return None
    try:
        published_at = datetime.fromtimestamp(ctime, tz=PROJECT_TIMEZONE)
    except (OverflowError, OSError, ValueError):
        return None
    likes = raw.get("like", 0)
    if type(likes) is not int or likes < 0:
        likes = 0
    comment_id_text = str(comment_id).strip()
    if not comment_id_text:
        return None
    return BilibiliComment(
        comment_id=comment_id_text,
        source_url=f"{source_url.split('#', 1)[0]}#reply{comment_id_text}",
        author=author.strip()[:120],
        content=message.strip()[:2_000],
        likes=likes,
        published_at=published_at,
    )
