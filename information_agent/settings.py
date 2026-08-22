from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
PROJECT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


class EnvFileOpenError(RuntimeError):
    """Raised when the fixed project environment file cannot be opened."""


@dataclass(frozen=True, slots=True)
class MainLLMConfig:
    api_key: str = field(repr=False)
    model: str
    base_url: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> MainLLMConfig:
        values = os.environ if environ is None else environ
        return cls(
            api_key=values.get("LLM_API_KEY", "").strip(),
            model=_configured_value(values.get("LLM_MODEL"), DEFAULT_LLM_MODEL),
            base_url=_configured_value(values.get("LLM_BASE_URL"), DEFAULT_LLM_BASE_URL),
        )

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def available(self) -> bool:
        return self.api_key_configured and bool(self.model) and _valid_base_url(self.base_url)

    def to_public_status(self) -> dict[str, bool | str]:
        return {
            "api_key_configured": self.api_key_configured,
            "model": _redact(self.model, self.api_key),
            "base_url": _public_base_url(self.base_url, self.api_key),
            "available": self.available,
        }


def open_project_env_file() -> None:
    path = PROJECT_ENV_PATH
    if not path.is_file():
        raise EnvFileOpenError("项目 .env 文件不存在")

    try:
        startfile = getattr(os, "startfile", None)
        if callable(startfile):
            startfile(str(path))
            return

        command = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen(
            [command, str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError) as exc:
        raise EnvFileOpenError("无法打开项目 .env 文件") from exc


def _configured_value(value: str | None, default: str) -> str:
    normalized = value.strip() if value is not None else ""
    return normalized or default


def _valid_base_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and bool(hostname)
        and (port is None or port > 0)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _public_base_url(value: str, secret: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            port = parsed.port
            netloc = parsed.hostname if port is None else f"{parsed.hostname}:{port}"
            value = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except ValueError:
        pass
    return _redact(value, secret)


def _redact(value: str, secret: str) -> str:
    return value.replace(secret, "[已隐藏]") if secret else value
