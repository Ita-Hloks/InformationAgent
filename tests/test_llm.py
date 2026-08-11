import json

import pytest

from information_agent.analysis.llm import _analysis_input, parse_analysis
from information_agent.collection import RawFeedEntry
from information_agent.common.text import split_content
from information_agent.normalization import llm_safe_text, normalize_evidence
from information_agent.selection import SelectedEvidence


def test_parse_analysis_validates_and_clamps_values() -> None:
    result = parse_analysis(
        json.dumps(
            {
                "claims": [
                    {"text": "第一条结论。", "evidence_ids": ["1", "bad"]},
                    {"text": "第二条结论", "evidence_ids": [2]},
                ],
                "uncertainties": ["只有摘要"],
            },
            ensure_ascii=False,
        )
    )
    assert result.summary == "第一条结论；第二条结论。"
    assert result.claims[0].evidence_ids == [1]


def test_parse_analysis_omits_coercible_invalid_citation_values() -> None:
    result = parse_analysis(
        json.dumps(
            {
                "claims": [
                    {
                        "text": "保留的结论",
                        "evidence_ids": [
                            7,
                            "08",
                            True,
                            1.0,
                            float("nan"),
                            float("inf"),
                            [],
                            {},
                            None,
                            "bad",
                        ],
                    },
                    {
                        "text": "没有有效引用的结论",
                        "evidence_ids": [
                            False,
                            2.5,
                            float("-inf"),
                            [],
                            {},
                            None,
                            "invalid",
                        ],
                    },
                ],
                "uncertainties": ["保留的不确定性"],
            },
            ensure_ascii=False,
        )
    )

    assert result.summary == "保留的结论。"
    assert result.claims[0].evidence_ids == [7, 8]
    assert len(result.claims) == 1
    assert result.uncertainties == ["保留的不确定性"]


def test_parse_analysis_rejects_wrong_shape() -> None:
    with pytest.raises(ValueError, match="claims"):
        parse_analysis('{"claims": {}, "uncertainties": []}')


def test_llm_safe_text_removes_code_fences_and_inline_code() -> None:
    assert llm_safe_text("事实\n```python\nprint('secret')\n```\n结论 `x < 1`") == "事实\n结论"


@pytest.mark.parametrize("content", ["", "content"])
@pytest.mark.parametrize("batch_chars", [0, -1])
def test_split_content_rejects_non_positive_batch_chars(content: str, batch_chars: int) -> None:
    with pytest.raises(ValueError, match="batch_chars must be positive"):
        split_content(content, batch_chars)


def test_split_content_preserves_content_order_and_natural_boundaries() -> None:
    content = "first.\nsecond.\nthird"

    chunks = split_content(content, batch_chars=10)

    assert chunks == ["first.\n", "second.\n", "third"]
    assert "".join(chunks) == content


def test_parse_analysis_omits_non_string_text_and_uncertainties() -> None:
    result = parse_analysis(
        json.dumps(
            {
                "claims": [
                    {"text": "  retained claim  ", "evidence_ids": [7]},
                    {"text": ["not", "a", "claim"], "evidence_ids": [8]},
                    {"text": {"not": "a claim"}, "evidence_ids": [9]},
                    {"text": 10, "evidence_ids": [10]},
                    {"text": "   ", "evidence_ids": [11]},
                ],
                "uncertainties": [
                    "  first uncertainty  ",
                    ["not", "an uncertainty"],
                    {"not": "an uncertainty"},
                    12,
                    "   ",
                    "second uncertainty",
                ],
            }
        )
    )

    assert result.summary == "retained claim。"
    assert [(claim.text, claim.evidence_ids) for claim in result.claims] == [
        ("retained claim", [7])
    ]
    assert result.uncertainties == ["first uncertainty", "second uncertainty"]


def test_analysis_input_includes_each_content_batch_once() -> None:
    content = "甲" * 2_000 + "乙" * 2_000 + "丙" * 2_000
    article = normalize_evidence(
        [RawFeedEntry("https://example.com/article", "分批文章", content)]
    )[0]

    prompt = _analysis_input([SelectedEvidence(article, evidence_id=1)])

    assert prompt.count('<evidence id="1"') == 3
    assert 'batch="1/3"' in prompt
    assert 'batch="2/3"' in prompt
    assert 'batch="3/3"' in prompt
    assert article.content_chunks[0] in prompt
    assert article.content_chunks[1] in prompt
    assert article.content_chunks[2] in prompt
