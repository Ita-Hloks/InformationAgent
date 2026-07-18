import json
import sys

from information_agent.cli import main
from information_agent.contracts import CollectionReport, Evidence, RunStatus
from information_agent.orchestration.collection import collect
from information_agent.serialization import collection_report_to_payload


def test_collect_succeeds_without_llm_configuration(monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    def collector(_: str, __: float) -> list[Evidence]:
        return [
            Evidence(
                "https://example.com/article",
                "AI 芯片发布",
                "这是一篇长度超过二十个字符并与 AI 芯片主题相关的文章正文。",
            )
        ]

    report = collect("AI 芯片", ["feed"], collector=collector)
    payload = collection_report_to_payload(report)

    assert report.status is RunStatus.COMPLETED
    assert len(report.articles) == 1
    assert set(payload) == {"topic", "status", "articles", "errors"}


def test_collect_reports_partial_and_failed_sources() -> None:
    def partly_working_collector(feed: str, _: float) -> list[Evidence]:
        if feed == "broken":
            raise RuntimeError("连接失败")
        return []

    partial = collect("AI", ["working", "broken"], collector=partly_working_collector)
    failed = collect("AI", ["broken"], collector=partly_working_collector)

    assert partial.status is RunStatus.PARTIAL
    assert partial.articles == []
    assert failed.status is RunStatus.FAILED
    assert failed.articles == []


def test_collect_with_no_matches_is_completed() -> None:
    def collector(_: str, __: float) -> list[Evidence]:
        return [
            Evidence(
                "https://example.com/weather",
                "天气预报",
                "这是一篇长度超过二十个字符但与研究主题完全无关的天气文章。",
            )
        ]

    report = collect("AI", ["feed"], collector=collector)

    assert report.status is RunStatus.COMPLETED
    assert report.articles == []


def test_collect_cli_does_not_load_llm_configuration(monkeypatch, capsys) -> None:
    import information_agent.orchestration.collection as collection_module

    def fake_collect(*args, **kwargs) -> CollectionReport:
        return CollectionReport("AI", RunStatus.COMPLETED, [])

    def fail_load_dotenv() -> None:
        raise AssertionError("collect 命令不应加载 LLM 配置")

    monkeypatch.setattr(collection_module, "collect", fake_collect)
    monkeypatch.setattr("information_agent.cli.load_dotenv", fail_load_dotenv)
    monkeypatch.setattr(sys, "argv", ["information-agent", "collect", "AI", "feed"])

    main()
    payload = json.loads(capsys.readouterr().out)

    assert payload == {"topic": "AI", "status": "completed", "articles": [], "errors": []}
