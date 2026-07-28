from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..contracts import PROJECT_TIMEZONE


@dataclass(frozen=True, slots=True)
class CallBackup:
    path: Path

    @classmethod
    def start(cls, *, stage: str, request: dict[str, Any]) -> CallBackup:
        created_at = datetime.now(PROJECT_TIMEZONE)
        log_directory = _log_directory()
        log_directory.mkdir(parents=True, exist_ok=True)
        path = log_directory / (f"{created_at:%Y%m%d-%H%M%S-%f}-{stage}-{uuid4().hex[:8]}.json")
        _write_json(
            path,
            {
                "created_at": created_at.isoformat(timespec="milliseconds"),
                "stage": stage,
                "status": "started",
                "request": request,
            },
        )
        return cls(path)

    def complete(self, **fields: Any) -> None:
        self._finish(status="completed", **fields)

    def fail(self, error: Exception) -> None:
        self._finish(
            status="failed",
            error={"type": type(error).__name__, "message": str(error)},
        )

    def _finish(self, *, status: str, **fields: Any) -> None:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["status"] = status
        payload["finished_at"] = datetime.now(PROJECT_TIMEZONE).isoformat(timespec="milliseconds")
        payload.update(fields)
        _write_json(self.path, payload)


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
