from __future__ import annotations

from dataclasses import dataclass

from ..contracts import CollectionReport


@dataclass(frozen=True, slots=True)
class PersistedCollection:
    """已提交到数据库的一次粗处理结果。"""

    run_id: str
    report: CollectionReport
