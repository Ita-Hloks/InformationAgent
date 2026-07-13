from information_agent.analysis.evaluation import evaluate_analysis
from information_agent.contracts import Analysis, Claim, Evidence


def test_evaluation_detects_invalid_and_unsupported_citations() -> None:
    evidence = Evidence("https://example.com/1", "AI 芯片", "用于模型推理", id=1)
    analysis = Analysis(
        summary="测试",
        claims=[
            Claim("AI 芯片用于推理", [1]),
            Claim("火星发现海洋", [999]),
        ],
    )
    result = evaluate_analysis(analysis, [evidence])
    assert result.citation_coverage == 1.0
    assert result.citation_validity == 0.5
    assert result.lexical_support == 0.5
    assert len(result.issues) == 2


def test_empty_analysis_is_not_reported_as_supported() -> None:
    result = evaluate_analysis(Analysis("无结论", []), [])
    assert result.citation_coverage == 0.0
    assert result.issues == ["没有可评估的分析结论"]
