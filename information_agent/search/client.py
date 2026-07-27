from __future__ import annotations

from typing import Any

from openai import OpenAI

from .config import HostedSearchConfig


def create_search_client(config: HostedSearchConfig) -> Any:
    """Create the OpenAI-compatible client used by the hosted search adapter."""
    return OpenAI(api_key=config.api_key, base_url=config.base_url)
