"""MVP 固定流程编排。"""

from typing import Any

__all__ = ["collect", "ingest", "plan", "plan_run", "run", "search"]


def __getattr__(name: str) -> Any:
    if name == "collect":
        from .collection import collect

        return collect
    if name == "ingest":
        from .ingestion import ingest

        return ingest
    if name == "run":
        from .workflow import run

        return run
    if name == "plan":
        from .planning import plan

        return plan
    if name == "plan_run":
        from .database_planning import plan_run

        return plan_run
    if name == "search":
        from .search_workflow import search

        return search
    raise AttributeError(name)
