from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from information_agent.cli import main
from information_agent.collection import FeedFetchResult, RawFeedEntry
from information_agent.contracts import CollectionReport, RunStatus
from information_agent.normalization import derive_article
from information_agent.orchestration.ingestion import ingest
from information_agent.selection import SelectedEvidence
from information_agent.storage import PersistedCollection, SQLiteCollectionStore


def test_ingest_saves_all_normalized_articles_and_selected_evidence(tmp_path: Path) -> None:
    database_path = tmp_path / "information-agent.db"

    def collector(_: str, __: float) -> list[RawFeedEntry]:
        return [
            RawFeedEntry(
                "https://example.com/ai",
                "AI 芯片发布",
                "这是一篇长度超过二十个字符并与 AI 芯片主题相关的文章正文。",
            ),
            RawFeedEntry(
                "https://example.com/weather",
                "天气预报",
                "这是一篇长度超过二十个字符但与研究主题无关的天气文章正文。",
            ),
        ]

    result = ingest("AI 芯片", ["feed"], database_path=database_path, collector=collector)
    selected = SQLiteCollectionStore(database_path).load_selected_evidence(result.run_id)

    assert result.report.status is RunStatus.COMPLETED
    assert [item.source_url for item in selected] == ["https://example.com/ai"]
    assert selected[0].evidence_id == 1

    with sqlite3.connect(database_path) as connection:
        snapshot_count = connection.execute("SELECT COUNT(*) FROM article_snapshots").fetchone()[0]
        evidence_rows = connection.execute(
            "SELECT selected, evidence_no FROM run_evidence ORDER BY selected DESC"
        ).fetchall()
        evidence_columns = {row[1] for row in connection.execute("PRAGMA table_info(run_evidence)")}

    assert snapshot_count == 2
    assert evidence_rows == [(1, 1), (0, None)]
    assert "relevance_score" not in evidence_columns


def test_ingest_persists_selected_segments_from_mixed_entry(tmp_path: Path) -> None:
    database_path = tmp_path / "information-agent.db"

    def collector(_: str, __: float) -> list[RawFeedEntry]:
        return [
            RawFeedEntry(
                "https://example.com/digest",
                "今日科技汇总",
                "第一篇：AI 芯片发布，厂商公布了完整测试结果和比较基线。\n\n"
                "第二篇：手机更新，厂商公布了新的产品计划和发布时间。",
            )
        ]

    class Selector:
        def select(self, topic, items, *, limit, timeout):
            base = items[0]
            return [
                SelectedEvidence(
                    derive_article(
                        base,
                        title="AI 芯片发布",
                        content="第一篇：AI 芯片发布，厂商公布了完整测试结果和比较基线。",
                    ),
                    1,
                )
            ]

    result = ingest(
        "AI 芯片",
        ["feed"],
        database_path=database_path,
        collector=collector,
        relevance_selector=Selector(),
    )
    selected = SQLiteCollectionStore(database_path).load_selected_evidence(result.run_id)

    assert result.report.status is RunStatus.COMPLETED
    assert len(selected) == 1
    assert selected[0].content.startswith("第一篇：AI 芯片发布")

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT selected, evidence_no FROM run_evidence ORDER BY selected DESC"
        ).fetchall()

    assert rows == [(1, 1), (0, None)]


def test_store_migrates_legacy_relevance_score_column(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            INSERT INTO schema_migrations VALUES (1, 'now');
            INSERT INTO schema_migrations VALUES (2, 'now');
            INSERT INTO schema_migrations VALUES (3, 'now');
            CREATE TABLE run_evidence (
                run_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                evidence_no INTEGER,
                relevance_score REAL,
                selected INTEGER NOT NULL,
                PRIMARY KEY (run_id, snapshot_id),
                UNIQUE (run_id, evidence_no)
            );
            """
        )

    store = SQLiteCollectionStore(database_path)
    with store._connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(run_evidence)")}

    assert "relevance_score" not in columns


def test_ingest_reuses_unchanged_article_snapshot_across_runs(tmp_path: Path) -> None:
    database_path = tmp_path / "information-agent.db"

    def collector(_: str, __: float) -> list[RawFeedEntry]:
        return [
            RawFeedEntry(
                "https://example.com/ai",
                "AI 芯片发布",
                "这是一篇长度超过二十个字符并与 AI 芯片主题相关的文章正文。",
            )
        ]

    first = ingest("AI 芯片", ["feed"], database_path=database_path, collector=collector)
    second = ingest("AI 芯片", ["feed"], database_path=database_path, collector=collector)

    with sqlite3.connect(database_path) as connection:
        snapshot_count = connection.execute("SELECT COUNT(*) FROM article_snapshots").fetchone()[0]
        evidence_count = connection.execute("SELECT COUNT(*) FROM run_evidence").fetchone()[0]

    assert first.run_id != second.run_id
    assert snapshot_count == 1
    assert evidence_count == 2


def test_ingest_keeps_changed_same_url_snapshot_unselected(tmp_path: Path) -> None:
    database_path = tmp_path / "information-agent.db"

    def collector(_: str, __: float) -> list[RawFeedEntry]:
        return [
            RawFeedEntry(
                "https://example.com/ai",
                "AI 芯片发布",
                "这是一篇长度超过二十个字符并与 AI 芯片主题相关的第一版文章正文。",
            ),
            RawFeedEntry(
                "https://example.com/ai",
                "AI 芯片发布",
                "这是一篇长度超过二十个字符并与 AI 芯片主题相关的第二版文章正文。",
            ),
        ]

    result = ingest("AI 芯片", ["feed"], database_path=database_path, collector=collector)

    with sqlite3.connect(database_path) as connection:
        evidence_rows = connection.execute(
            "SELECT selected, evidence_no FROM run_evidence ORDER BY selected DESC"
        ).fetchall()

    assert result.report.status is RunStatus.COMPLETED
    assert evidence_rows == [(1, 1), (0, None)]


def test_ingest_preserves_partial_collection_errors(tmp_path: Path) -> None:
    database_path = tmp_path / "information-agent.db"

    def collector(feed: str, _: float) -> list[RawFeedEntry]:
        if feed == "broken":
            raise RuntimeError("连接失败")
        return [
            RawFeedEntry(
                "https://example.com/ai",
                "AI 芯片发布",
                "这是一篇长度超过二十个字符并与 AI 芯片主题相关的文章正文。",
            )
        ]

    result = ingest(
        "AI 芯片",
        ["working", "broken"],
        database_path=database_path,
        collector=collector,
        max_attempts=1,
    )

    with sqlite3.connect(database_path) as connection:
        status, errors_json = connection.execute(
            "SELECT status, errors_json FROM research_runs WHERE id = ?", (result.run_id,)
        ).fetchone()

    assert result.report.status is RunStatus.PARTIAL
    assert status == "partial"
    assert json.loads(errors_json) == result.report.errors


def test_ingest_uses_feed_cache_and_skips_seen_entry_ids(monkeypatch, tmp_path: Path) -> None:
    database_path = tmp_path / "information-agent.db"
    feed_url = "https://example.com/rss.xml"
    entry = RawFeedEntry(
        "https://example.com/ai",
        "AI 芯片发布",
        "这是一篇长度超过二十个字符并与 AI 芯片主题相关的文章正文。",
        feed_url=feed_url,
        entry_id="guid-1",
        updated_at="2026-07-28T10:00:00+08:00",
    )
    responses = [
        FeedFetchResult(feed_url, [entry], '"feed-v1"', "Mon, 28 Jul 2026 02:00:00 GMT"),
        FeedFetchResult(feed_url, [entry], '"feed-v1"', "Mon, 28 Jul 2026 02:00:00 GMT"),
    ]
    requests: list[tuple[str | None, str | None]] = []

    def fake_fetch(url: str, timeout: float, *, etag: str | None, last_modified: str | None):
        assert url == feed_url
        assert timeout > 0
        requests.append((etag, last_modified))
        return responses.pop(0)

    monkeypatch.setattr(
        "information_agent.orchestration.ingestion.fetch_feed_with_cache",
        fake_fetch,
    )

    first = ingest("AI 芯片", [feed_url], database_path=database_path)
    second = ingest("AI 芯片", [feed_url], database_path=database_path)

    with sqlite3.connect(database_path) as connection:
        snapshot_count = connection.execute("SELECT COUNT(*) FROM article_snapshots").fetchone()[0]
        entry_count = connection.execute("SELECT COUNT(*) FROM feed_entries").fetchone()[0]
        feed = connection.execute("SELECT etag, last_modified FROM feeds").fetchone()

    assert first.report.status is RunStatus.COMPLETED
    assert len(first.report.articles) == 1
    assert second.report.status is RunStatus.COMPLETED
    assert second.report.articles == []
    assert requests == [
        (None, None),
        ('"feed-v1"', "Mon, 28 Jul 2026 02:00:00 GMT"),
    ]
    assert snapshot_count == 1
    assert entry_count == 1
    assert feed == ('"feed-v1"', "Mon, 28 Jul 2026 02:00:00 GMT")


def test_ingest_reprocesses_an_entry_when_its_update_marker_changes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "information-agent.db"
    feed_url = "https://example.com/rss.xml"
    first_entry = RawFeedEntry(
        "https://example.com/ai",
        "AI 芯片发布",
        "这是一篇长度超过二十个字符并与 AI 芯片主题相关的第一版文章正文。",
        feed_url=feed_url,
        entry_id="guid-1",
        updated_at="2026-07-28T10:00:00+08:00",
    )
    updated_entry = RawFeedEntry(
        "https://example.com/ai",
        "AI 芯片发布",
        "这是一篇长度超过二十个字符并与 AI 芯片主题相关的第二版文章正文。",
        feed_url=feed_url,
        entry_id="guid-1",
        updated_at="2026-07-28T11:00:00+08:00",
    )
    responses = [
        FeedFetchResult(feed_url, [first_entry], '"feed-v1"', None),
        FeedFetchResult(feed_url, [updated_entry], '"feed-v2"', None),
    ]

    def fake_fetch(url: str, timeout: float, *, etag: str | None, last_modified: str | None):
        assert url == feed_url
        assert timeout > 0
        return responses.pop(0)

    monkeypatch.setattr(
        "information_agent.orchestration.ingestion.fetch_feed_with_cache",
        fake_fetch,
    )

    first = ingest("AI 芯片", [feed_url], database_path=database_path)
    second = ingest("AI 芯片", [feed_url], database_path=database_path)

    with sqlite3.connect(database_path) as connection:
        snapshot_count = connection.execute("SELECT COUNT(*) FROM article_snapshots").fetchone()[0]
        updated_marker = connection.execute("SELECT updated_marker FROM feed_entries").fetchone()[0]

    assert len(first.report.articles) == 1
    assert len(second.report.articles) == 1
    assert snapshot_count == 2
    assert updated_marker == "2026-07-28T11:00:00+08:00"


def test_ingest_cli_loads_llm_configuration(monkeypatch, capsys, tmp_path: Path) -> None:
    result = PersistedCollection(
        run_id="run-123",
        report=CollectionReport("AI", RunStatus.COMPLETED, []),
    )

    def fake_ingest(*args, **kwargs) -> PersistedCollection:
        return result

    loaded: list[bool] = []

    def record_load_dotenv() -> None:
        loaded.append(True)

    monkeypatch.setattr("information_agent.orchestration.ingestion.ingest", fake_ingest)
    monkeypatch.setattr("information_agent.cli.load_dotenv", record_load_dotenv)
    monkeypatch.setenv("INFORMATION_AGENT_DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setattr(sys, "argv", ["information-agent", "ingest", "AI", "feed"])

    main()
    payload = json.loads(capsys.readouterr().out)

    assert loaded == [True]
    assert payload == {
        "run_id": "run-123",
        "topic": "AI",
        "status": "completed",
        "articles": [],
        "errors": [],
    }
