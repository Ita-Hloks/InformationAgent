from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from information_agent.collection import RawFeedEntry
from information_agent.normalization import normalize_evidence
from information_agent.selection import (
    LLMRelevanceSelector,
    RelevanceResponseError,
    SelectedEvidence,
    select_evidence,
)


def normalized(source_url: str, title: str, content: str):
    return normalize_evidence([RawFeedEntry(source_url, title, content)], min_content_chars=1)[0]


class FakeCompletionClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.messages: list[dict[str, str]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def with_options(self, **kwargs):
        return self

    def create(self, **kwargs):
        self.messages = kwargs["messages"]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.payload)))]
        )


def test_llm_selector_keeps_relevant_segments_and_splits_a_digest() -> None:
    items = [
        normalized(
            "https://example.com/strong",
            "新型 AI 芯片降低推理成本",
            "厂商公布了新型 AI 芯片的推理测试结果和比较基线。",
        ),
        normalized(
            "https://example.com/weak",
            "城市交通管理更新",
            "文章末尾顺带提到人工智能，但全文讨论道路拥堵和公交调度。",
        ),
        normalized(
            "https://example.com/digest",
            "今日科技新闻汇总",
            (
                "第一篇：AI 芯片发布。芯片测试结果显示延迟下降。\n\n"
                "第二篇：手机更新。手机厂商公布了新的产品计划。"
            ),
        ),
    ]
    client = FakeCompletionClient(
        {
            "decisions": [
                {
                    "candidate_id": "candidate-1",
                    "segments": [
                        {
                            "title": "新型 AI 芯片降低推理成本",
                            "start_quote": "厂商公布了",
                            "end_quote": "比较基线。",
                        }
                    ],
                },
                {
                    "candidate_id": "candidate-2",
                    "segments": [],
                },
                {
                    "candidate_id": "candidate-3",
                    "segments": [
                        {
                            "title": "AI 芯片发布",
                            "start_quote": "第一篇：AI 芯片发布。",
                            "end_quote": "延迟下降。",
                        },
                    ],
                },
            ]
        }
    )

    selected = select_evidence(
        "AI 芯片",
        items,
        selector=LLMRelevanceSelector(client=client),
        timeout=10,
    )

    assert [item.source_url for item in selected] == [
        "https://example.com/strong",
        "https://example.com/digest",
    ]
    assert selected[1].title == "AI 芯片发布"
    assert selected[1].content == "第一篇：AI 芯片发布。芯片测试结果显示延迟下降。"
    assert "手机" not in selected[1].content
    user_prompt = client.messages[1]["content"]
    assert "第一篇：AI 芯片发布" in user_prompt


def test_selection_deduplicates_before_calling_llm() -> None:
    items = [
        normalized(
            "https://example.com/article",
            "主题文章",
            "第一版主题文章内容已经达到最小长度要求。",
        ),
        normalized(
            "https://example.com/article",
            "主题文章",
            "第二版主题文章内容已经达到最小长度要求。",
        ),
    ]
    client = FakeCompletionClient(
        {
            "decisions": [
                {
                    "candidate_id": "candidate-1",
                    "segments": [
                        {
                            "title": "主题文章",
                            "start_quote": "第一版",
                            "end_quote": "最小长度要求。",
                        }
                    ],
                }
            ]
        }
    )

    selected = select_evidence(
        "主题",
        items,
        selector=LLMRelevanceSelector(client=client),
        timeout=10,
    )

    assert len(selected) == 1
    assert selected[0].source_url == "https://example.com/article"


def test_malformed_llm_output_is_rejected() -> None:
    from information_agent.selection.llm import parse_relevance_response

    with pytest.raises(RelevanceResponseError, match="文章片段字段不符合约定"):
        parse_relevance_response(
            json.dumps(
                {
                    "decisions": [
                        {
                            "candidate_id": "candidate-1",
                            "segments": [
                                {
                                    "title": "主题文章",
                                    "start_quote": "主题",
                                }
                            ],
                        }
                    ]
                }
            ),
            batch_start=0,
            batch_size=1,
        )


def test_default_selector_requires_llm_configuration(monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        LLMRelevanceSelector()


def test_selector_output_cannot_invent_an_article() -> None:
    item = normalized("https://example.com/article", "主题文章", "主题正文。")

    class InventingSelector:
        def select(self, topic, items, *, limit, timeout):
            return [
                SelectedEvidence(
                    article=normalized("https://example.com/other", "其他", "其他正文。"),
                    evidence_id=9,
                )
            ]

    with pytest.raises(ValueError, match="未知文章"):
        select_evidence("主题", [item], selector=InventingSelector(), timeout=10)


def test_selector_rejects_model_text_outside_original_entry() -> None:
    item = normalized("https://example.com/article", "主题文章", "主题正文。")

    class InventingSegmentSelector:
        def select(self, topic, items, *, limit, timeout):
            return [
                SelectedEvidence(
                    article=normalized(
                        "https://example.com/article",
                        "主题文章",
                        "模型补写的正文。",
                    ),
                    evidence_id=1,
                )
            ]

    with pytest.raises(ValueError, match="输入文章之外"):
        select_evidence("主题", [item], selector=InventingSegmentSelector(), timeout=10)
