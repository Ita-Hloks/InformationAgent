from __future__ import annotations

import json

import pytest

from information_agent.collection import RawFeedEntry
from information_agent.investigation import LLMQuestionPlanner, QuestionKind, parse_search_plans
from information_agent.investigation import planner as investigation_planner
from information_agent.normalization import normalize_evidence
from information_agent.selection import SelectedEvidence


def _evidence() -> list[SelectedEvidence]:
    article = normalize_evidence(
        [
            RawFeedEntry(
                "https://example.com/article",
                "芯片发布",
                "公司称新芯片使推理成本下降 70%，并将在下月开始供货。"
                "该公司尚未公开完整的测试方法与比较基线。",
            )
        ]
    )[0]
    return [SelectedEvidence(article, evidence_id=1)]


def _valid_payload() -> str:
    return json.dumps(
        {
            "plans": [
                {
                    "evidence_id": 1,
                    "trigger_quote": "新芯片使推理成本下降 70%",
                    "question": "该成本降幅的比较基线和测试方法是什么？",
                    "kind": "quantitative_claim",
                    "priority": 1,
                    "queries": [
                        {
                            "query": "公司名 新芯片 推理成本 70% 测试方法",
                            "purpose": "查找原始测试方法和比较基线",
                        }
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )


def test_parse_search_plans_returns_traceable_plan() -> None:
    plans = parse_search_plans(_valid_payload(), _evidence())

    assert len(plans) == 1
    assert plans[0].evidence_id == 1
    assert plans[0].kind is QuestionKind.QUANTITATIVE_CLAIM
    assert plans[0].queries[0].query == "公司名 新芯片 推理成本 70% 测试方法"


def test_parse_search_plans_accepts_numeric_string_evidence_id() -> None:
    payload = json.loads(_valid_payload())
    payload["plans"][0]["evidence_id"] = "1"

    plans = parse_search_plans(json.dumps(payload, ensure_ascii=False), _evidence())

    assert plans[0].evidence_id == 1


def test_parse_search_plans_rejects_quote_not_in_article() -> None:
    payload = json.loads(_valid_payload())
    payload["plans"][0]["trigger_quote"] = "不存在于文章中的断言"

    with pytest.raises(ValueError, match="trigger_quote"):
        parse_search_plans(json.dumps(payload, ensure_ascii=False), _evidence())


def test_parse_search_plans_rejects_duplicate_queries() -> None:
    payload = json.loads(_valid_payload())
    payload["plans"][0]["queries"].append(
        {"query": "公司名 新芯片 推理成本 70% 测试方法", "purpose": "重复查询"}
    )

    with pytest.raises(ValueError, match="查询不能重复"):
        parse_search_plans(json.dumps(payload, ensure_ascii=False), _evidence())


def test_parse_search_plans_rejects_duplicate_queries_across_plans() -> None:
    payload = json.loads(_valid_payload())
    second_plan = payload["plans"][0].copy()
    second_plan["evidence_id"] = 2
    second_plan["queries"] = [
        {"query": "  公司名 新芯片 推理成本 70% 测试方法  ", "purpose": "查找另一项依据"}
    ]
    payload["plans"].append(second_plan)
    evidence = _evidence()
    evidence.append(SelectedEvidence(evidence[0].article, evidence_id=2))

    with pytest.raises(ValueError, match="查询不能重复"):
        parse_search_plans(json.dumps(payload, ensure_ascii=False), evidence)


def test_parse_search_plans_preserves_distinct_queries_across_plans() -> None:
    payload = json.loads(_valid_payload())
    second_plan = payload["plans"][0].copy()
    second_plan["evidence_id"] = 2
    second_plan["queries"] = [{"query": "新芯片 下月供货 量产交付", "purpose": "查找量产依据"}]
    payload["plans"].append(second_plan)
    evidence = _evidence()
    evidence.append(SelectedEvidence(evidence[0].article, evidence_id=2))

    plans = parse_search_plans(json.dumps(payload, ensure_ascii=False), evidence)

    assert [plan.evidence_id for plan in plans] == [1, 2]
    assert [query.query for plan in plans for query in plan.queries] == [
        "公司名 新芯片 推理成本 70% 测试方法",
        "新芯片 下月供货 量产交付",
    ]


def test_parse_search_plans_limits_one_plan_per_article() -> None:
    payload = json.loads(_valid_payload())
    payload["plans"].append(
        {
            "evidence_id": 1,
            "trigger_quote": "将在下月开始供货",
            "question": "该供货计划是否有公开的量产与交付依据？",
            "kind": "time_sensitive_claim",
            "priority": 1,
            "queries": [{"query": "新芯片 下月供货 量产交付", "purpose": "查找量产和交付依据"}],
        }
    )

    with pytest.raises(ValueError, match="每篇文章最多生成 1 个"):
        parse_search_plans(json.dumps(payload, ensure_ascii=False), _evidence())


def test_parse_search_plans_rejects_nonessential_priority() -> None:
    payload = json.loads(_valid_payload())
    payload["plans"][0]["priority"] = 2

    with pytest.raises(ValueError, match="最高优先级"):
        parse_search_plans(json.dumps(payload, ensure_ascii=False), _evidence())


def test_parse_search_plans_rejects_conclusion_fields() -> None:
    payload = json.loads(_valid_payload())
    payload["plans"][0]["confidence"] = 0.9

    with pytest.raises(ValueError, match="计划字段"):
        parse_search_plans(json.dumps(payload, ensure_ascii=False), _evidence())


def test_parse_search_plans_requires_chinese_question() -> None:
    payload = json.loads(_valid_payload())
    payload["plans"][0]["question"] = "What is the comparison baseline?"

    with pytest.raises(ValueError, match="question 必须使用中文"):
        parse_search_plans(json.dumps(payload, ensure_ascii=False), _evidence())


def test_parse_search_plans_allows_query_in_the_source_language() -> None:
    payload = json.loads(_valid_payload())
    payload["plans"][0]["queries"][0]["query"] = "AI chip independent benchmark"

    plans = parse_search_plans(json.dumps(payload, ensure_ascii=False), _evidence())

    assert plans[0].queries[0].query == "AI chip independent benchmark"


def test_parse_search_plans_allows_no_questions() -> None:
    assert parse_search_plans('{"plans": []}', _evidence()) == []


def test_llm_planner_retries_invalid_json_with_feedback(monkeypatch) -> None:
    responses = iter(["{}", _valid_payload()])
    calls: list[list[dict[str, str]]] = []

    def fake_request_json_completion(**kwargs) -> str:
        calls.append(kwargs["messages"])
        return next(responses)

    monkeypatch.setattr(
        investigation_planner,
        "request_json_completion",
        fake_request_json_completion,
    )
    planner = object.__new__(LLMQuestionPlanner)
    planner.client = object()

    result = planner.plan_with_result("AI", _evidence(), timeout=10)

    assert len(calls) == 2
    assert "模型输出必须是仅包含 plans 的 JSON 对象" in calls[1][1]["content"]
    assert result.raw_response == _valid_payload()
    assert len(result.plans) == 1
