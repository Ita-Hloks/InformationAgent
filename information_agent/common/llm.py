from __future__ import annotations

from typing import Any

from .call_log import CallBackup


def request_json_completion(
    *,
    client: Any,
    model: str,
    messages: list[dict[str, str]],
    timeout: float,
    stage: str,
) -> str:
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
