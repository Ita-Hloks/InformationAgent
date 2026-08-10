import json

import pytest

import information_agent.cli as cli


def _temporary_output_paths(output_path):
    return list(output_path.parent.glob(f".{output_path.name}.*.tmp"))


def test_file_output_is_replaced_atomically_after_writing_utf8_json(tmp_path) -> None:
    output_path = tmp_path / "result.json"
    output_path.write_bytes(b"old result")

    cli._write_json_output({"topic": "人工智能"}, output_path)

    assert output_path.read_bytes() == '{\n  "topic": "人工智能"\n}\n'.encode()
    assert b"\r\n" not in output_path.read_bytes()
    assert _temporary_output_paths(output_path) == []


def test_replacement_failure_preserves_existing_output_and_cleans_temporary_file(
    monkeypatch, tmp_path
) -> None:
    output_path = tmp_path / "result.json"
    existing = b"existing research result"
    output_path.write_bytes(existing)

    def fail_replace(_source, _destination) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(cli.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replacement failure"):
        cli._write_json_output({"result": "new"}, output_path)

    assert output_path.read_bytes() == existing
    assert _temporary_output_paths(output_path) == []


def test_write_failure_preserves_existing_output_and_cleans_temporary_file(
    monkeypatch, tmp_path
) -> None:
    output_path = tmp_path / "result.json"
    existing = b"existing research result"
    output_path.write_bytes(existing)
    named_temporary_file = cli.tempfile.NamedTemporaryFile

    class FailingTemporaryFile:
        def __init__(self, file) -> None:
            self.file = file
            self.name = file.name

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            self.file.close()

        def write(self, _content) -> None:
            raise OSError("simulated write failure")

    def failing_named_temporary_file(*args, **kwargs):
        return FailingTemporaryFile(named_temporary_file(*args, **kwargs))

    monkeypatch.setattr(cli.tempfile, "NamedTemporaryFile", failing_named_temporary_file)

    with pytest.raises(OSError, match="simulated write failure"):
        cli._write_json_output({"result": "new"}, output_path)

    assert output_path.read_bytes() == existing
    assert _temporary_output_paths(output_path) == []


def test_stdout_output_is_unchanged(capsys) -> None:
    cli._write_json_output({"result": "new"})

    assert json.loads(capsys.readouterr().out) == {"result": "new"}


def test_bare_output_name_uses_log_directory(monkeypatch, tmp_path) -> None:
    log_directory = tmp_path / "log"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INFORMATION_AGENT_LOG_DIR", str(log_directory))

    cli._write_json_output({"result": "new"}, cli.Path("result.json"))

    assert not (tmp_path / "result.json").exists()
    assert json.loads((log_directory / "result.json").read_text(encoding="utf-8")) == {
        "result": "new"
    }
