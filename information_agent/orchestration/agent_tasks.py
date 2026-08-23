from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..agent import AgentReport
from ..common import DEFAULT_LLM_TIMEOUT_SECONDS
from ..serialization import agent_report_to_payload
from ..storage import (
    AnalysisRun,
    AnalysisRunStatus,
    SQLiteCollectionStore,
    default_database_path,
)
from .agent_workflow import (
    DEFAULT_MAX_AGENT_STEPS,
    DEFAULT_MAX_ATTEMPTS,
    agent_run,
)

AgentRunner = Callable[..., AgentReport]


@dataclass(slots=True)
class _AgentTask:
    request_id: str
    research_run_id: str
    analysis_run_id: str
    timeout_seconds: float
    max_steps: int
    max_attempts: int
    stop_event: threading.Event = field(default_factory=threading.Event)
    future: Future[AgentReport] | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    report: AgentReport | None = None
    error: dict[str, str] | None = None


class AgentTaskManager:
    """Run one persisted Agent in the background and expose resumable snapshots."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        runner: AgentRunner = agent_run,
        max_workers: int = 2,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("Agent 后台并发数必须大于 0")
        self.database_path = Path(database_path or default_database_path())
        self._runner = runner
        self._store = SQLiteCollectionStore(self.database_path)
        self._store.recover_running_agent_runs()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="information-agent",
        )
        self._lock = threading.RLock()
        self._tasks: dict[str, _AgentTask] = {}

    def submit(
        self,
        research_run_id: str,
        *,
        request_id: str,
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        max_steps: int = DEFAULT_MAX_AGENT_STEPS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> dict[str, Any]:
        normalized_request_id = request_id.strip()
        if not normalized_request_id:
            raise ValueError("Agent request_id 不能为空")
        config = {
            "timeout_seconds": timeout_seconds,
            "max_steps": max_steps,
            "max_attempts": max_attempts,
        }
        analysis_run = self._store.create_analysis_run(
            research_run_id,
            "agent_research",
            config,
            idempotency_key=normalized_request_id,
        )

        with self._lock:
            task = self._tasks.get(normalized_request_id)
            if task is not None and task.research_run_id == research_run_id:
                if task.future is not None and not task.future.done():
                    return self._snapshot(task)
                if task.report is not None or task.error is not None:
                    return self._snapshot(task)

            if analysis_run.status in _terminal_statuses():
                return self._persisted_snapshot(analysis_run)
            if analysis_run.status is AnalysisRunStatus.RUNNING:
                # A task from a previous process may still own the running row.
                return self._persisted_snapshot(analysis_run)

            task = _AgentTask(
                request_id=normalized_request_id,
                research_run_id=research_run_id,
                analysis_run_id=analysis_run.id,
                timeout_seconds=timeout_seconds,
                max_steps=max_steps,
                max_attempts=max_attempts,
                progress={
                    "phase": "queued",
                    "attempt": 0,
                    "max_attempts": max_attempts,
                    "retryable": None,
                },
            )
            self._tasks[normalized_request_id] = task
            task.future = self._executor.submit(self._run_task, task)
            return self._snapshot(task)

    def get(
        self,
        research_run_id: str,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any] | None:
        latest_report = self._store.load_latest_agent_report(research_run_id)
        with self._lock:
            task = self._find_task(research_run_id, request_id)
            if task is not None:
                return self._snapshot(task)

        analysis_run = (
            self._store.find_analysis_run_by_idempotency_key(request_id)
            if request_id
            else self._store.load_latest_analysis_run(research_run_id)
        )
        if analysis_run is not None and analysis_run.research_run_id != research_run_id:
            raise ValueError("Agent 请求不属于当前研究运行")
        if analysis_run is None:
            if request_id is not None:
                return None
            if latest_report is None:
                return None
            return _report_snapshot(
                latest_report,
                research_run_id=research_run_id,
                request_id=None,
            )
        return self._persisted_snapshot(
            analysis_run,
            persisted_report=(
                latest_report
                if request_id is None
                and latest_report is not None
                and latest_report.get("analysis_run_id") == analysis_run.id
                else None
            ),
        )

    def stop(
        self,
        research_run_id: str,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            task = self._find_task(research_run_id, request_id)
            if task is not None and task.future is not None and not task.future.done():
                task.stop_event.set()
                task.progress = {
                    **task.progress,
                    "phase": "stopping",
                    "message": "正在停止 Agent",
                }
                return self._snapshot(task)

        return self.get(research_run_id, request_id=request_id)

    def stop_and_wait(
        self,
        research_run_id: str,
        *,
        request_id: str | None = None,
        timeout: float = 2.0,
    ) -> dict[str, Any] | None:
        snapshot = self.stop(research_run_id, request_id=request_id)
        if snapshot is None or snapshot["status"] not in {"stopping", "running"}:
            return snapshot
        resolved_request_id = snapshot.get("request_id")
        if not isinstance(resolved_request_id, str):
            return snapshot
        try:
            return self.wait(resolved_request_id, timeout=timeout)
        except TimeoutError:
            return self.get(research_run_id, request_id=resolved_request_id)

    def wait(self, request_id: str, timeout: float | None = None) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(request_id)
            if task is None or task.future is None:
                raise ValueError(f"不存在的 Agent 请求：{request_id}")
            future = task.future
        future.result(timeout=timeout)
        with self._lock:
            return self._snapshot(task)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _run_task(self, task: _AgentTask) -> AgentReport:
        try:
            report = self._invoke_runner(task)
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            try:
                self._store.set_analysis_run_status(
                    task.analysis_run_id,
                    AnalysisRunStatus.FAILED,
                    error=exc,
                )
            except Exception as persistence_exc:
                diagnostic_error = {
                    "type": "AgentPersistenceError",
                    "message": (
                        f"Agent 异常：{type(exc).__name__}: {exc}；"
                        f"状态持久化失败：{type(persistence_exc).__name__}: {persistence_exc}"
                    ),
                }
                with self._lock:
                    task.error = diagnostic_error
                    task.progress = {
                        **task.progress,
                        "phase": "persistence_failed",
                        "message": "Agent 状态持久化失败",
                        "retryable": False,
                        "error": diagnostic_error,
                    }
                raise exc from persistence_exc
            with self._lock:
                task.error = error
                task.progress = {
                    **task.progress,
                    "phase": "failed",
                    "retryable": False,
                    "error": error,
                }
            raise

        with self._lock:
            task.report = report
            task.progress = {
                **task.progress,
                "phase": (
                    "completed" if report.status.value == "completed" else report.stop_reason.value
                ),
                "retryable": False,
            }
        return report

    def _invoke_runner(self, task: _AgentTask) -> AgentReport:
        parameters = inspect.signature(self._runner).parameters
        kwargs: dict[str, Any] = {
            "database_path": self.database_path,
            "timeout_seconds": task.timeout_seconds,
            "max_steps": task.max_steps,
            "max_attempts": task.max_attempts,
        }
        optional = {
            "idempotency_key": task.request_id,
            "should_stop": task.stop_event.is_set,
            "on_progress": lambda payload: self._update_progress(task, payload),
        }
        for name, value in optional.items():
            if name in parameters:
                kwargs[name] = value
        return self._runner(task.research_run_id, **kwargs)

    def _update_progress(self, task: _AgentTask, payload: dict[str, Any]) -> None:
        with self._lock:
            task.progress = {**task.progress, **payload}

    def _find_task(
        self,
        research_run_id: str,
        request_id: str | None,
    ) -> _AgentTask | None:
        if request_id is not None:
            task = self._tasks.get(request_id)
            return task if task and task.research_run_id == research_run_id else None
        candidates = [
            task for task in self._tasks.values() if task.research_run_id == research_run_id
        ]
        if not candidates:
            return None
        return candidates[-1]

    def _snapshot(self, task: _AgentTask) -> dict[str, Any]:
        report = agent_report_to_payload(task.report) if task.report is not None else None
        status = _task_status(task)
        return {
            "request_id": task.request_id,
            "run_id": task.research_run_id,
            "analysis_run_id": task.analysis_run_id,
            "status": status,
            "phase": task.progress.get("phase", status),
            "attempt": task.progress.get("attempt", 0),
            "max_attempts": task.max_attempts,
            "retryable": task.progress.get("retryable"),
            "message": task.progress.get("message") or _status_message(status),
            "error": task.error or task.progress.get("error"),
            "report": report,
        }

    def _persisted_snapshot(
        self,
        analysis_run: AnalysisRun,
        *,
        persisted_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        report = persisted_report or self._store.load_analysis_report(analysis_run.id)
        if report is not None:
            report = {
                **report,
                "analysis_run_id": analysis_run.id,
            }
            return _report_snapshot(
                report,
                research_run_id=analysis_run.research_run_id,
                request_id=analysis_run.idempotency_key,
                analysis_run_id=analysis_run.id,
            )
        status = _analysis_status(analysis_run.status)
        error = analysis_run.errors[-1] if analysis_run.errors else None
        return {
            "request_id": analysis_run.idempotency_key,
            "run_id": analysis_run.research_run_id,
            "analysis_run_id": analysis_run.id,
            "status": status,
            "phase": analysis_run.current_step_key or status,
            "attempt": 0,
            "max_attempts": analysis_run.config.get("max_attempts", DEFAULT_MAX_ATTEMPTS),
            "retryable": None,
            "message": _status_message(status),
            "error": error,
            "report": None,
        }


def _terminal_statuses() -> set[AnalysisRunStatus]:
    return {
        AnalysisRunStatus.COMPLETED,
        AnalysisRunStatus.PARTIAL,
        AnalysisRunStatus.FAILED,
        AnalysisRunStatus.CANCELLED,
        AnalysisRunStatus.SKIPPED,
    }


def _task_status(task: _AgentTask) -> str:
    if task.report is not None:
        return (
            "completed"
            if task.report.status.value == "completed"
            else ("cancelled" if task.report.stop_reason.value == "cancelled" else "partial")
        )
    if task.error is not None:
        return "failed"
    if task.stop_event.is_set():
        return "stopping"
    if task.future is None or not task.future.running():
        return "queued"
    return "running"


def _analysis_status(status: AnalysisRunStatus) -> str:
    return {
        AnalysisRunStatus.CREATED: "queued",
        AnalysisRunStatus.RUNNING: "running",
        AnalysisRunStatus.PAUSED: "partial",
        AnalysisRunStatus.INTERRUPTED: "partial",
        AnalysisRunStatus.COMPLETED: "completed",
        AnalysisRunStatus.PARTIAL: "partial",
        AnalysisRunStatus.SKIPPED: "partial",
        AnalysisRunStatus.FAILED: "failed",
        AnalysisRunStatus.CANCELLED: "cancelled",
    }[status]


def _report_snapshot(
    report: dict[str, Any],
    *,
    research_run_id: str,
    request_id: str | None,
    analysis_run_id: str | None = None,
) -> dict[str, Any]:
    resolved_analysis_run_id = analysis_run_id or report.get("analysis_run_id")
    return {
        "request_id": request_id,
        "run_id": research_run_id,
        "analysis_run_id": resolved_analysis_run_id,
        "status": report.get("status", "partial"),
        "phase": "completed" if report.get("status") == "completed" else report.get("stop_reason"),
        "attempt": report.get("steps", 0),
        "max_attempts": DEFAULT_MAX_ATTEMPTS,
        "retryable": False,
        "message": _status_message(report.get("status", "partial")),
        "error": (
            {"type": "AgentRunError", "message": "; ".join(report["errors"])}
            if report.get("errors")
            else None
        ),
        "report": report,
    }


def _status_message(status: str) -> str:
    return {
        "queued": "Agent 已排队",
        "running": "Agent 正在运行",
        "stopping": "正在停止 Agent",
        "completed": "Agent 已完成",
        "partial": "Agent 已保存部分结果，未生成完整结论",
        "cancelled": "Agent 已停止",
        "failed": "Agent 运行失败",
    }.get(status, "Agent 状态已更新")
