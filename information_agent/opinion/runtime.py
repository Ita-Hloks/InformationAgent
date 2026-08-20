from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import datetime

from ..contracts import project_now
from .models import Attempt

Clock = Callable[[], float]
WallClock = Callable[[], datetime]


class OpinionTimeoutError(TimeoutError):
    """A stage could not start or finish before the shared run deadline."""

    code = "timeout"

    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(f"{stage} 阶段已超出舆情分析总时限")


class OpinionRetryExhaustedError(RuntimeError):
    """A retryable stage failed on every allowed attempt."""

    code = "retry_exhausted"

    def __init__(self, stage: str, cause: BaseException) -> None:
        self.stage = stage
        self.cause = cause
        super().__init__(f"{stage} 阶段重试耗尽：{cause}")


def validate_deadline(timeout: float, *, clock: Clock, deadline: float | None) -> float:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    if deadline is None:
        return clock() + timeout
    if not math.isfinite(deadline):
        raise ValueError("deadline must be a finite number")
    return deadline


def remaining_time(deadline: float, *, clock: Clock) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        return 0.0
    return remaining


def run_attempt(
    *,
    stage: str,
    attempt: int,
    deadline: float,
    clock: Clock,
    operation: Callable[[float], object],
    attempts: list[Attempt],
    wall_clock: WallClock = project_now,
    secrets: tuple[str, ...] = (),
) -> object:
    timeout = remaining_time(deadline, clock=clock)
    if timeout <= 0:
        raise OpinionTimeoutError(stage)

    started_at = wall_clock().isoformat()
    try:
        result = operation(timeout)
        if remaining_time(deadline, clock=clock) <= 0:
            raise OpinionTimeoutError(stage)
    except Exception as exc:
        outcome = "timed_out" if isinstance(exc, TimeoutError) else "failed"
        attempts.append(
            Attempt(
                stage=stage,
                attempt=attempt,
                started_at=started_at,
                finished_at=wall_clock().isoformat(),
                outcome=outcome,
                error_code=str(getattr(exc, "code", "failed")),
                error_summary=error_summary(exc, secrets=secrets),
            )
        )
        raise

    attempts.append(
        Attempt(
            stage=stage,
            attempt=attempt,
            started_at=started_at,
            finished_at=wall_clock().isoformat(),
            outcome="succeeded",
        )
    )
    return result


def error_summary(error: BaseException, *, secrets: tuple[str, ...] = ()) -> str:
    message = str(error).strip() or type(error).__name__
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:500]


def retry_delay(attempt: int, remaining: float) -> float:
    """Return a small bounded delay without allocating time outside the budget."""
    if remaining <= 0:
        return 0.0
    return min(0.05 * (2 ** max(0, attempt - 1)), remaining)


def default_clock() -> Clock:
    return time.monotonic
