from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

from ..common import normalize_url

_IMAGE_META_KEYS = {
    "og:image",
    "og:image:url",
    "twitter:image",
    "twitter:image:src",
}


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


def _first_srcset_url(value: str | None) -> str | None:
    if not value:
        return None
    for candidate in value.split(","):
        url = candidate.strip().split(maxsplit=1)
        if url:
            return url[0]
    return None
