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
from ..storage import (
    AnalysisAttemptStatus,
    AnalysisRun,
    AnalysisRunStatus,
    AnalysisState,
    AnalysisStepStatus,
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
    diagnostic: dict[str, Any] | None = None


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
                if task.diagnostic is not None:
                    return self._snapshot(task)
                self._discard_task(task)

            if analysis_run.status in _terminal_statuses():
                return self._persisted_snapshot(analysis_run)
            if analysis_run.status is AnalysisRunStatus.RUNNING:
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
        analysis_run = (
            self._store.find_analysis_run_by_idempotency_key(request_id)
            if request_id
            else self._store.load_latest_analysis_run(research_run_id)
        )
        if analysis_run is not None and analysis_run.research_run_id != research_run_id:
            raise ValueError("Agent 请求不属于当前研究运行")
        if analysis_run is None:
            return None
        state = self._store.load_analysis_state(analysis_run.id)
        with self._lock:
            task = self._find_task(research_run_id, request_id)
            if task is not None and task.diagnostic is not None:
                return _state_snapshot(state, diagnostic=task.diagnostic)
        return _state_snapshot(state)

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
        if snapshot is None or snapshot["status"] not in {"created", "running"}:
            return snapshot
        resolved_request_id = snapshot.get("request_id")
        if not isinstance(resolved_request_id, str):
            return snapshot
        try:
            return self.wait(resolved_request_id, timeout=timeout)
        except TimeoutError:
            return self.get(research_run_id, request_id=resolved_request_id)

    def wait(self, request_id: str, timeout: float | None = None) -> dict[str, Any]:
        normalized_request_id = request_id.strip()
        if not normalized_request_id:
            raise ValueError("Agent request_id 不能为空")
        with self._lock:
            task = self._tasks.get(normalized_request_id)
            future = task.future if task is not None else None
            research_run_id = task.research_run_id if task is not None else None
        if future is not None:
            future.result(timeout=timeout)
            assert research_run_id is not None
            snapshot = self.get(research_run_id, request_id=normalized_request_id)
            if snapshot is not None:
                return snapshot

        analysis_run = self._store.find_analysis_run_by_idempotency_key(normalized_request_id)
        if analysis_run is None:
            raise ValueError(f"不存在的 Agent 请求：{normalized_request_id}")
        snapshot = self.get(analysis_run.research_run_id, request_id=normalized_request_id)
        if snapshot is None:
            raise ValueError(f"不存在的 Agent 请求：{normalized_request_id}")
        return snapshot

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)
        with self._lock:
            self._tasks.clear()

    def _run_task(self, task: _AgentTask) -> AgentReport:
        try:
            report = self._invoke_runner(task)
        except Exception as exc:
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
                    task.diagnostic = {
                        "phase": "persistence_failed",
                        "message": "Agent 状态持久化失败",
                        "error": diagnostic_error,
                    }
                raise exc from persistence_exc
            with self._lock:
                self._discard_task(task)
            raise

        with self._lock:
            self._discard_task(task)
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
        state = self._store.load_analysis_state(task.analysis_run_id)
        return _state_snapshot(state, diagnostic=task.diagnostic)

    def _persisted_snapshot(self, analysis_run: AnalysisRun) -> dict[str, Any]:
        return _state_snapshot(self._store.load_analysis_state(analysis_run.id))

    def _discard_task(self, task: _AgentTask) -> None:
        if self._tasks.get(task.request_id) is task:
            self._tasks.pop(task.request_id, None)


def _terminal_statuses() -> set[AnalysisRunStatus]:
    return {
        AnalysisRunStatus.COMPLETED,
        AnalysisRunStatus.PARTIAL,
        AnalysisRunStatus.FAILED,
        AnalysisRunStatus.CANCELLED,
        AnalysisRunStatus.SKIPPED,
    }


def _state_snapshot(
    state: AnalysisState,
    *,
    diagnostic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stage_details = _stage_details(state)
    status = state.run.status.value
    current_stage = next(
        (detail for detail in stage_details if detail["step_key"] == state.run.current_step_key),
        stage_details[-1] if stage_details else None,
    )
    error = state.run.errors[-1] if state.run.errors else None
    if current_stage is not None:
        error = current_stage["error"] or error
    retryable = (
        False
        if state.run.status in _terminal_statuses()
        else current_stage["retryable"]
        if current_stage is not None
        else None
    )
    snapshot = {
        "request_id": state.run.idempotency_key,
        "run_id": state.run.research_run_id,
        "analysis_run_id": state.run.id,
        "status": status,
        "phase": (
            state.run.current_step_key or state.steps[-1].step_key if state.steps else status
        ),
        "attempt": current_stage["attempt"] if current_stage is not None else 0,
        "max_attempts": _max_attempts(state),
        "retryable": retryable,
        "message": _status_message(status),
        "error": error,
        "stage_details": stage_details,
        "report": _report_from_state(state),
    }
    if diagnostic is not None:
        snapshot.update(
            phase=diagnostic["phase"],
            message=diagnostic["message"],
            error=diagnostic["error"],
        )
    return snapshot


def _stage_details(state: AnalysisState) -> list[dict[str, Any]]:
    max_attempts = _max_attempts(state)
    attempts_by_step: dict[str, list[Any]] = {}
    for attempt in state.attempts:
        attempts_by_step.setdefault(attempt.analysis_step_id, []).append(attempt)
    terminal_run = state.run.status in _terminal_statuses()
    details: list[dict[str, Any]] = []
    for step in state.steps:
        attempts = attempts_by_step.get(step.id, [])
        attempt_details = [
            {
                "attempt_no": attempt.attempt_no,
                "operation": attempt.operation,
                "status": attempt.status.value,
                "error": attempt.error,
                "retryable": (
                    not terminal_run
                    and step.status is AnalysisStepStatus.RUNNING
                    and attempt.status is AnalysisAttemptStatus.FAILED
                    and attempt.attempt_no < max_attempts
                ),
            }
            for attempt in attempts
        ]
        error = step.error or next(
            (attempt.error for attempt in reversed(attempts) if attempt.error is not None),
            None,
        )
        retryable = (
            not terminal_run
            and step.status is AnalysisStepStatus.RUNNING
            and any(
                attempt.status is AnalysisAttemptStatus.FAILED and attempt.attempt_no < max_attempts
                for attempt in attempts
            )
        )
        details.append(
            {
                "step_key": step.step_key,
                "status": step.status.value,
                "attempts": attempt_details,
                "attempt": attempts[-1].attempt_no if attempts else 0,
                "max_attempts": max_attempts,
                "error": error,
                "retryable": retryable,
            }
        )
    return details


def _max_attempts(state: AnalysisState) -> int:
    return int(state.run.config.get("max_attempts", DEFAULT_MAX_ATTEMPTS))


def _report_from_state(state: AnalysisState) -> dict[str, Any] | None:
    for artifact in reversed(state.artifacts):
        if artifact.kind == "agent_report":
            payload = dict(artifact.payload)
            payload["analysis_run_id"] = state.run.id
            return payload
    return None


def _status_message(status: str) -> str:
    return {
        "created": "Agent 已创建",
        "running": "Agent 正在运行",
        "paused": "Agent 已暂停",
        "interrupted": "Agent 已中断",
        "completed": "Agent 已完成",
        "partial": "Agent 已保存部分结果，未生成完整结论",
        "skipped": "Agent 已跳过",
        "cancelled": "Agent 已停止",
        "failed": "Agent 运行失败",
    }.get(status, "Agent 状态已更新")
