from __future__ import annotations

import math
from typing import Any

from .call_log import CallBackup

DEFAULT_LLM_TIMEOUT_SECONDS = 300.0


def is_retryable_llm_error(error: BaseException) -> bool:
    """Return whether an API error can reasonably succeed on a repeated call."""

    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return status_code in {408, 409, 429}
    return True


def request_json_completion(
    *,
    client: Any,
    model: str,
    messages: list[dict[str, str]],
    timeout: float,
    stage: str,
) -> str:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")

    backup = CallBackup.start(
        stage=stage,
        request={"model": model, "messages": messages},
    )
    try:
        response = client.with_options(timeout=timeout).chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=messages,
        )
        content = response.choices[0].message.content or ""
    except Exception as exc:
        backup.fail(exc)
        raise

    backup.complete(response=content)
    return content
