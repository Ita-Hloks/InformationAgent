import json

import pytest

from information_agent.analysis.llm import parse_analysis


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
