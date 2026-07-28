"""MVP 固定流程编排。"""

from typing import Any

__all__ = ["collect", "ingest", "plan", "run", "search"]


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
    if name == "search":
        from .search_workflow import search

        return search
    raise AttributeError(name)
