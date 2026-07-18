"""MVP 固定流程编排。"""

from typing import Any

__all__ = ["collect", "run"]


def __getattr__(name: str) -> Any:
    if name == "collect":
        from .collection import collect

        return collect
    if name == "run":
        from .workflow import run

        return run
    raise AttributeError(name)
