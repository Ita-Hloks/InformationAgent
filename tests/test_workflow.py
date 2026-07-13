from information_agent.contracts import Analysis, Claim, Evidence, RunStatus
from information_agent.orchestration import run


class FakeAnalyst:
    def analyze(self, topic: str, evidence: list[Evidence], timeout: float) -> Analysis:
        assert topic == "AI 芯片"
        assert timeout > 0
        return Analysis(
            summary="一条相关更新。",
            claims=[Claim("AI 芯片用于推理", [evidence[0].id])],
        )


class FailingAnalyst:
    def analyze(self, topic: str, evidence: list[Evidence], timeout: float) -> Analysis:
        raise RuntimeError("模型不可用")


def test_run_filters_deduplicates_and_evaluates() -> None:
    def collector(_: str, timeout: float) -> list[Evidence]:
        assert timeout > 0
        return [
            Evidence("https://example.com/1", "AI 芯片发布", "新芯片用于推理"),
            Evidence("https://example.com/1", "重复文章", "AI 芯片"),
            Evidence("https://example.com/2", "天气", "今天晴天"),
        ]

    report = run(
        "AI 芯片",
        ["feed-a"],
        collector=collector,
        analyst=FakeAnalyst(),
    )

    assert report.status is RunStatus.COMPLETED
    assert len(report.evidence) == 1
    assert report.evidence[0].id == 1
    assert report.evaluation.citation_validity == 1.0
    assert report.errors == []


def test_run_keeps_evidence_when_one_feed_and_model_fail() -> None:
    def collector(feed: str, _: float) -> list[Evidence]:
        if feed == "broken":
            raise RuntimeError("连接失败")
        return [Evidence("https://example.com/ok", "RSS 技术", "RSS 订阅")]

    report = run(
        "RSS",
        ["working", "broken"],
        collector=collector,
        analyst=FailingAnalyst(),
    )

    assert report.status is RunStatus.PARTIAL
    assert len(report.evidence) == 1
    assert report.analysis.claims == []
    assert any("连接失败" in error for error in report.errors)
    assert any("模型不可用" in error for error in report.errors)


def test_run_skips_analyst_without_matching_evidence() -> None:
    def collector(_: str, __: float) -> list[Evidence]:
        return [Evidence("https://example.com/weather", "天气", "今天晴天")]

    report = run("AI", ["feed"], collector=collector, analyst=FailingAnalyst())

    assert report.status is RunStatus.PARTIAL
    assert report.analysis.summary == "没有找到与主题匹配的 RSS 内容。"
    assert report.errors == []
