import json

import pytest

from information_agent.analysis.llm import _analysis_input, parse_analysis
from information_agent.collection import RawFeedEntry
from information_agent.normalization import normalize_evidence
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


def test_parse_analysis_rejects_wrong_shape() -> None:
    with pytest.raises(ValueError, match="claims"):
        parse_analysis('{"claims": {}, "uncertainties": []}')


def test_analysis_input_includes_each_content_batch_once() -> None:
    content = "甲" * 500 + "乙" * 500 + "丙" * 500
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
