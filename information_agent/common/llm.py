from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from .call_log import CallBackup

DEFAULT_LLM_TIMEOUT_SECONDS = 300.0
DEFAULT_LLM_MAX_ATTEMPTS = 3
LLM_RETRY_DELAYS_SECONDS = (5.0, 10.0)


def is_retryable_llm_error(error: BaseException) -> bool:
    """Return whether an API error can reasonably succeed on a repeated call."""

    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return status_code in {408, 409, 429}
    if isinstance(status_code, int):
        return 500 <= status_code < 600
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return True
    return type(error).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
        "ConflictError",
    }


def request_json_completion(
    *,
    client: Any,
    model: str,
    messages: list[dict[str, str]],
    timeout: float,
    stage: str,
    max_attempts: int = 1,
    sleep: Callable[[float], None] = time.sleep,
    record_content: bool = True,
    metadata: dict[str, Any] | None = None,
) -> str:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")

    for attempt in range(1, max_attempts + 1):
        backup = CallBackup.start(
            stage=stage,
            request={"model": model, "messages": messages, "attempt": attempt},
            record_content=record_content,
            metadata=metadata,
        )
        try:
            # 通过提示约束 JSON，兼容不支持 response_format 的 OpenAI-compatible 网关。
            response = client.with_options(timeout=timeout).chat.completions.create(
                model=model,
                messages=messages,
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:
            retryable = is_retryable_llm_error(exc)
            _annotate_error(exc, stage=stage, attempt=attempt, retryable=retryable)
            backup.fail(exc)
            if attempt < max_attempts and retryable:
                sleep(LLM_RETRY_DELAYS_SECONDS[attempt - 1])
                continue
            raise

        backup.complete(response=content)
        return content
    raise AssertionError("LLM 重试循环必须返回或抛出异常")


def _annotate_error(
    error: BaseException,
    *,
    stage: str,
    attempt: int,
    retryable: bool,
) -> None:
    for name, value in (
        ("stage", stage),
        ("attempt", attempt),
        ("retryable", retryable),
    ):
        try:
            setattr(error, name, value)
        except (AttributeError, TypeError):
            pass
