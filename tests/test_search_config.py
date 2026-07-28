from __future__ import annotations

import pytest

from information_agent.search import HostedSearchConfig


def test_hosted_search_config_reads_defaults() -> None:
    config = HostedSearchConfig.from_env(
        {
            "SEARCH_LLM_API_KEY": "secret",
            "SEARCH_LLM_MODEL": "search-model",
            "SEARCH_LLM_BASE_URL": "https://api.example.com/v1",
        }
    )

    assert config.model == "search-model"
    assert config.base_url == "https://api.example.com/v1"
    assert config.result_count == 5
    assert config.content_size == "medium"
    assert config.timeout_seconds == 60


def test_hosted_search_config_requires_api_key() -> None:
    with pytest.raises(RuntimeError, match="SEARCH_LLM_API_KEY"):
        HostedSearchConfig.from_env(
            {
                "SEARCH_LLM_MODEL": "search-model",
                "SEARCH_LLM_BASE_URL": "https://api.example.com/v1",
            }
        )


def test_hosted_search_config_requires_model() -> None:
    with pytest.raises(ValueError, match="SEARCH_LLM_MODEL"):
        HostedSearchConfig.from_env(
            {
                "SEARCH_LLM_API_KEY": "secret",
                "SEARCH_LLM_BASE_URL": "https://api.example.com/v1",
            }
        )


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("SEARCH_LLM_RESULT_COUNT", "0", "1 到 50"),
        ("SEARCH_LLM_RESULT_COUNT", "not-a-number", "必须是整数"),
        ("SEARCH_LLM_CONTENT_SIZE", "large", "low、medium 或 high"),
        ("SEARCH_LLM_TIMEOUT_SECONDS", "0", "必须大于 0"),
    ],
)
def test_hosted_search_config_rejects_invalid_values(
    name: str,
    value: str,
    message: str,
) -> None:
    environ = {
        "SEARCH_LLM_API_KEY": "secret",
        "SEARCH_LLM_MODEL": "search-model",
        "SEARCH_LLM_BASE_URL": "https://api.example.com/v1",
        name: value,
    }

    with pytest.raises((RuntimeError, ValueError), match=message):
        HostedSearchConfig.from_env(environ)


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "api.example.com",
        "ftp://api.example.com",
        "https://api.example.com/v1/chat/completions",
    ],
)
def test_hosted_search_config_requires_http_base_url(base_url: str) -> None:
    with pytest.raises(ValueError, match="SEARCH_LLM_BASE_URL"):
        HostedSearchConfig.from_env(
            {
                "SEARCH_LLM_API_KEY": "secret",
                "SEARCH_LLM_MODEL": "search-model",
                "SEARCH_LLM_BASE_URL": base_url,
            }
        )
