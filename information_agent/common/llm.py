from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..contracts import PROJECT_TIMEZONE


def request_json_completion(
    *,
    client: Any,
    model: str,
    messages: list[dict[str, str]],
    timeout: float,
    stage: str,
) -> str:
    backup_path = _create_backup(stage=stage, model=model, messages=messages)
    try:
        response = client.with_options(timeout=timeout).chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=messages,
        )
        content = response.choices[0].message.content or "{}"
    except Exception as exc:
        _finish_backup(
            backup_path,
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise

    _finish_backup(backup_path, status="completed", response=content)
    return content


def _create_backup(*, stage: str, model: str, messages: list[dict[str, str]]) -> Path:
    created_at = datetime.now(PROJECT_TIMEZONE)
    log_directory = _log_directory()
    log_directory.mkdir(parents=True, exist_ok=True)
    path = log_directory / (f"{created_at:%Y%m%d-%H%M%S-%f}-{stage}-{uuid4().hex[:8]}.json")
    _write_json(
        path,
        {
            "created_at": created_at.isoformat(timespec="milliseconds"),
            "stage": stage,
            "model": model,
            "status": "started",
            "messages": messages,
        },
    )
    return path


def _finish_backup(path: Path, *, status: str, **fields: Any) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["status"] = status
    payload["finished_at"] = datetime.now(PROJECT_TIMEZONE).isoformat(timespec="milliseconds")
    payload.update(fields)
    _write_json(path, payload)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _log_directory() -> Path:
    configured = os.getenv("INFORMATION_AGENT_LOG_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "log"
