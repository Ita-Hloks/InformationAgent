from __future__ import annotations

import json
from types import SimpleNamespace

from information_agent.investigation import QuestionKind, SearchPlan, SearchQuery
from information_agent.search import HostedSearchAnswerer, HostedSearchConfig, SearchAnswerStatus


class FakeResponse:
    def __init__(self, *, answer: str, web_search: list[dict[str, str]]) -> None:
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=answer))]
        self.web_search = web_search

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {
            "choices": [{"message": {"content": self.choices[0].message.content}}],
            "web_search": self.web_search,
        }


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[dict[str, object]] = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs: object) -> FakeResponse:
        self.requests.append(kwargs)
        return self.response


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
    return HostedSearchConfig(api_key="secret", model="search-model", timeout_seconds=30)


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
    client = FakeClient(FakeResponse(answer="模型声称找到了答案。", web_search=[]))

    result = HostedSearchAnswerer(_config(), client).answer(_plan(), timeout=20)

    assert result.status is SearchAnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer == "未能获得带有可验证来源的搜索结果。"
    assert result.sources == ()
