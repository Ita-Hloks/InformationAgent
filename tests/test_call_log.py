from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from information_agent.common import request_json_completion


class FakeClient:
    def __init__(self, *, content: str | None = "{}", error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.chat = SimpleNamespace(completions=self)
        self.with_options_calls = 0
        self.create_calls = 0
        self.requests: list[dict[str, object]] = []

    def with_options(self, *, timeout: float) -> FakeClient:
        self.with_options_calls += 1
        assert timeout > 0
        return self

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.create_calls += 1
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_request_json_completion_backs_up_request_and_response(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(tmp_path))
    messages = [{"role": "user", "content": "测试正文"}]
    client = FakeClient(content='{"plans": []}')

    result = request_json_completion(
        client=client,
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
    assert client.with_options_calls == 1
    assert client.create_calls == 1
    assert client.requests == [{"model": "test-model", "messages": messages}]


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), float("-inf")])
def test_request_json_completion_rejects_invalid_timeout_before_side_effects(
    tmp_path, monkeypatch, timeout
) -> None:
    log_dir = tmp_path / "call-backups"
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(log_dir))
    client = FakeClient()

    with pytest.raises(ValueError, match="positive finite"):
        request_json_completion(
            client=client,
            model="test-model",
            messages=[{"role": "user", "content": "测试正文"}],
            timeout=timeout,
            stage="planning",
        )

    assert not log_dir.exists()
    assert client.with_options_calls == 0
    assert client.create_calls == 0


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


def test_request_json_completion_preserves_empty_response(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(tmp_path))

    result = request_json_completion(
        client=FakeClient(content=None),
        model="test-model",
        messages=[{"role": "user", "content": "测试正文"}],
        timeout=1,
        stage="planning",
    )

    backup = next(tmp_path.glob("*.json"))
    payload = json.loads(backup.read_text(encoding="utf-8"))
    assert result == ""
    assert payload["response"] == ""
