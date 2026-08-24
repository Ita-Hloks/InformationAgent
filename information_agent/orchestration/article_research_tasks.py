from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from typing import Any, Protocol

from ..storage import (
    ArticleResearchRun,
    ReaderArticle,
    ReaderAutomationSettings,
    SQLiteCollectionStore,
    default_database_path,
)
from .agent_tasks import AgentTaskManager


class AgentTasks(Protocol):
    def submit(
        self,
        research_run_id: str,
        *,
        request_id: str,
        timeout_seconds: float,
        max_steps: int,
        max_attempts: int,
    ) -> dict[str, Any]: ...

    def wait(self, request_id: str, timeout: float | None = None) -> dict[str, Any]: ...

    def get(
        self,
        research_run_id: str,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any] | None: ...

    def shutdown(self) -> None: ...


class ArticleResearchTaskManager:
    """串行执行文章研究，并在待执行任务中优先处理手动请求。"""

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        store: SQLiteCollectionStore | None = None,
        agent_tasks: AgentTasks | None = None,
    ) -> None:
        database = Path(database_path or default_database_path())
        self._store = store or SQLiteCollectionStore(database)
        self._owns_agent_tasks = agent_tasks is None
        self._agent_tasks = agent_tasks or AgentTaskManager(database, max_workers=1)
        self._condition = threading.Condition()
        self._manual_queue: deque[str] = deque()
        self._auto_queue: deque[str] = deque()
        self._queued_ids: set[str] = set()
        self._closed = False
        self._store.recover_running_article_research_runs()
        for run in reversed(self._store.list_article_research_runs(limit=200)):
            if run.status == "queued":
                self._enqueue(run)
        self._worker = threading.Thread(
            target=self._work_loop,
            name="article-research",
            daemon=True,
        )
        self._worker.start()

    def submit(
        self,
        article: ReaderArticle,
        *,
        mode: str,
        settings: ReaderAutomationSettings,
        request_id: str | None = None,
    ) -> ArticleResearchRun:
        config = {
            "timeout_seconds": settings.agent_timeout_seconds,
            "max_steps": settings.max_searches,
            "max_attempts": settings.max_attempts,
        }
        run = self._store.create_article_research_run(
            article,
            mode=mode,
            config=config,
            request_id=request_id,
        )
        if run.status == "queued":
            self._enqueue(run)
        return run

    def shutdown(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if self._owns_agent_tasks:
            self._agent_tasks.shutdown()

    def agent_snapshot(self, run: ArticleResearchRun) -> dict[str, Any] | None:
        if run.status == "queued":
            return None
        return self._agent_tasks.get(run.id, request_id=run.agent_request_id)

    def _enqueue(self, run: ArticleResearchRun) -> None:
        with self._condition:
            if run.id in self._queued_ids or self._closed:
                return
            queue = self._manual_queue if run.mode == "manual" else self._auto_queue
            queue.append(run.id)
            self._queued_ids.add(run.id)
            self._condition.notify()

    def _work_loop(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._closed or self._manual_queue or self._auto_queue
                )
                if self._closed:
                    return
                queue = self._manual_queue if self._manual_queue else self._auto_queue
                run_id = queue.popleft()
                self._queued_ids.discard(run_id)
            self._run_one(run_id)

    def _run_one(self, run_id: str) -> None:
        run = self._store.get_article_research_run(run_id)
        if run is None or run.status != "queued":
            return
        self._store.update_article_research_run(run_id, status="running")
        try:
            started = self._agent_tasks.submit(
                run.id,
                request_id=run.agent_request_id,
                timeout_seconds=float(run.config["timeout_seconds"]),
                max_steps=int(run.config["max_steps"]),
                max_attempts=int(run.config["max_attempts"]),
            )
            analysis_run_id = started.get("analysis_run_id")
            if isinstance(analysis_run_id, str):
                self._store.update_article_research_run(
                    run_id,
                    status="running",
                    analysis_run_id=analysis_run_id,
                )
            finished = self._agent_tasks.wait(run.agent_request_id)
        except Exception as exc:
            self._store.update_article_research_run(run_id, status="failed", error=exc)
            return

        analysis_run_id = finished.get("analysis_run_id")
        terminal_status = _article_status(str(finished.get("status") or "failed"))
        self._store.update_article_research_run(
            run_id,
            status=terminal_status,
            analysis_run_id=analysis_run_id if isinstance(analysis_run_id, str) else None,
            error=finished.get("error") if terminal_status == "failed" else None,
        )


def _article_status(agent_status: str) -> str:
    if agent_status == "completed":
        return "completed"
    if agent_status == "cancelled":
        return "cancelled"
    if agent_status == "failed":
        return "failed"
    return "partial"
