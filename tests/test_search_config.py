from __future__ import annotations

import pytest

from information_agent.search import HostedSearchAnswerer, HostedSearchConfig


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
    assert config.timeout_seconds == 300
    assert config.adapter == "openai_responses_web_search"


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


@pytest.mark.parametrize("timeout_seconds", ["nan", "inf", "-inf"])
def test_hosted_search_config_rejects_non_finite_timeouts(
    timeout_seconds: str,
) -> None:
    with pytest.raises(ValueError, match="SEARCH_LLM_TIMEOUT_SECONDS"):
        HostedSearchConfig.from_env(
            {
                "SEARCH_LLM_API_KEY": "secret",
                "SEARCH_LLM_MODEL": "search-model",
                "SEARCH_LLM_BASE_URL": "https://api.example.com/v1",
                "SEARCH_LLM_TIMEOUT_SECONDS": timeout_seconds,
            }
        )


@pytest.mark.parametrize("timeout_seconds", [1, 0.5])
def test_hosted_search_config_accepts_positive_finite_timeouts(
    timeout_seconds: int | float,
) -> None:
    config = HostedSearchConfig.from_env(
        {
            "SEARCH_LLM_API_KEY": "secret",
            "SEARCH_LLM_MODEL": "search-model",
            "SEARCH_LLM_BASE_URL": "https://api.example.com/v1",
            "SEARCH_LLM_TIMEOUT_SECONDS": str(timeout_seconds),
        }
    )

    assert config.timeout_seconds == timeout_seconds


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


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:password@api.example.com/v1",
        "https://%75ser:%70assword@api.example.com/v1",
        "https://api.example.com/v1?query=value",
        "https://api.example.com/v1?",
        "https://api.example.com/v1#fragment",
        "https://api.example.com/v1#",
    ],
)
def test_hosted_search_config_rejects_unsafe_service_roots_before_client_creation(
    base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[HostedSearchConfig] = []

    def failing_factory(config: HostedSearchConfig) -> object:
        calls.append(config)
        raise AssertionError("client factory was called")

    monkeypatch.setattr("information_agent.search.hosted.create_search_client", failing_factory)

    with pytest.raises(ValueError, match="SEARCH_LLM_BASE_URL"):
        HostedSearchAnswerer(HostedSearchConfig("secret", "search-model", base_url))

    assert calls == []

    config = HostedSearchConfig("secret", "search-model", "https://api.example.com/v1")
    with pytest.raises(AssertionError, match="client factory was called"):
        HostedSearchAnswerer(config)
    assert calls == [config]


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.example.com/v1",
        "https://api.example.com/v1/%E6%90%9C%E7%B4%A2",
        "https://api.example.com/v1/",
    ],
)
def test_hosted_search_config_accepts_safe_service_roots(base_url: str) -> None:
    config = HostedSearchConfig("secret", "search-model", base_url)

    assert config.base_url == base_url
