import json
import sys
import time
from collections import Counter
from threading import Barrier
from urllib.error import HTTPError, URLError

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


def test_collect_fetches_six_sources_concurrently() -> None:
    all_sources_started = Barrier(6)

    def collector(feed: str, _: float) -> list[Evidence]:
        all_sources_started.wait(timeout=2)
        return [
            Evidence(
                f"https://example.com/{feed}",
                "AI source update",
                "This AI source contains enough content to survive normalization.",
            )
        ]

    report = collect(
        "AI",
        [f"feed-{index}" for index in range(6)],
        collector=collector,
    )

    assert report.status is RunStatus.COMPLETED
    assert len(report.articles) == 6
    assert report.errors == []


def test_collect_retries_transient_timeout_until_source_succeeds() -> None:
    attempts = 0

    def collector(_: str, __: float) -> list[Evidence]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError("temporary timeout")
        return [
            Evidence(
                "https://example.com/recovered",
                "AI source recovered",
                "This AI source recovered and returned enough article content.",
            )
        ]

    report = collect(
        "AI",
        ["flaky-feed"],
        collector=collector,
        max_attempts=3,
    )

    assert attempts == 3
    assert report.status is RunStatus.COMPLETED
    assert len(report.articles) == 1
    assert report.errors == []


def test_collect_applies_an_independent_timeout_to_each_source() -> None:
    both_sources_started = Barrier(2)
    observed_timeouts: dict[str, float] = {}

    def collector(feed: str, timeout: float) -> list[Evidence]:
        observed_timeouts[feed] = timeout
        both_sources_started.wait(timeout=1)
        return []

    report = collect(
        "AI",
        ["feed-a", "feed-b"],
        timeout_seconds=5,
        source_timeout_seconds=0.25,
        max_attempts=1,
        collector=collector,
    )

    assert observed_timeouts == {"feed-a": 0.25, "feed-b": 0.25}
    assert report.status is RunStatus.COMPLETED
    assert report.errors == []


def test_collect_retries_only_transient_http_errors() -> None:
    attempts: Counter[str] = Counter()

    def collector(feed: str, _: float) -> list[Evidence]:
        attempts[feed] += 1
        if feed == "temporarily-unavailable" and attempts[feed] == 1:
            raise HTTPError(feed, 503, "Service Unavailable", {}, None)
        if feed == "missing":
            raise HTTPError(feed, 404, "Not Found", {}, None)
        return [
            Evidence(
                "https://example.com/recovered-http",
                "AI service recovered",
                "This AI service recovered after a transient HTTP failure.",
            )
        ]

    report = collect(
        "AI",
        ["temporarily-unavailable", "missing"],
        collector=collector,
        max_attempts=3,
    )

    assert attempts == Counter({"temporarily-unavailable": 2, "missing": 1})
    assert report.status is RunStatus.PARTIAL
    assert len(report.articles) == 1
    assert len(report.errors) == 1
    assert "404" in report.errors[0]


def test_collect_retries_transient_network_errors() -> None:
    attempts = 0

    def collector(_: str, __: float) -> list[Evidence]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise URLError("connection reset")
        return [
            Evidence(
                "https://example.com/recovered-network",
                "AI network recovered",
                "This AI source recovered after a transient network failure.",
            )
        ]

    report = collect(
        "AI",
        ["flaky-network"],
        collector=collector,
        max_attempts=2,
    )

    assert attempts == 2
    assert report.status is RunStatus.COMPLETED
    assert len(report.articles) == 1
    assert report.errors == []


def test_collect_stops_retrying_when_total_deadline_expires() -> None:
    attempts = 0

    def collector(_: str, __: float) -> list[Evidence]:
        nonlocal attempts
        attempts += 1
        raise TimeoutError("still unavailable")

    started = time.monotonic()
    report = collect(
        "AI",
        ["unavailable"],
        timeout_seconds=0.05,
        source_timeout_seconds=1,
        max_attempts=10,
        collector=collector,
    )
    elapsed = time.monotonic() - started

    assert attempts < 10
    assert elapsed < 0.5
    assert report.status is RunStatus.FAILED
    assert len(report.errors) == 1


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
