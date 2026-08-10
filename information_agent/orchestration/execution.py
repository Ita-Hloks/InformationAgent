from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    """One monotonic deadline shared by every stage in a workflow invocation."""

    total_seconds: float
    deadline: float
    _clock: Clock = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.total_seconds) or self.total_seconds <= 0:
            raise ValueError("total_seconds must be a positive finite number")

    @classmethod
    def start(
        cls,
        total_seconds: float,
        *,
        clock: Clock = time.monotonic,
    ) -> ExecutionBudget:
        return cls(
            total_seconds=total_seconds,
            deadline=clock() + total_seconds,
            _clock=clock,
        )

    def remaining(self) -> float:
        return max(0.0, self.deadline - self._clock())

    def timeout_for(self, maximum_seconds: float) -> float:
        """Return a non-negative stage timeout capped by the remaining budget."""
        return min(max(0.0, maximum_seconds), self.remaining())
