from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from ..common import DEFAULT_LLM_TIMEOUT_SECONDS

DEFAULT_RESULT_COUNT = 5
MAX_RESULT_COUNT = 50
DEFAULT_CONTENT_SIZE = "medium"
DEFAULT_TIMEOUT_SECONDS = DEFAULT_LLM_TIMEOUT_SECONDS

SUPPORTED_CONTENT_SIZES = {"low", "medium", "high"}


@dataclass(frozen=True, slots=True)
class HostedSearchConfig:
    api_key: str
    model: str
    base_url: str
    result_count: int = DEFAULT_RESULT_COUNT
    content_size: str = DEFAULT_CONTENT_SIZE
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise RuntimeError("缺少环境变量 SEARCH_LLM_API_KEY")
        if not self.model.strip():
            raise ValueError("SEARCH_LLM_MODEL 不能为空")
        parsed_base_url = urlparse(self.base_url)
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
            raise ValueError("SEARCH_LLM_BASE_URL 必须是有效的 HTTP(S) 地址")
        if parsed_base_url.username is not None or parsed_base_url.password is not None:
            raise ValueError("SEARCH_LLM_BASE_URL must not contain userinfo")
        if "?" in self.base_url or "#" in self.base_url:
            raise ValueError("SEARCH_LLM_BASE_URL must not contain a query or fragment")
        if parsed_base_url.path.rstrip("/").endswith("/chat/completions"):
            raise ValueError("SEARCH_LLM_BASE_URL 应填写服务根地址，不应包含 chat/completions")
        if not 1 <= self.result_count <= MAX_RESULT_COUNT:
            raise ValueError("SEARCH_LLM_RESULT_COUNT 必须在 1 到 50 之间")
        if self.content_size not in SUPPORTED_CONTENT_SIZES:
            raise ValueError("SEARCH_LLM_CONTENT_SIZE 必须是 low、medium 或 high")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("SEARCH_LLM_TIMEOUT_SECONDS 必须大于 0")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> HostedSearchConfig:
        values = os.environ if environ is None else environ
        return cls(
            api_key=values.get("SEARCH_LLM_API_KEY", ""),
            model=values.get("SEARCH_LLM_MODEL", ""),
            base_url=values.get("SEARCH_LLM_BASE_URL", "").strip(),
            result_count=_parse_int(
                values.get("SEARCH_LLM_RESULT_COUNT"),
                "SEARCH_LLM_RESULT_COUNT",
                DEFAULT_RESULT_COUNT,
            ),
            content_size=values.get("SEARCH_LLM_CONTENT_SIZE", DEFAULT_CONTENT_SIZE),
            timeout_seconds=_parse_float(
                values.get("SEARCH_LLM_TIMEOUT_SECONDS"),
                "SEARCH_LLM_TIMEOUT_SECONDS",
                DEFAULT_TIMEOUT_SECONDS,
            ),
        )


def _parse_int(value: str | None, name: str, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc


def _parse_float(value: str | None, name: str, default: float) -> float:
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字") from exc
