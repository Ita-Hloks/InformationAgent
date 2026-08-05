from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..contracts import PROJECT_TIMEZONE


def _optional_text(value: object | None) -> str | None:
    return str(value) if value is not None else None


def _parse_datetime(value: object | None) -> datetime | None:
    if value is None:
        return None
    return _required_datetime(value)


def _required_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("数据库中的日期时间必须包含时区")
    return parsed.astimezone(PROJECT_TIMEZONE)


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("数据库中的日期时间必须包含时区")
    return value.astimezone(PROJECT_TIMEZONE).isoformat(timespec="seconds")


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("分析持久化数据必须是可 JSON 序列化的值") from exc


def _error_object(error: Exception | dict[str, Any]) -> dict[str, Any]:
    if isinstance(error, dict):
        return dict(error)
    return {"type": type(error).__name__, "message": str(error)}


def _error_json(error: Exception | dict[str, Any] | None) -> str | None:
    if error is None:
        return None
    return _canonical_json(_error_object(error))


def _load_json_object(raw: str, field_name: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"数据库字段 {field_name} 必须是 JSON 对象")
    return value


def _load_json_list(raw: str) -> list[dict[str, Any]]:
    value = json.loads(raw)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("数据库错误字段必须是 JSON 对象数组")
    return [dict(item) for item in value]
