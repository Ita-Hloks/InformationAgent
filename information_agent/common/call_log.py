from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..contracts import PROJECT_TIMEZONE

LOG_RETENTION_DAYS = 30
LOG_MAX_BYTES = 500 * 1024 * 1024
LOG_CLEANUP_MAX_DELETIONS = 100


@dataclass(frozen=True, slots=True)
class LogUsage:
    file_count: int
    total_bytes: int
    earliest_at: str | None


@dataclass(frozen=True, slots=True)
class LogCleanupReport:
    usage: LogUsage
    deleted_count: int
    deleted_bytes: int


@dataclass(frozen=True, slots=True)
class CallBackup:
    path: Path
    record_content: bool
    started_monotonic: float

    @classmethod
    def start(
        cls,
        *,
        stage: str,
        request: dict[str, Any],
        record_content: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> CallBackup:
        created_at = datetime.now(PROJECT_TIMEZONE)
        log_directory = get_log_directory()
        log_directory.mkdir(parents=True, exist_ok=True)
        path = log_directory / (f"{created_at:%Y%m%d-%H%M%S-%f}-{stage}-{uuid4().hex[:8]}.json")
        payload: dict[str, Any] = {
            "created_at": created_at.isoformat(timespec="milliseconds"),
            "stage": stage,
            "status": "started",
            "metadata": metadata or {},
        }
        if record_content:
            payload["request"] = request
        else:
            payload["request_metadata"] = _request_metadata(request)
        _write_json(path, payload)
        _cleanup_after_write(log_directory)
        return cls(path, record_content, time.monotonic())

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
        payload["duration_ms"] = round((time.monotonic() - self.started_monotonic) * 1000, 3)
        if self.record_content:
            payload.update(fields)
        else:
            payload.update(_content_free_fields(fields))
        _write_json(self.path, payload)
        _cleanup_after_write(self.path.parent)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _request_metadata(request: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("model", "stage", "request_id", "attempt"):
        if key in request:
            metadata[key] = request[key]
    messages = request.get("messages")
    if isinstance(messages, list):
        metadata["message_count"] = len(messages)
    return metadata


def _content_free_fields(fields: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "response" in fields:
        response = fields["response"]
        result["response_metadata"] = {
            "present": response is not None,
            "chars": len(str(response)) if response is not None else 0,
        }
    if "result" in fields:
        result["result_metadata"] = {"present": fields["result"] is not None}
    for key, value in fields.items():
        if key not in {"request", "response", "result"}:
            result[key] = value
    return result


def inspect_log_directory(directory: Path | None = None) -> LogUsage:
    files = _log_files(directory or get_log_directory())
    return LogUsage(
        file_count=len(files),
        total_bytes=sum(size for _, size, _ in files),
        earliest_at=_earliest_at(files),
    )


def cleanup_log_directory(
    directory: Path | None = None,
    *,
    retention_days: int = LOG_RETENTION_DAYS,
    max_bytes: int = LOG_MAX_BYTES,
    max_deletions: int = LOG_CLEANUP_MAX_DELETIONS,
    now: datetime | None = None,
) -> LogCleanupReport:
    if retention_days < 0:
        raise ValueError("retention_days must be non-negative")
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    if max_deletions <= 0:
        raise ValueError("max_deletions must be positive")

    log_directory = directory or get_log_directory()
    files = _log_files(log_directory)
    cutoff = (now or datetime.now(PROJECT_TIMEZONE)) - timedelta(days=retention_days)
    cutoff_timestamp = cutoff.timestamp()
    deleted_count = 0
    deleted_bytes = 0
    remaining = list(files)

    for path, size, modified_timestamp in sorted(files, key=lambda item: item[2]):
        if deleted_count >= max_deletions or modified_timestamp >= cutoff_timestamp:
            break
        if _delete_log_file(path):
            deleted_count += 1
            deleted_bytes += size
            remaining = [item for item in remaining if item[0] != path]

    current_size = sum(size for _, size, _ in remaining)
    for path, size, _ in sorted(remaining, key=lambda item: item[2]):
        if current_size <= max_bytes or deleted_count >= max_deletions:
            break
        if _delete_log_file(path):
            deleted_count += 1
            deleted_bytes += size
            current_size -= size

    usage = inspect_log_directory(log_directory)
    return LogCleanupReport(usage, deleted_count, deleted_bytes)


def clear_log_directory(directory: Path | None = None) -> int:
    deleted_count = 0
    for path, _, _ in _log_files(directory or get_log_directory()):
        if _delete_log_file(path):
            deleted_count += 1
    return deleted_count


def _cleanup_after_write(directory: Path) -> None:
    try:
        cleanup_log_directory(directory)
    except OSError:
        # Logging must not make the underlying model request fail.
        return


def _log_files(directory: Path) -> list[tuple[Path, int, float]]:
    if not directory.is_dir():
        return []
    files: list[tuple[Path, int, float]] = []
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append((path, stat.st_size, stat.st_mtime))
    return files


def _delete_log_file(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _earliest_at(files: list[tuple[Path, int, float]]) -> str | None:
    if not files:
        return None
    timestamp = min(item[2] for item in files)
    return datetime.fromtimestamp(timestamp, PROJECT_TIMEZONE).isoformat(timespec="seconds")


def get_log_directory() -> Path:
    configured = os.getenv("INFORMATION_AGENT_LOG_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "log"
