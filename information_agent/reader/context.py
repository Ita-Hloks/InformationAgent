from __future__ import annotations

import ipaddress
import math
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)
from uuid import uuid4

from ..collection._http import content_length_exceeds_limit
from ..collection.models import RawFeedEntry
from ..collection.web import MAX_PAGE_BYTES, _extract_text, _guess_encoding
from ..common import normalize_url
from ..contracts import ContentType
from ..normalization import normalize_evidence
from ..storage import ReaderArticle
from .service import ReaderService

DEFAULT_CONTEXT_FETCH_TIMEOUT_SECONDS = 10.0
CONTEXT_TTL_SECONDS = 15 * 60
MAX_CONTEXT_REDIRECTS = 3
MIN_CONTEXT_CONTENT_CHARS = 20
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".lan", ".internal", ".home.arpa")


class ArticleContextError(ValueError):
    pass


class ArticleContextUrlError(ArticleContextError):
    pass


class ArticleContextUnavailableError(RuntimeError):
    pass


class ArticleContextNotFoundError(LookupError):
    pass


class ArticleContextNotConfirmedError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ArticleContextResult:
    context_id: str
    source_url: str
    title: str
    is_local: bool
    confirmed: bool


@dataclass(slots=True)
class _StoredArticleContext:
    context_id: str
    source_url: str
    title: str
    article: ReaderArticle
    is_local: bool
    confirmed: bool
    expires_at: float


@dataclass(frozen=True, slots=True)
class _FetchedArticle:
    title: str
    content: str


class _ArticleMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture: str | None = None
        self._title_parts: list[str] = []
        self._heading_parts: list[str] = []

    @property
    def title(self) -> str:
        title = _normalize_metadata_text(self._title_parts)
        return title or _normalize_metadata_text(self._heading_parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag == "title":
            self._capture = "title"
        elif normalized_tag == "h1" and self._capture is None:
            self._capture = "heading"

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag == "title" and self._capture == "title":
            self._capture = None
        elif normalized_tag == "h1" and self._capture == "heading":
            self._capture = None

    def handle_data(self, data: str) -> None:
        if self._capture == "title":
            self._title_parts.append(data)
        elif self._capture == "heading":
            self._heading_parts.append(data)


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object):
        return None


_SAFE_OPENER = build_opener(ProxyHandler({}), _NoRedirectHandler())


def _safe_urlopen(request: Request, *, timeout: float):
    return _SAFE_OPENER.open(request, timeout=timeout)


urlopen = _safe_urlopen


class ArticleContextService:
    def __init__(
        self,
        reader: ReaderService,
        *,
        fetcher: Callable[..., _FetchedArticle] | None = None,
        resolver: Callable[..., list[tuple[object, ...]]] = socket.getaddrinfo,
        opener: Callable[..., object] | None = None,
        fetch_timeout_seconds: float = DEFAULT_CONTEXT_FETCH_TIMEOUT_SECONDS,
        context_ttl_seconds: float = CONTEXT_TTL_SECONDS,
    ) -> None:
        _validate_positive_finite(fetch_timeout_seconds, "fetch_timeout_seconds")
        _validate_positive_finite(context_ttl_seconds, "context_ttl_seconds")
        self.reader = reader
        self.fetcher = fetcher or fetch_public_article
        self.resolver = resolver
        self.opener = opener
        self.fetch_timeout_seconds = fetch_timeout_seconds
        self.context_ttl_seconds = context_ttl_seconds
        self._contexts: dict[str, _StoredArticleContext] = {}
        self._lock = RLock()

    def resolve(self, url: str) -> ArticleContextResult:
        normalized_url = _validate_public_url(url, resolver=self.resolver)
        local_article = self.reader.get_article_by_source_url(normalized_url)
        if local_article is not None:
            return self._store_context(
                normalized_url,
                local_article.article.title,
                local_article,
                is_local=True,
            )
        try:
            fetched = self.fetcher(
                normalized_url,
                timeout=self.fetch_timeout_seconds,
                resolver=self.resolver,
                opener=self.opener,
            )
            temporary_article = _temporary_article(normalized_url, fetched)
        except ArticleContextUrlError:
            raise
        except ArticleContextUnavailableError:
            raise
        except Exception as exc:
            raise ArticleContextUnavailableError("网页抓取或解析失败，请检查 URL 后重试") from exc
        return self._store_context(
            normalized_url,
            temporary_article.article.title,
            temporary_article,
            is_local=False,
        )

    def confirm(self, context_id: str) -> ArticleContextResult:
        with self._lock:
            context = self._get_context_locked(context_id)
            context.confirmed = True
            return _context_result(context)

    def confirmed_article(self, context_id: str) -> ReaderArticle:
        with self._lock:
            context = self._get_context_locked(context_id)
            if not context.confirmed:
                raise ArticleContextNotConfirmedError("请先确认文章标题")
            return context.article

    def _store_context(
        self,
        source_url: str,
        title: str,
        article: ReaderArticle,
        *,
        is_local: bool,
    ) -> ArticleContextResult:
        now = time.monotonic()
        context_id = uuid4().hex
        context = _StoredArticleContext(
            context_id=context_id,
            source_url=source_url,
            title=title,
            article=article,
            is_local=is_local,
            confirmed=False,
            expires_at=now + self.context_ttl_seconds,
        )
        with self._lock:
            self._purge_expired_locked(now)
            self._contexts[context_id] = context
        return _context_result(context)

    def _get_context_locked(self, context_id: str) -> _StoredArticleContext:
        context = self._contexts.get(context_id)
        if context is None or context.expires_at <= time.monotonic():
            self._contexts.pop(context_id, None)
            raise ArticleContextNotFoundError("文章上下文已过期，请重新解析 URL")
        return context

    def _purge_expired_locked(self, now: float) -> None:
        expired_ids = [
            context_id
            for context_id, context in self._contexts.items()
            if context.expires_at <= now
        ]
        for context_id in expired_ids:
            del self._contexts[context_id]


def fetch_public_article(
    article_url: str,
    *,
    timeout: float = DEFAULT_CONTEXT_FETCH_TIMEOUT_SECONDS,
    resolver: Callable[..., list[tuple[object, ...]]] = socket.getaddrinfo,
    opener: Callable[..., object] | None = None,
) -> _FetchedArticle:
    _validate_positive_finite(timeout, "timeout")
    current_url = _validate_public_url(article_url, resolver=resolver)
    deadline = time.monotonic() + timeout
    open_url = opener or urlopen

    for redirect_count in range(MAX_CONTEXT_REDIRECTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ArticleContextUnavailableError("网页抓取超时，请稍后重试")
        request = Request(
            current_url,
            headers={
                "Accept": "text/html, application/xhtml+xml",
                "User-Agent": "InformationAgent/0.1 Article Context",
            },
        )
        try:
            response = open_url(request, timeout=remaining)
            status = _response_status(response)
            if status in _REDIRECT_STATUS_CODES:
                location = _response_header(response, "Location")
                _close_response(response)
                if not location:
                    raise ArticleContextUnavailableError("网页重定向缺少目标地址")
                if redirect_count >= MAX_CONTEXT_REDIRECTS:
                    raise ArticleContextUnavailableError("网页重定向次数超过限制")
                current_url = _validate_redirect_url(current_url, location, resolver=resolver)
                continue
            if status < 200 or status >= 300:
                _close_response(response)
                raise ArticleContextUnavailableError(f"网页返回错误状态：{status}")
            return _parse_html_response(response, deadline=deadline)
        except HTTPError as exc:
            if exc.code not in _REDIRECT_STATUS_CODES:
                raise ArticleContextUnavailableError("网页返回错误，无法读取文章") from exc
            location = _response_header(exc, "Location")
            _close_response(exc)
            if not location:
                raise ArticleContextUnavailableError("网页重定向缺少目标地址") from exc
            if redirect_count >= MAX_CONTEXT_REDIRECTS:
                raise ArticleContextUnavailableError("网页重定向次数超过限制") from exc
            current_url = _validate_redirect_url(current_url, location, resolver=resolver)
        except ArticleContextError:
            raise
        except (TimeoutError, URLError, OSError, ValueError) as exc:
            raise ArticleContextUnavailableError("网页抓取失败，请检查 URL 后重试") from exc

    raise ArticleContextUnavailableError("网页重定向次数超过限制")


def _parse_html_response(response: object, *, deadline: float) -> _FetchedArticle:
    headers = getattr(response, "headers", {})
    content_type = _header(headers, "Content-Type")
    media_type = content_type.split(";", 1)[0].strip().casefold() if content_type else ""
    if media_type and media_type not in {"text/html", "application/xhtml+xml"}:
        _close_response(response)
        raise ArticleContextUnavailableError("URL 返回的不是 HTML 页面")
    content_length = _header(headers, "Content-Length")
    if content_length_exceeds_limit(content_length, MAX_PAGE_BYTES):
        _close_response(response)
        raise ArticleContextUnavailableError("网页响应超过大小限制")
    try:
        payload = _read_response_body(response, deadline=deadline)
    finally:
        _close_response(response)
    if not isinstance(payload, bytes) or len(payload) > MAX_PAGE_BYTES:
        raise ArticleContextUnavailableError("网页响应超过大小限制")

    html = _decode_payload(payload, headers)
    metadata = _ArticleMetadataParser()
    try:
        metadata.feed(html)
        metadata.close()
    except Exception as exc:
        raise ArticleContextUnavailableError("网页 HTML 解析失败") from exc
    title = metadata.title
    content = _extract_text(html)
    if not title:
        raise ArticleContextUnavailableError("网页缺少可用标题")
    if content is None or len(content.strip()) < MIN_CONTEXT_CONTENT_CHARS:
        raise ArticleContextUnavailableError("网页正文为空或过短")
    return _FetchedArticle(title=title, content=content)


def _read_response_body(response: object, *, deadline: float) -> bytes:
    payload = bytearray()
    while True:
        if time.monotonic() >= deadline:
            raise ArticleContextUnavailableError("网页抓取超时，请稍后重试")
        chunk = response.read(  # type: ignore[attr-defined]
            min(64 * 1024, MAX_PAGE_BYTES + 1 - len(payload))
        )
        if not isinstance(chunk, bytes):
            raise ArticleContextUnavailableError("网页响应格式无效")
        payload.extend(chunk)
        if len(payload) > MAX_PAGE_BYTES:
            raise ArticleContextUnavailableError("网页响应超过大小限制")
        if not chunk:
            return bytes(payload)


def _temporary_article(source_url: str, fetched: _FetchedArticle) -> ReaderArticle:
    entries = normalize_evidence(
        [
            RawFeedEntry(
                source_url=source_url,
                title=fetched.title,
                content=fetched.content,
                source_type="url",
                content_type=ContentType.UNKNOWN,
            )
        ],
        min_content_chars=MIN_CONTEXT_CONTENT_CHARS,
    )
    if not entries:
        raise ArticleContextUnavailableError("网页没有可用正文")
    return ReaderArticle(feed_id="temporary", article=entries[0])


def _validate_url_shape(value: str) -> str:
    if not isinstance(value, str):
        raise ArticleContextUrlError("URL 只支持无账号信息的 http 或 https 地址")
    raw_value = value.strip()
    try:
        raw = urlsplit(raw_value)
        authority = raw.netloc.rsplit("@", 1)[-1]
        if "@" in raw.netloc:
            raise ArticleContextUrlError("URL 不允许包含账号信息")
        if authority.endswith(":"):
            raise ArticleContextUrlError("URL 端口无效")
        if raw.port == 0:
            raise ArticleContextUrlError("URL 端口无效")
    except ArticleContextUrlError:
        raise
    except ValueError as exc:
        raise ArticleContextUrlError("URL 只支持无账号信息的 http 或 https 地址") from exc

    normalized = normalize_url(raw_value)
    if normalized is None:
        raise ArticleContextUrlError("URL 只支持无账号信息的 http 或 https 地址")
    parsed = urlsplit(normalized)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if _is_blocked_hostname(hostname):
        raise ArticleContextUrlError("不允许访问 localhost、回环地址或内网地址")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return normalized
    if not _is_public_address(address):
        raise ArticleContextUrlError("不允许访问 localhost、回环地址或内网地址")
    return normalized


def _validate_public_url(
    value: str,
    *,
    resolver: Callable[..., list[tuple[object, ...]]],
) -> str:
    normalized = _validate_url_shape(value)
    hostname = (urlsplit(normalized).hostname or "").casefold().rstrip(".")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if _is_blocked_hostname(hostname):
            raise ArticleContextUrlError("不允许访问 localhost、回环地址或内网地址") from None
        try:
            resolved = resolver(hostname, urlsplit(normalized).port, type=socket.SOCK_STREAM)
        except (OSError, socket.gaierror) as exc:
            raise ArticleContextUnavailableError("URL 主机无法解析") from exc
        addresses = _resolved_addresses(resolved)
        if not addresses or any(not _is_public_address(item) for item in addresses):
            raise ArticleContextUrlError("URL 解析到了回环地址或内网地址") from None
    else:
        if not _is_public_address(address):
            raise ArticleContextUrlError("不允许访问 localhost、回环地址或内网地址")
    return normalized


def _validate_redirect_url(
    current_url: str,
    location: str,
    *,
    resolver: Callable[..., list[tuple[object, ...]]],
) -> str:
    target_url = urljoin(current_url, location)
    target = _validate_public_url(target_url, resolver=resolver)
    if urlsplit(current_url).scheme == "https" and urlsplit(target).scheme == "http":
        raise ArticleContextUrlError("不允许从 HTTPS 重定向到 HTTP")
    return target


def _resolved_addresses(
    resolved: list[tuple[object, ...]],
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for item in resolved:
        if len(item) < 5:
            continue
        try:
            addresses.append(ipaddress.ip_address(str(item[4][0])))
        except (IndexError, TypeError, ValueError):
            raise ArticleContextUrlError("URL 主机地址无效") from None
    return addresses


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_multicast
        and not address.is_unspecified
    )


def _is_blocked_hostname(hostname: str) -> bool:
    return (
        not hostname
        or hostname == "localhost"
        or hostname.endswith(_BLOCKED_HOST_SUFFIXES)
        or "." not in hostname
    )


def _decode_payload(payload: bytes, headers: object) -> str:
    guessed = _guess_encoding(type("Response", (), {"headers": headers})())
    for encoding in (guessed, "utf-8", "gbk", "gb2312", "latin-1"):
        if encoding is None:
            continue
        try:
            return payload.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    raise ArticleContextUnavailableError("网页编码无法识别")


def _normalize_metadata_text(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split()).strip()[:500]


def _response_status(response: object) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()  # type: ignore[attr-defined]
    return int(status)


def _header(headers: object, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    value = getter(name) if callable(getter) else None
    if value is None:
        items = getattr(headers, "items", None)
        if callable(items):
            value = next(
                (candidate for key, candidate in items() if str(key).casefold() == name.casefold()),
                None,
            )
    if value is None:
        return None
    return str(value).strip() or None


def _response_header(response: object, name: str) -> str | None:
    return _header(getattr(response, "headers", response), name)


def _close_response(response: object) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        close()


def _context_result(context: _StoredArticleContext) -> ArticleContextResult:
    return ArticleContextResult(
        context_id=context.context_id,
        source_url=context.source_url,
        title=context.title,
        is_local=context.is_local,
        confirmed=context.confirmed,
    )


def _validate_positive_finite(value: float, name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
