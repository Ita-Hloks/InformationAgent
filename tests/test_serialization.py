import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from information_agent.contracts import Analysis, Evaluation, Evidence, Report, RunStatus
from information_agent.serialization import format_json_datetime, report_to_payload


def test_report_payload_uses_project_timezone_minute_precision() -> None:
    source_timezone = timezone(timedelta(hours=8))
    evidence = Evidence(
        "https://example.com/article",
        "时间格式",
        "这是一段长度超过二十个字符并用于验证时间序列化格式的正文内容。",
        published_at=datetime(2026, 7, 17, 10, 30, 45, 123456, tzinfo=source_timezone),
        collected_at=datetime(2026, 7, 17, 3, 0, 1, 987654, tzinfo=UTC),
    )
    report = Report(
        "时间格式",
        RunStatus.COMPLETED,
        Analysis("完成。", []),
        [evidence],
        Evaluation(0.0, 0.0, 0.0),
    )

    payload = report_to_payload(report)

    assert payload["evidence"][0]["published_at"] == "2026-07-17T10:30+08:00"
    assert payload["evidence"][0]["collected_at"] == "2026-07-17T11:00+08:00"
    json.dumps(payload)


def test_report_payload_preserves_missing_published_time() -> None:
    evidence = Evidence(
        "https://example.com/article",
        "缺少发布时间",
        "这是一段长度超过二十个字符并且没有发布时间的正文内容。",
    )
    report = Report(
        "时间格式",
        RunStatus.PARTIAL,
        Analysis("没有发布时间。", []),
        [evidence],
        Evaluation(0.0, 0.0, 0.0),
    )

    assert report_to_payload(report)["evidence"][0]["published_at"] is None


def test_json_datetime_rejects_naive_value() -> None:
    with pytest.raises(ValueError, match="必须包含时区"):
        format_json_datetime(datetime(2026, 7, 17, 10, 30))
