from __future__ import annotations

import math
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from ..common import normalize_url

_IMAGE_META_KEYS = {
    "og:image",
    "og:image:url",
    "twitter:image",
    "twitter:image:src",
}
_IMAGE_PROBE_BYTES = 32


class _ImageURLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.casefold(): value for name, value in attrs if value is not None}
        normalized_tag = tag.casefold()
        if normalized_tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").casefold()
            if key in _IMAGE_META_KEYS:
                self._add(attributes.get("content"))
            return
        if normalized_tag == "link":
            relations = set((attributes.get("rel") or "").casefold().split())
            if "image_src" in relations:
                self._add(attributes.get("href"))
            return
        if normalized_tag == "img":
            for name in ("src", "data-src", "data-original", "data-lazy-src"):
                self._add(attributes.get(name))
            self._add(_first_srcset_url(attributes.get("srcset")))

    def _add(self, value: str | None) -> None:
        if value:
            candidate = value.strip()
            if candidate:
                self.candidates.append(candidate)


def extract_image_url_from_html(html: str, base_url: str) -> str | None:
    parser = _ImageURLParser()
    parser.feed(html)
    parser.close()
    for candidate in parser.candidates:
        image_url = resolve_image_url(candidate, base_url)
        if image_url is not None:
            return image_url
    return None


def resolve_image_url(value: str, base_url: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    return normalize_url(urljoin(base_url, candidate))


def image_url_is_accessible(image_url: str | None, timeout: float = 15) -> bool:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")

    normalized_url = normalize_url(image_url or "")
    if normalized_url is None:
        return False

    request = Request(
        normalized_url,
        headers={
            "User-Agent": "InformationAgent/0.1 Image-Probe",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is not None and not 200 <= status < 300:
                return False
            content_type = response.headers.get("Content-Type", "")
            if content_type.split(";", 1)[0].strip().casefold().startswith("image/"):
                return True
            return _looks_like_image(response.read(_IMAGE_PROBE_BYTES))
    except (HTTPError, URLError, OSError, ValueError):
        return False


def _looks_like_image(payload: bytes) -> bool:
    return (
        payload.startswith(
            (
                b"\xff\xd8\xff",  # JPEG
                b"\x89PNG\r\n\x1a\n",  # PNG
                b"GIF87a",  # GIF
                b"GIF89a",  # GIF
                b"BM",  # BMP
            )
        )
        or (len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP")
    )


def _first_srcset_url(value: str | None) -> str | None:
    if not value:
        return None
    for candidate in value.split(","):
        url = candidate.strip().split(maxsplit=1)
        if url:
            return url[0]
    return None
