from __future__ import annotations

import json
import socket
import sqlite3
import sys
from pathlib import Path

import pytest

from information_agent import cli
from information_agent.cli import build_parser
from information_agent.serialization import research_run_summaries_to_payload
from information_agent.storage import SQLiteCollectionStore
from information_agent.storage.models import ResearchRunSummary
from information_agent.storage.store import _read_only_snapshot_connection


def test_list_runs_returns_empty_list_for_initialized_database(tmp_path: Path) -> None:
    database_path = tmp_path / "runs.db"
    SQLiteCollectionStore(database_path).start_run("empty", [])
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM research_runs")

    assert SQLiteCollectionStore(database_path).list_runs(limit=20) == []


def test_list_runs_orders_and_filters_run_metadata(tmp_path: Path) -> None:
    database_path = tmp_path / "runs.db"
    store = SQLiteCollectionStore(database_path)
    first = store.start_run("first", ["one"])
    second = store.start_run("second", ["one", "two"])
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE research_runs SET created_at = ? WHERE id IN (?, ?)",
            ("2026-08-04T09:00:00+08:00", first, second),
        )
        connection.execute("UPDATE research_runs SET status = 'completed' WHERE id = ?", (second,))

    runs = store.list_runs(limit=20)

    assert [run.run_id for run in runs] == sorted((first, second))
    assert {run.run_id: run.feed_count for run in runs} == {first: 1, second: 2}
    assert store.list_runs(limit=20, status="completed")[0].run_id == second


def test_list_runs_uses_read_only_uri_without_writer_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "runs.db"
    store = SQLiteCollectionStore(database_path)
    store.start_run("wal", ["feed"])
    connect = sqlite3.connect
    connection_arguments: list[tuple[str, bool]] = []
    forbidden_actions = {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
    }

    def read_only_connect(database: str, *args: object, **kwargs: object) -> sqlite3.Connection:
        connection_arguments.append((database, bool(kwargs.get("uri"))))
        connection = connect(database, *args, **kwargs)
        connection.set_authorizer(
            lambda action, *_: (
                sqlite3.SQLITE_DENY if action in forbidden_actions else sqlite3.SQLITE_OK
            )
        )
        return connection

    def unexpected_writer_connection() -> sqlite3.Connection:
        raise AssertionError("list-runs must not initialize or migrate the database")

    monkeypatch.setattr("information_agent.storage.store.sqlite3.connect", read_only_connect)
    monkeypatch.setattr(store, "_connect", unexpected_writer_connection)

    assert store.list_runs(limit=20)[0].topic == "wal"
    assert connection_arguments == [(f"{database_path.resolve().as_uri()}?mode=ro", True)]


def test_read_only_transaction_retains_snapshot_while_writer_commits(tmp_path: Path) -> None:
    database_path = tmp_path / "runs.db"
    store = SQLiteCollectionStore(database_path)
    run_id = store.start_run("before", ["feed"])

    with _read_only_snapshot_connection(database_path) as reader:
        assert (
            reader.execute("SELECT topic FROM research_runs WHERE id = ?", (run_id,)).fetchone()[
                "topic"
            ]
            == "before"
        )
        with sqlite3.connect(database_path) as writer:
            writer.execute("UPDATE research_runs SET topic = 'after' WHERE id = ?", (run_id,))
        assert (
            reader.execute("SELECT topic FROM research_runs WHERE id = ?", (run_id,)).fetchone()[
                "topic"
            ]
            == "before"
        )

    assert store.list_runs(limit=20)[0].topic == "after"


def test_list_runs_cli_arguments_and_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = build_parser()
    args = parser.parse_args(["list-runs", "--limit", "7", "--status", "failed"])
    assert (args.limit, args.status) == (7, "failed")
    with pytest.raises(SystemExit):
        parser.parse_args(["list-runs", "--limit", "101"])
    with pytest.raises(SystemExit):
        parser.parse_args(["list-runs", "--status", "unknown"])

    run = ResearchRunSummary(
        run_id="run-1",
        topic="topic",
        status="collecting",
        started_at=_datetime("2026-08-04T09:00:00+08:00"),
        finished_at=None,
        feed_count=1,
        snapshot_count=2,
        selected_evidence_count=1,
        collection_error_count=0,
    )
    assert set(research_run_summaries_to_payload([run])["runs"][0]) == {
        "run_id",
        "topic",
        "status",
        "started_at",
        "feed_count",
        "snapshot_count",
        "selected_evidence_count",
        "collection_error_count",
    }

    database_path = tmp_path / "runs.db"
    SQLiteCollectionStore(database_path).start_run("offline", [])

    def unexpected_credentials_load() -> None:
        raise AssertionError("list-runs must not load credentials or invoke LLM workflows")

    def unexpected_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("list-runs must not make network connections")

    monkeypatch.setattr(cli, "load_dotenv", unexpected_credentials_load)
    monkeypatch.setattr(socket, "create_connection", unexpected_network)
    monkeypatch.setattr("information_agent.storage.default_database_path", lambda: database_path)
    monkeypatch.setattr(sys, "argv", ["information-agent", "list-runs"])

    cli.main()

    assert json.loads(capsys.readouterr().out)["runs"][0]["topic"] == "offline"


def _datetime(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
