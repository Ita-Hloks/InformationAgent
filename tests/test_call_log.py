from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from information_agent.common import request_json_completion


class FakeClient:
    def __init__(self, *, content: str = "{}", error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.chat = SimpleNamespace(completions=self)

    def with_options(self, *, timeout: float) -> FakeClient:
        assert timeout > 0
        return self

    def create(self, **_: object) -> SimpleNamespace:
        if self.error is not None:
            raise self.error
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_request_json_completion_backs_up_request_and_response(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(tmp_path))
    messages = [{"role": "user", "content": "测试正文"}]

    result = request_json_completion(
        client=FakeClient(content='{"plans": []}'),
        model="test-model",
        messages=messages,
        timeout=1,
        stage="planning",
    )

    backups = list(tmp_path.glob("*.json"))
    assert result == '{"plans": []}'
    assert len(backups) == 1
    payload = json.loads(backups[0].read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["request"]["model"] == "test-model"
    assert payload["request"]["messages"] == messages
    assert payload["response"] == '{"plans": []}'


def test_request_json_completion_backs_up_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="调用失败"):
        request_json_completion(
            client=FakeClient(error=RuntimeError("调用失败")),
            model="test-model",
            messages=[{"role": "user", "content": "测试正文"}],
            timeout=1,
            stage="analysis",
        )

    backup = next(tmp_path.glob("*.json"))
    payload = json.loads(backup.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["error"] == {"type": "RuntimeError", "message": "调用失败"}
