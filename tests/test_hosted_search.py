from __future__ import annotations

import json
from types import SimpleNamespace

from information_agent.investigation import QuestionKind, SearchPlan, SearchQuery
from information_agent.search import HostedSearchAnswerer, HostedSearchConfig, SearchAnswerStatus
from information_agent.search.hosted import (
    MAX_SOURCE_PUBLISHED_AT_CHARS,
    MAX_SOURCE_REFERENCE_CHARS,
    MAX_SOURCE_SITE_NAME_CHARS,
    MAX_SOURCE_SNIPPET_CHARS,
    MAX_SOURCE_TITLE_CHARS,
    _parse_sources,
)


class FakeResponse:
    def __init__(
        self,
        *,
        answer: str,
        web_search: list[dict[str, str]] | None = None,
        reasoning_content: str | None = None,
    ) -> None:
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(
                    content=answer,
                    reasoning_content=reasoning_content,
                )
            )
        ]
        self.web_search = web_search or []

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        message = {"content": self.choices[0].message.content}
        if self.choices[0].message.reasoning_content is not None:
            message["reasoning_content"] = self.choices[0].message.reasoning_content
        return {
            "choices": [{"message": message}],
            "web_search": self.web_search,
        }


class FakeClient:
    def __init__(self, response: FakeResponse | list[FakeResponse]) -> None:
        self.responses = response if isinstance(response, list) else [response]
        self.requests: list[dict[str, object]] = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs: object) -> FakeResponse:
        self.requests.append(kwargs)
        return self.responses.pop(0)


def _plan() -> SearchPlan:
    return SearchPlan(
        evidence_id=2,
        trigger_quote="监督网约车服务的日常运营",
        question="该岗位是否包含监督网约车服务日常运营的职责？",
        kind=QuestionKind.ATTRIBUTION_CLAIM,
        priority=1,
        queries=(
            SearchQuery(
                "AI Safety Operator Bogota job description",
                "查找招聘页面",
            ),
        ),
    )


def _config() -> HostedSearchConfig:
    return HostedSearchConfig(
        api_key="secret",
        model="search-model",
        base_url="https://api.example.com/v1",
        timeout_seconds=30,
    )


def test_hosted_search_answerer_returns_answer_with_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(tmp_path))
    response = FakeResponse(
        answer="是，该岗位描述包含这项职责。",
        web_search=[
            {
                "title": "AI Safety Operator",
                "link": "https://example.com/careers/search/job/123?utm_source=search",
                "media": "招聘网站",
                "publish_date": "2026-07-20",
                "content": "岗位负责监督网约车服务的日常运营。",
                "refer": "[1]",
            }
        ],
    )
    client = FakeClient(response)

    result = HostedSearchAnswerer(_config(), client).answer(_plan(), timeout=20)

    assert result.status is SearchAnswerStatus.ANSWERED
    assert result.answer == "是，该岗位描述包含这项职责。"
    assert result.sources[0].url == "https://example.com/careers/search/job/123"
    assert result.sources[0].site_name == "招聘网站"
    request = client.requests[0]
    assert request["model"] == "search-model"
    assert request["timeout"] == 20
    assert request["response_format"] == {"type": "json_object"}
    assert request["tools"] == [
        {
            "type": "web_search",
            "web_search": {
                "enable": True,
                "search_result": True,
                "count": 5,
                "content_size": "medium",
            },
        }
    ]
    backup = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert backup["status"] == "completed"
    assert backup["response"]["web_search"][0]["title"] == "AI Safety Operator"
    assert backup["result"]["status"] == "answered"


def test_hosted_search_answerer_requires_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(tmp_path))
    client = FakeClient(FakeResponse(answer="模型声称找到了答案。"))

    result = HostedSearchAnswerer(_config(), client).answer(_plan(), timeout=20)

    assert result.status is SearchAnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer == "未能获得带有可验证来源的搜索结果。"
    assert result.sources == ()


def test_hosted_search_answerer_accepts_json_content_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(tmp_path))
    client = FakeClient(
        FakeResponse(
            answer=json.dumps(
                {
                    "answer": "Python 官方文档首页是 https://docs.python.org/3/。",
                    "sources": [
                        {
                            "title": "Python Documentation",
                            "url": "https://docs.python.org/3/",
                            "snippet": "Official Python documentation.",
                            "site_name": "Python Documentation",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )
    )

    result = HostedSearchAnswerer(_config(), client).answer(_plan(), timeout=20)

    assert result.status is SearchAnswerStatus.ANSWERED
    assert result.answer == "Python 官方文档首页是 https://docs.python.org/3/。"
    assert result.sources[0].url == "https://docs.python.org/3/"
    assert result.sources[0].site_name == "Python Documentation"


def test_hosted_search_answerer_synthesizes_search_trace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(tmp_path))
    first_response = FakeResponse(
        answer="</chain>\n<search>\n<query>Python official documentation</query>\n</search>",
        web_search=[
            {
                "title": "Python Documentation",
                "link": "https://docs.python.org/3/",
                "content": "The official Python documentation.",
            }
        ],
    )
    second_response = FakeResponse(
        answer="Python 官方文档首页是 https://docs.python.org/3/。",
    )
    client = FakeClient([first_response, second_response])

    result = HostedSearchAnswerer(_config(), client).answer(_plan(), timeout=20)

    assert result.status is SearchAnswerStatus.ANSWERED
    assert result.answer == "Python 官方文档首页是 https://docs.python.org/3/。"
    assert result.sources[0].url == "https://docs.python.org/3/"
    assert len(client.requests) == 2
    assert "tools" in client.requests[0]
    assert "tools" not in client.requests[1]
    assert client.requests[1]["response_format"] == {"type": "json_object"}
    assert "https://docs.python.org/3/" in client.requests[1]["messages"][1]["content"]
    backups = [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("*.json")]
    assert {item["stage"] for item in backups} == {
        "hosted-search-answer",
        "hosted-search-synthesis",
    }


def test_hosted_search_answerer_rejects_search_trace_after_synthesis(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(tmp_path))
    trace = "<search><query>Python official documentation</query></search>"
    source = {
        "title": "Python Documentation",
        "link": "https://docs.python.org/3/",
        "content": "The official Python documentation.",
    }
    client = FakeClient(
        [
            FakeResponse(answer=trace, web_search=[source]),
            FakeResponse(answer=trace),
            FakeResponse(answer=trace),
        ]
    )

    result = HostedSearchAnswerer(_config(), client).answer(_plan(), timeout=20)

    assert result.status is SearchAnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer == "未能获得带有可验证来源的搜索结果。"
    assert result.sources[0].url == "https://docs.python.org/3/"
    assert len(client.requests) == 3


def test_hosted_search_answerer_ignores_separate_reasoning_content(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(tmp_path))
    client = FakeClient(
        FakeResponse(
            answer="Python 官方文档首页是 https://docs.python.org/3/。",
            reasoning_content="<chain>这段内容不能成为答案。</chain>",
            web_search=[
                {
                    "title": "Python Documentation",
                    "link": "https://docs.python.org/3/",
                    "content": "The official Python documentation.",
                }
            ],
        )
    )

    result = HostedSearchAnswerer(_config(), client).answer(_plan(), timeout=20)

    assert result.status is SearchAnswerStatus.ANSWERED
    assert result.answer == "Python 官方文档首页是 https://docs.python.org/3/。"
    assert len(client.requests) == 1


def test_hosted_search_answerer_preserves_explicit_insufficient_answer(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(tmp_path))
    client = FakeClient(
        FakeResponse(
            answer="未找到足够可靠的公开证据。",
            web_search=[
                {
                    "title": "低相关来源",
                    "link": "https://example.com/weak",
                    "content": "没有直接回答问题。",
                }
            ],
        )
    )

    result = HostedSearchAnswerer(_config(), client).answer(_plan(), timeout=20)

    assert result.status is SearchAnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer == "未能获得带有可验证来源的搜索结果。"
    assert len(client.requests) == 1


def test_hosted_search_answerer_creates_a_client_when_not_injected(monkeypatch) -> None:
    client = FakeClient(FakeResponse(answer=""))
    observed_configs: list[HostedSearchConfig] = []

    def fake_factory(config: HostedSearchConfig) -> FakeClient:
        observed_configs.append(config)
        return client

    monkeypatch.setattr("information_agent.search.hosted.create_search_client", fake_factory)

    answerer = HostedSearchAnswerer(_config())

    assert answerer.client is client
    assert observed_configs == [_config()]


def test_parse_sources_normalizes_and_bounds_untrusted_text() -> None:
    sources = _parse_sources(
        [
            {
                "title": "  source\n title  " + "x" * MAX_SOURCE_TITLE_CHARS,
                "link": "https://example.com/article",
                "media": "  source\t site  " + "x" * MAX_SOURCE_SITE_NAME_CHARS,
                "publish_date": "  2026\n 08  " + "x" * MAX_SOURCE_PUBLISHED_AT_CHARS,
                "content": "  source\n snippet  " + "x" * MAX_SOURCE_SNIPPET_CHARS,
                "refer": "  source\t reference  " + "x" * MAX_SOURCE_REFERENCE_CHARS,
            },
            {
                "title": "Second source",
                "link": "https://example.com/second",
                "media": " \n\t ",
                "publish_date": "",
                "content": None,
                "refer": "  ",
            },
        ]
    )

    first, second = sources
    assert first.title == ("source title " + "x" * MAX_SOURCE_TITLE_CHARS)[:MAX_SOURCE_TITLE_CHARS]
    assert (
        first.site_name
        == ("source site " + "x" * MAX_SOURCE_SITE_NAME_CHARS)[:MAX_SOURCE_SITE_NAME_CHARS]
    )
    assert (
        first.published_at
        == ("2026 08 " + "x" * MAX_SOURCE_PUBLISHED_AT_CHARS)[:MAX_SOURCE_PUBLISHED_AT_CHARS]
    )
    assert (
        first.snippet
        == ("source snippet " + "x" * MAX_SOURCE_SNIPPET_CHARS)[:MAX_SOURCE_SNIPPET_CHARS]
    )
    assert (
        first.reference
        == ("source reference " + "x" * MAX_SOURCE_REFERENCE_CHARS)[:MAX_SOURCE_REFERENCE_CHARS]
    )
    assert second.site_name is None
    assert second.published_at is None
    assert second.snippet is None
    assert second.reference is None


def test_parse_sources_retains_first_source_and_uses_untruncated_url_title_fallback() -> None:
    url = "https://example.com/" + "a" * (MAX_SOURCE_TITLE_CHARS + 1)

    sources = _parse_sources(
        [
            {"title": "   ", "link": url},
            {"title": "Duplicate", "link": url},
            {"title": "Second", "link": "https://example.com/second"},
        ]
    )

    assert [source.url for source in sources] == [url, "https://example.com/second"]
    assert sources[0].title == url
    assert sources[1].title == "Second"
