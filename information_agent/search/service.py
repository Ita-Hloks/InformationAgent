from __future__ import annotations

from typing import Protocol

from ..investigation import SearchPlan
from .models import SearchAnswer


class SearchAnswerer(Protocol):
    def answer(self, plan: SearchPlan, timeout: float) -> SearchAnswer: ...
