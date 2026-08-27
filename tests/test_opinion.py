from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from information_agent.api import create_app
from information_agent.collection import FeedFetchResult, RawFeedEntry
from information_agent.contracts import CollectionReport, RunStatus, project_now
from information_agent.investigation import (
    OpinionPlan,
    PlanningResult,
    SearchQuery,
    parse_opinion_plans,
)
from information_agent.normalization import normalize_evidence
from information_agent.opinion import (
    BilibiliComment,
    BilibiliCommentCollector,
    Classification,
    ClassificationStatus,
    OpinionAnalysisService,
    OpinionPoint,
    OpinionSnapshotMismatchError,
    OpinionStatus,
    aggregate_opinion_points,
    parse_bilibili_target,
    parse_comment_analysis,
)
from information_agent.opinion import llm as opinion_llm
from information_agent.opinion.llm import LLMOpinionAnalyzer
from information_agent.opinion.parsing import parse_persisted_opinion_report
from information_agent.orchestration.database_planning import plan_run
from information_agent.reader import ReaderService
from information_agent.selection import SelectedEvidence
from information_agent.storage import SQLiteCollectionStore


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_parse_bilibili_target_supports_video_and_article_urls() -> None:
    video = parse_bilibili_target("https://www.bilibili.com/video/BV1xx")
    article = parse_bilibili_target("https://www.bilibili.com/read/cv12345")

    assert video.comment_type == 1
    assert video.bvid == "BV1xx"
    assert article.comment_type == 12
    assert article.oid == 12345


def test_bilibili_comment_collector_resolves_bvid_and_filters_window() -> None:
    now = project_now()
    calls: list[str] = []

    def request_json(url: str, _timeout: float, _referer: str) -> object:
        calls.append(url)
        if "web-interface/view" in url:
            return {"code": 0, "data": {"aid": 456}}
        return {
            "code": 0,
            "data": {
                "replies": [
                    {
                        "rpid": 1,
                        "content": {"message": "这条评论仍在窗口内"},
                        "member": {"uname": "用户甲"},
                        "ctime": int((now - timedelta(hours=1)).timestamp()),
                        "like": 3,
                    },
                    {
                        "rpid": 2,
                        "content": {"message": "这条评论过旧"},
                        "member": {"uname": "用户乙"},
                        "ctime": int((now - timedelta(hours=80)).timestamp()),
                        "like": 1,
                    },
                ]
            },
        }

    comments = BilibiliCommentCollector(request_json=request_json).collect(
        "https://www.bilibili.com/video/BV1xx",
        window_hours=72,
        limit=10,
        timeout=10,
    )

    assert [comment.comment_id for comment in comments] == ["1"]
    assert len(calls) == 2
    assert "oid=456" in calls[1]


def test_bilibili_comment_collector_retries_only_with_remaining_deadline() -> None:
    clock = FakeClock()
    timeouts: list[float] = []

    def request_json(_url: str, timeout: float, _referer: str) -> object:
        timeouts.append(timeout)
        clock.advance(min(0.4, timeout))
        raise TimeoutError("temporary timeout")

    collector = BilibiliCommentCollector(
        request_json=request_json,
        clock=clock,
        sleep=clock.advance,
        max_attempts=5,
    )

    with pytest.raises(TimeoutError):
        collector.collect(
            "https://www.bilibili.com/read/cv12345",
            limit=10,
            timeout=1.0,
        )

    assert len(timeouts) == 3
    assert timeouts[0] == 1.0
    assert 0 < timeouts[1] < timeouts[0]
    assert 0 < timeouts[2] < timeouts[1]
    assert clock.now <= 1.0
    assert [item.attempt for item in collector.last_attempts] == [1, 2, 3]


def test_llm_retries_recalculate_the_shared_deadline(monkeypatch) -> None:
    article = normalize_evidence(
        [
            RawFeedEntry(
                "https://www.bilibili.com/video/BV1xx",
                "一篇有争议的文章",
                "厂商称效果提升 70%，但没有说明完整测试条件。",
            )
        ]
    )[0]
    valid_payload = (
        '{"opinion_plans":[{"evidence_id":1,"trigger_quote":"效果提升 70%",'
        '"question":"效果提升的比较基线是否清楚？",'
        '"queries":[{"query":"效果提升 70%","purpose":"寻找相关讨论"}]}]}'
    )
    responses = iter(["{}", valid_payload])
    clock = FakeClock()
    timeouts: list[float] = []

    def fake_request_json_completion(**kwargs: object) -> str:
        timeouts.append(float(kwargs["timeout"]))
        clock.advance(1.0)
        return next(responses)

    monkeypatch.setattr(opinion_llm, "request_json_completion", fake_request_json_completion)
    analyzer = LLMOpinionAnalyzer(client=object(), clock=clock, max_attempts=2)

    plans = analyzer.detect_controversies(article, timeout=5.0)

    assert len(plans) == 1
    assert timeouts == [5.0, 4.0]
    assert [item.outcome for item in analyzer.last_attempts] == ["failed", "succeeded"]


def test_opinion_planning_prompt_prevents_invalid_query_shape_and_evidence_id(
    monkeypatch,
) -> None:
    article = normalize_evidence(
        [
            RawFeedEntry(
                "https://www.bilibili.com/video/BV1xx",
                "一篇有争议的文章",
                "厂商称效果提升 70%，但没有说明完整测试条件。",
            )
        ]
    )[0]
    invalid_responses = iter(
        [
            (
                '{"opinion_plans":[{"evidence_id":"1",'
                '"trigger_quote":"效果提升 70%","question":"效果提升的比较基线是否清楚？",'
                '"queries":["效果提升 70%","测试条件","独立评测"]}]}'
            ),
            (
                '{"opinion_plans":[{"evidence_id":"1-1",'
                '"trigger_quote":"效果提升 70%","question":"效果提升的比较基线是否清楚？",'
                '"queries":["效果提升 70%","测试条件"]}]}'
            ),
        ]
    )
    valid_response = (
        '{"opinion_plans":[{"evidence_id":1,"trigger_quote":"效果提升 70%",'
        '"question":"效果提升的比较基线是否清楚？",'
        '"queries":[{"query":"效果提升 70%","purpose":"寻找相关讨论"}]}]}'
    )
    prompts: list[str] = []

    def fake_request_json_completion(**kwargs: object) -> str:
        system_prompt = str(kwargs["messages"][0]["content"])
        prompts.append(system_prompt)
        if "evidence_id 只能是 JSON 整数 1" in system_prompt:
            assert "queries 必须是对象数组" in system_prompt
            return valid_response
        return next(invalid_responses)

    monkeypatch.setattr(opinion_llm, "request_json_completion", fake_request_json_completion)
    analyzer = LLMOpinionAnalyzer(client=object(), max_attempts=2)

    plans = analyzer.detect_controversies(article, timeout=10)

    assert len(plans) == 1
    assert len(plans[0].queries) == 1
    assert len(prompts) == 1


def test_opinion_run_creation_is_atomic_under_concurrency(tmp_path: Path) -> None:
    reader = _reader_service(tmp_path / "opinion-concurrent.db")
    reader_article = reader.list_articles()[0]
    article = reader_article.article
    store = reader.store

    def start() -> str:
        return store.start_opinion_run(
            article.article_id,
            article_snapshot_id=reader_article.snapshot_id,
            content_hash=reader_article.content_hash,
            requested_limit=10,
        ).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        run_ids = list(executor.map(lambda _: start(), range(2)))

    assert run_ids[0] == run_ids[1]
    with sqlite3.connect(store.database_path) as connection:
        active_count = connection.execute(
            "SELECT COUNT(*) FROM opinion_runs WHERE article_id = ? AND status = 'running'",
            (article.article_id,),
        ).fetchone()[0]
    assert active_count == 1


def test_stale_opinion_run_is_closed_before_a_new_run_is_created(tmp_path: Path) -> None:
    reader = _reader_service(tmp_path / "opinion-stale.db")
    reader_article = reader.list_articles()[0]
    article = reader_article.article
    store = reader.store
    old_run = store.start_opinion_run(
        article.article_id,
        article_snapshot_id=reader_article.snapshot_id,
        content_hash=reader_article.content_hash,
        requested_limit=10,
        timeout_seconds=1,
    )

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE opinion_runs SET last_heartbeat_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+08:00", old_run.id),
        )
        connection.commit()

    new_run = store.start_opinion_run(
        article.article_id,
        article_snapshot_id=reader_article.snapshot_id,
        content_hash=reader_article.content_hash,
        requested_limit=10,
        timeout_seconds=1,
    )

    assert new_run.id != old_run.id
    with sqlite3.connect(store.database_path) as connection:
        stale_status, errors = connection.execute(
            "SELECT status, errors_json FROM opinion_runs WHERE id = ?",
            (old_run.id,),
        ).fetchone()
    assert stale_status == "failed"
    assert "stale_running" in errors


def test_concurrent_service_request_reuses_the_active_run(tmp_path: Path) -> None:
    reader = _reader_service(tmp_path / "opinion-service-concurrent.db")
    article = reader.list_articles()[0].article
    analyzer = BlockingOpinionAnalyzer()
    collector = FakeCommentCollector()
    first_service = OpinionAnalysisService(
        store=reader.store,
        analyzer=analyzer,
        collector=collector,
        comment_limit=10,
    )
    second_service = OpinionAnalysisService(
        store=reader.store,
        analyzer=analyzer,
        collector=collector,
        comment_limit=10,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_future = executor.submit(first_service.request, article.article_id)
        assert analyzer.entered.wait(timeout=2)
        active = second_service.request(article.article_id, force_refresh=True)
        analyzer.release.set()
        completed = first_future.result(timeout=2)

    assert active.status is OpinionStatus.RUNNING
    assert active.run_id == completed.run_id
    assert analyzer.detect_calls == 1
    assert collector.calls == 1


def test_llm_opinion_analyzer_parses_controversy_and_comment_result(
    monkeypatch,
) -> None:
    article = normalize_evidence(
        [
            RawFeedEntry(
                "https://www.bilibili.com/video/BV1xx",
                "一篇有争议的文章",
                "厂商称效果提升 70%，但没有说明完整测试条件。",
            )
        ]
    )[0]
    plan_payload = (
        '{"opinion_plans":[{"evidence_id":1,"trigger_quote":"效果提升 70%",'
        '"question":"效果提升的比较基线是否清楚？",'
        '"queries":[{"query":"效果提升 70%","purpose":"寻找相关讨论"}]}]}'
    )
    comment = BilibiliComment(
        comment_id="reply-1",
        source_url="https://www.bilibili.com/video/BV1xx#reply-1",
        author="用户甲",
        content="需要先说明比较基线。",
        likes=2,
        published_at=project_now(),
    )
    analysis_payload = (
        '{"summary":"讨论集中在测试条件。","classifications":[{"run_id":"run-1",'
        '"evidence_id":1,"comment_id":"reply-1","classification_status":"classified",'
        '"stance":"oppose","error_code":null}],"points":[{"evidence_id":1,'
        '"summary":"评论质疑比较基线。","representative_comment_ids":["reply-1"]}],'
        '"uncertainties":[]}'
    )
    responses = iter([plan_payload, analysis_payload])

    def fake_request_json_completion(**_: object) -> str:
        return next(responses)

    monkeypatch.setattr(opinion_llm, "request_json_completion", fake_request_json_completion)
    analyzer = object.__new__(LLMOpinionAnalyzer)
    analyzer.client = object()

    plans = analyzer.detect_controversies(article, timeout=10)
    summary, points, uncertainties = analyzer.analyze_comments(
        article, plans, [comment], timeout=10
    )

    assert parse_opinion_plans(plan_payload, [SelectedEvidence(article, 1)]) == plans
    assert plans[0].platform == "bilibili"
    assert summary == "讨论集中在测试条件。"
    assert points[0].stance_counts["oppose"] == 1
    assert uncertainties == []


class FakeOpinionAnalyzer:
    def __init__(self) -> None:
        self.detect_calls = 0
        self.comment_calls = 0

    def detect_controversies(self, article, timeout: float) -> list[OpinionPlan]:
        assert timeout > 0
        self.detect_calls += 1
        return [
            OpinionPlan(
                evidence_id=1,
                trigger_quote="厂商称效果提升 70%",
                question="效果提升的比较基线是否清楚？",
                queries=(SearchQuery("效果提升 70%", "寻找相关讨论"),),
            )
        ]

    def analyze_comments(
        self,
        article,
        controversy_points,
        comments,
        timeout: float,
        *,
        run_id: str | None = None,
    ):
        assert timeout > 0
        assert run_id
        self.comment_calls += 1
        self.last_classifications = [
            Classification(
                run_id=run_id,
                evidence_id=1,
                comment_id=comments[0].comment_id,
                classification_status="classified",
                stance="support",
            ),
            Classification(
                run_id=run_id,
                evidence_id=1,
                comment_id=comments[1].comment_id,
                classification_status="classified",
                stance="oppose",
            ),
        ]
        return (
            "评论主要围绕比较基线是否透明展开。",
            [
                OpinionPoint(
                    evidence_id=1,
                    question=controversy_points[0].question,
                    summary="支持和质疑意见都集中在测试条件是否公开。",
                    stance_counts={"support": 1, "oppose": 1, "mixed": 0, "unclear": 0},
                    representative_comment_ids=(comments[0].comment_id,),
                )
            ],
            ["评论样本不代表全部观众。"],
        )


class EmptyClassificationAnalyzer(FakeOpinionAnalyzer):
    def analyze_comments(
        self,
        article,
        controversy_points,
        comments,
        timeout: float,
        *,
        run_id: str | None = None,
    ):
        assert run_id
        self.comment_calls += 1
        self.last_classifications = []
        return "模型摘要", [], []


class PlanningFailureAnalyzer:
    def detect_controversies(self, article, timeout: float):
        raise RuntimeError("规划器连接断开")

    def analyze_comments(self, *args, **kwargs):
        raise AssertionError("规划失败后不应进入评论分析")


class BlockingOpinionAnalyzer(FakeOpinionAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def detect_controversies(self, article, timeout: float) -> list[OpinionPlan]:
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise AssertionError("测试分析器未被释放")
        return super().detect_controversies(article, timeout)


class FakeCommentCollector:
    def __init__(self) -> None:
        self.calls = 0

    def collect(self, source_url, *, window_hours, limit, timeout):
        assert window_hours == 72
        assert limit == 10
        assert timeout > 0
        self.calls += 1
        return [
            BilibiliComment(
                comment_id="reply-1",
                source_url=f"{source_url}#reply-1",
                author="用户甲",
                content="需要先说明比较基线。",
                likes=2,
                published_at=project_now(),
            ),
            BilibiliComment(
                comment_id="reply-2",
                source_url=f"{source_url}#reply-2",
                author="用户乙",
                content="我认为这个结果有参考价值。",
                likes=1,
                published_at=project_now(),
            ),
        ]


class NoControversyAnalyzer:
    def __init__(self) -> None:
        self.detect_calls = 0

    def detect_controversies(self, article, timeout: float) -> list[OpinionPlan]:
        assert timeout > 0
        self.detect_calls += 1
        return []

    def analyze_comments(self, *args, **kwargs):
        raise AssertionError("没有争议点时不应进入评论分析")


class EmptyCommentCollector:
    def __init__(self) -> None:
        self.calls = 0

    def collect(self, source_url, *, window_hours, limit, timeout):
        assert window_hours == 72
        assert limit == 10
        assert timeout > 0
        self.calls += 1
        return []


class PartialClassificationAnalyzer(FakeOpinionAnalyzer):
    def analyze_comments(
        self,
        article,
        controversy_points,
        comments,
        timeout: float,
        *,
        run_id: str | None = None,
    ):
        assert run_id
        assert timeout > 0
        self.comment_calls += 1
        self.last_classifications = [
            Classification(
                run_id=run_id,
                evidence_id=1,
                comment_id=comments[0].comment_id,
                classification_status="classified",
                stance="support",
            ),
            Classification(
                run_id=run_id,
                evidence_id=1,
                comment_id=comments[1].comment_id,
                classification_status="unclassified",
                error_code="classification_failed",
            ),
        ]
        points = aggregate_opinion_points(
            controversy_points,
            self.last_classifications,
            point_summaries={1: "评论集中讨论比较条件。"},
        )
        return "仅描述当前评论样本。", list(points), ["评论样本不代表总体民意。"]


class CommentCollectionFailure(RuntimeError):
    code = "comment_collection_failed"
    stage = "comment_collection"


class FailingCommentCollector:
    def collect(self, source_url, *, window_hours, limit, timeout):
        raise CommentCollectionFailure("评论接口暂时不可用")


def _reader_service(database_path: Path) -> ReaderService:
    def fetcher(feed_url: str, _timeout: float, **_: object) -> FeedFetchResult:
        return FeedFetchResult(
            feed_url=feed_url,
            etag=None,
            last_modified=None,
            entries=[
                RawFeedEntry(
                    source_url="https://www.bilibili.com/video/BV1xx",
                    title="一篇有争议的文章",
                    content="厂商称效果提升 70%，但没有说明完整测试条件。",
                    feed_url=feed_url,
                    published_at=project_now(),
                )
            ],
        )

    service = ReaderService(database_path, fetcher=fetcher)
    service.subscribe("https://example.com/feed.xml")
    return service


def test_opinion_analysis_is_explicit_and_reuses_completed_result(tmp_path: Path) -> None:
    reader = _reader_service(tmp_path / "opinion.db")
    article = reader.list_articles()[0].article
    analyzer = FakeOpinionAnalyzer()
    collector = FakeCommentCollector()
    service = OpinionAnalysisService(
        store=reader.store,
        analyzer=analyzer,
        collector=collector,
        comment_limit=10,
    )
    client = TestClient(create_app(reader, opinion_service=service))

    before = service.get_status(article.article_id)
    assert before.status is OpinionStatus.NOT_REQUESTED
    assert analyzer.detect_calls == 0
    assert collector.calls == 0

    api_before = client.get(f"/api/articles/{article.article_id}/opinion")
    assert api_before.status_code == 200
    assert api_before.json()["status"] == "not_requested"

    api_run = client.post(f"/api/articles/{article.article_id}/opinion")
    assert api_run.status_code == 200
    assert api_run.json()["status"] == "completed"

    first = service.get_status(article.article_id)
    assert first.status is OpinionStatus.COMPLETED
    assert first.summary.startswith("评论主要")
    assert len(first.comments) == 2
    assert analyzer.detect_calls == 1
    assert analyzer.comment_calls == 1
    assert collector.calls == 1

    second = service.request(article.article_id)
    assert second.run_id == first.run_id
    assert analyzer.detect_calls == 1
    assert analyzer.comment_calls == 1
    assert collector.calls == 1

    status_response = client.get(f"/api/articles/{article.article_id}/opinion")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"


def test_opinion_request_completes_without_collecting_when_no_controversy_points(
    tmp_path: Path,
) -> None:
    reader = _reader_service(tmp_path / "opinion-no-controversy.db")
    article = reader.list_articles()[0].article
    analyzer = NoControversyAnalyzer()
    collector = FakeCommentCollector()
    service = OpinionAnalysisService(
        store=reader.store,
        analyzer=analyzer,
        collector=collector,
        comment_limit=10,
    )

    report = service.request(article.article_id)

    assert report.status is OpinionStatus.COMPLETED
    assert report.status_reason == "no_controversy_points"
    assert report.controversy_points == ()
    assert report.comments == ()
    assert report.points == ()
    assert report.collected_count == 0
    assert report.analyzed_count == 0
    assert report.classification_total == 0
    assert analyzer.detect_calls == 1
    assert collector.calls == 0


def test_opinion_request_returns_completed_empty_sample_without_analysis(tmp_path: Path) -> None:
    reader = _reader_service(tmp_path / "opinion-empty-sample.db")
    article = reader.list_articles()[0].article
    analyzer = FakeOpinionAnalyzer()
    collector = EmptyCommentCollector()
    service = OpinionAnalysisService(
        store=reader.store,
        analyzer=analyzer,
        collector=collector,
        comment_limit=10,
    )

    report = service.request(article.article_id)

    assert report.status is OpinionStatus.COMPLETED
    assert report.status_reason == "sample_empty"
    assert report.collected_count == 0
    assert report.analyzed_count == 0
    assert report.classification_total == 0
    assert report.points == ()
    assert "总体民意" in report.uncertainties[0]
    assert analyzer.comment_calls == 0
    assert collector.calls == 1


def test_opinion_request_persists_partial_classification_counts_from_relationships(
    tmp_path: Path,
) -> None:
    reader = _reader_service(tmp_path / "opinion-partial-classification.db")
    article = reader.list_articles()[0].article
    analyzer = PartialClassificationAnalyzer()
    service = OpinionAnalysisService(
        store=reader.store,
        analyzer=analyzer,
        collector=FakeCommentCollector(),
        comment_limit=10,
    )
    response = TestClient(create_app(reader, opinion_service=service)).post(
        f"/api/articles/{article.article_id}/opinion"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "partial"
    assert payload["status_reason"] == "partial_classification"
    assert payload["collected_count"] == 2
    assert payload["analyzed_count"] == 2
    assert payload["classification_total"] == 2
    assert payload["classified_count"] == 1
    assert payload["unclassified_count"] == 1
    assert payload["points"][0]["stance_counts"] == {
        "support": 1,
        "oppose": 0,
        "mixed": 0,
        "unclear": 0,
    }
    assert {item["classification_status"] for item in payload["classifications"]} == {
        "classified",
        "unclassified",
    }


def test_opinion_request_persists_failed_collection_with_error_context(tmp_path: Path) -> None:
    reader = _reader_service(tmp_path / "opinion-failed.db")
    article = reader.list_articles()[0].article
    service = OpinionAnalysisService(
        store=reader.store,
        analyzer=FakeOpinionAnalyzer(),
        collector=FailingCommentCollector(),
        comment_limit=10,
    )

    response = TestClient(create_app(reader, opinion_service=service)).post(
        f"/api/articles/{article.article_id}/opinion"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["status_reason"] == "failed"
    assert payload["collected_count"] == 0
    assert payload["classification_total"] == 0
    assert payload["errors"][0]["code"] == "comment_collection_failed"
    assert payload["errors"][0]["stage"] == "comment_collection"
    assert any(item["stage"] == "comment_collection" for item in payload["attempts"])


def test_empty_external_classification_is_not_saved_as_completed(tmp_path: Path) -> None:
    reader = _reader_service(tmp_path / "opinion-empty-analysis.db")
    article = reader.list_articles()[0].article
    service = OpinionAnalysisService(
        store=reader.store,
        analyzer=EmptyClassificationAnalyzer(),
        collector=FakeCommentCollector(),
        comment_limit=10,
    )

    report = service.request(article.article_id)

    assert report.status is OpinionStatus.PARTIAL
    assert report.status_reason == "partial_classification"
    assert report.summary == "模型摘要"
    assert report.errors[0].code == "analysis_response_invalid"
    assert report.errors[0].stage == "opinion_analysis"


def test_unknown_planning_error_keeps_the_planning_stage(tmp_path: Path) -> None:
    reader = _reader_service(tmp_path / "opinion-planning-error.db")
    article = reader.list_articles()[0].article
    service = OpinionAnalysisService(
        store=reader.store,
        analyzer=PlanningFailureAnalyzer(),
        collector=FakeCommentCollector(),
        comment_limit=10,
    )

    report = service.request(article.article_id)

    assert report.status is OpinionStatus.FAILED
    assert report.errors[0].stage == "opinion_planning"
    assert "规划器连接断开" in report.errors[0].message


def test_completed_opinion_run_requires_a_summary(tmp_path: Path) -> None:
    reader = _reader_service(tmp_path / "opinion-missing-summary.db")
    reader_article = reader.list_articles()[0]
    article = reader_article.article
    run = reader.store.start_opinion_run(
        article.article_id,
        article_snapshot_id=reader_article.snapshot_id,
        content_hash=reader_article.content_hash,
        requested_limit=10,
    )

    with pytest.raises(ValueError, match="summary"):
        reader.store.complete_opinion_run(
            run.id,
            status="completed",
            result_payload={
                "article_id": article.article_id,
                "article_snapshot_id": reader_article.snapshot_id,
                "content_hash": reader_article.content_hash,
                "source_url": article.source_url,
                "requested_limit": 10,
                "status_reason": "completed",
            },
            comments=[],
        )

    active = reader.store.get_latest_opinion_run(article.article_id)
    assert active is not None
    assert active.status == "running"


def test_persisted_points_without_relationships_do_not_supply_stance_counts() -> None:
    plan, comments = _classification_context()
    report = parse_persisted_opinion_report(
        {
            "article_id": "article-1",
            "article_snapshot_id": "snapshot-1",
            "content_hash": "hash-1",
            "source_url": "https://www.bilibili.com/video/BV1xx",
            "status": "partial",
            "status_reason": "partial_classification",
            "requested_limit": 10,
            "collected_count": len(comments),
            "analyzed_count": len(comments),
            "classification_total": 0,
            "classified_count": 0,
            "unclassified_count": 0,
            "controversy_points": [
                {
                    "evidence_id": plan.evidence_id,
                    "trigger_quote": plan.trigger_quote,
                    "question": plan.question,
                    "platform": plan.platform,
                    "window_hours": plan.window_hours,
                    "queries": [
                        {"query": query.query, "purpose": query.purpose} for query in plan.queries
                    ],
                }
            ],
            "comments": [
                {
                    "comment_id": item.comment_id,
                    "source_url": item.source_url,
                    "author": item.author,
                    "content": item.content,
                    "likes": item.likes,
                    "published_at": None,
                }
                for item in comments
            ],
            "classifications": [],
            "points": [
                {
                    "evidence_id": 1,
                    "question": plan.question,
                    "summary": "不应直接使用模型数字。",
                    "stance_counts": {
                        "support": 99,
                        "oppose": 0,
                        "mixed": 0,
                        "unclear": 0,
                    },
                    "representative_comment_ids": ["reply-1"],
                }
            ],
            "summary": "仅描述样本。",
            "uncertainties": ["样本不代表总体民意。"],
            "errors": [],
            "attempts": [],
        }
    )

    assert report.points == ()


def test_latest_empty_planning_result_supersedes_older_opinion_plan(tmp_path: Path) -> None:
    article = normalize_evidence(
        [
            RawFeedEntry(
                "https://www.bilibili.com/video/BV1xx",
                "一篇文章",
                "厂商称效果提升 70%，但没有说明完整测试条件。",
            )
        ]
    )[0]
    store = SQLiteCollectionStore(tmp_path / "planning.db")
    run_id = store.start_run("AI", ["feed"])
    store.complete_run(
        run_id,
        CollectionReport("AI", RunStatus.COMPLETED, [SelectedEvidence(article, 1)]),
        [article],
    )
    opinion_plan = OpinionPlan(
        evidence_id=1,
        trigger_quote="效果提升 70%",
        question="效果提升的比较基线是否清楚？",
        queries=(SearchQuery("效果提升 70%", "寻找相关讨论"),),
    )

    class FixedPlanner:
        def __init__(self, plans: list[OpinionPlan]) -> None:
            self.plans = plans

        def plan_with_result(self, topic, evidence, timeout):
            return PlanningResult('{"plans": [], "opinion_plans": []}', [], self.plans)

    plan_run(run_id, database_path=store.database_path, planner=FixedPlanner([opinion_plan]))
    plan_run(run_id, database_path=store.database_path, planner=FixedPlanner([]))

    assert store.load_opinion_plans_for_article(article.article_id) == []


def test_opinion_plans_for_one_article_do_not_include_sibling_articles(tmp_path: Path) -> None:
    articles = normalize_evidence(
        [
            RawFeedEntry(
                "https://www.bilibili.com/video/BV1xx",
                "文章一",
                "文章一的争议内容需要核查，本文还提供了背景说明。",
            ),
            RawFeedEntry(
                "https://www.bilibili.com/video/BV2xx",
                "文章二",
                "文章二的争议内容需要核查，本文还提供了背景说明。",
            ),
        ]
    )
    store = SQLiteCollectionStore(tmp_path / "opinion-plan-isolation.db")
    run_id = store.start_run("主题", ["feed"])
    store.complete_run(
        run_id,
        CollectionReport(
            "主题",
            RunStatus.COMPLETED,
            [SelectedEvidence(articles[0], 1), SelectedEvidence(articles[1], 2)],
        ),
        articles,
    )
    plans = [
        OpinionPlan(
            evidence_id=1,
            trigger_quote="文章一的争议内容",
            question="文章一的问题？",
            queries=(SearchQuery("文章一", "查看讨论"),),
        ),
        OpinionPlan(
            evidence_id=2,
            trigger_quote="文章二的争议内容",
            question="文章二的问题？",
            queries=(SearchQuery("文章二", "查看讨论"),),
        ),
    ]

    class FixedPlanner:
        def plan_with_result(self, topic, evidence, timeout):
            return PlanningResult('{"plans": [], "opinion_plans": []}', [], plans)

    plan_run(run_id, database_path=store.database_path, planner=FixedPlanner())

    loaded = store.load_opinion_plans_for_article(articles[0].article_id)
    assert [item.evidence_id for item in loaded] == [1]
    assert loaded[0].question == "文章一的问题？"


def _classification_context() -> tuple[OpinionPlan, list[BilibiliComment]]:
    plan = OpinionPlan(
        evidence_id=1,
        trigger_quote="文章争议锚点",
        question="这个问题如何判断？",
        queries=(SearchQuery("文章争议", "寻找公开讨论"),),
    )
    comments = [
        BilibiliComment("reply-1", "https://example.com#reply-1", "用户甲", "支持", 1, None),
        BilibiliComment("reply-2", "https://example.com#reply-2", "用户乙", "无法判断", 0, None),
    ]
    return plan, comments


def test_parse_comment_analysis_normalizes_classifications_and_unclassified() -> None:
    plan, comments = _classification_context()
    payload = {
        "summary": "评论讨论集中在证据条件。",
        "classifications": [
            {
                "run_id": "run-1",
                "evidence_id": 1,
                "comment_id": "reply-1",
                "classification_status": "classified",
                "stance": "support",
                "error_code": None,
            },
            {
                "run_id": "run-1",
                "evidence_id": 1,
                "comment_id": "reply-2",
                "classification_status": "unclassified",
                "stance": None,
                "error_code": "classification_failed",
            },
        ],
        "points": [
            {
                "evidence_id": 1,
                "summary": "评论讨论集中在证据条件。",
                "representative_comment_ids": ["reply-1"],
            }
        ],
        "uncertainties": [],
    }

    result = parse_comment_analysis(
        json.dumps(payload, ensure_ascii=False), [plan], comments, run_id="run-1"
    )
    points = aggregate_opinion_points([plan], result.classifications)

    assert len(result.classifications) == 2
    assert result.classifications[1].classification_status is ClassificationStatus.UNCLASSIFIED
    assert points[0].stance_counts == {"support": 1, "oppose": 0, "mixed": 0, "unclear": 0}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("comment_id", "missing"),
        ("evidence_id", 99),
        ("stance", "unknown"),
    ],
)
def test_parse_comment_analysis_rejects_invalid_relationships(field: str, value: object) -> None:
    plan, comments = _classification_context()
    item = {
        "run_id": "run-1",
        "evidence_id": 1,
        "comment_id": "reply-1",
        "classification_status": "classified",
        "stance": "support",
        "error_code": None,
    }
    item[field] = value
    payload = {"summary": "摘要", "classifications": [item], "uncertainties": []}

    with pytest.raises(ValueError):
        parse_comment_analysis(
            json.dumps(payload, ensure_ascii=False), [plan], comments, run_id="run-1"
        )


def test_parse_comment_analysis_rejects_missing_and_duplicate_relationships() -> None:
    plan, comments = _classification_context()
    item = {
        "run_id": "run-1",
        "evidence_id": 1,
        "comment_id": "reply-1",
        "classification_status": "classified",
        "stance": "support",
        "error_code": None,
    }
    with pytest.raises(ValueError):
        parse_comment_analysis(
            json.dumps({"summary": "摘要", "classifications": [item]}),
            [plan],
            comments,
            run_id="run-1",
        )
    with pytest.raises(ValueError):
        parse_comment_analysis(
            json.dumps({"summary": "摘要", "classifications": [item, item], "uncertainties": []}),
            [plan],
            comments,
            run_id="run-1",
        )


def test_parse_comment_analysis_rejects_missing_point_summary() -> None:
    plan, comments = _classification_context()
    item = {
        "run_id": "run-1",
        "evidence_id": 1,
        "comment_id": "reply-1",
        "classification_status": "classified",
        "stance": "support",
        "error_code": None,
    }

    with pytest.raises(ValueError, match="points"):
        parse_comment_analysis(
            json.dumps(
                {"summary": "摘要", "classifications": [item], "points": [], "uncertainties": []}
            ),
            [plan],
            comments,
            run_id="run-1",
        )


def test_request_rejects_a_run_from_an_old_article_snapshot(tmp_path: Path) -> None:
    source_url = "https://www.bilibili.com/video/BV1xx"
    first_article = normalize_evidence(
        [RawFeedEntry(source_url, "标题", "第一版正文包含足够长度的争议内容用于快照测试。")]
    )[0]
    second_article = normalize_evidence(
        [RawFeedEntry(source_url, "标题", "第二版正文已经变化并且同样包含足够长度的内容。")]
    )[0]
    contents = [first_article.content]
    markers = ["v1"]

    def fetcher(feed_url: str, _timeout: float, **_: object) -> FeedFetchResult:
        return FeedFetchResult(
            feed_url=feed_url,
            etag=None,
            last_modified=None,
            entries=[RawFeedEntry(source_url, "标题", contents[0], updated_at=markers[0])],
        )

    reader = ReaderService(tmp_path / "snapshot.db", fetcher=fetcher)
    subscription = reader.subscribe("https://example.com/feed.xml")
    store = reader.store
    first_reader_article = store.get_reader_article(first_article.article_id)
    assert first_reader_article is not None
    old_run = store.start_opinion_run(
        first_article.article_id,
        article_snapshot_id=first_reader_article.snapshot_id,
        content_hash=first_reader_article.content_hash,
        requested_limit=10,
    )
    store.complete_opinion_run(
        old_run.id,
        status="completed",
        result_payload={
            "article_snapshot_id": first_reader_article.snapshot_id,
            "content_hash": first_reader_article.content_hash,
            "requested_limit": 10,
            "summary": "旧版本样本摘要。",
        },
        comments=[],
    )
    contents[0] = second_article.content
    markers[0] = "v2"
    reader.refresh(subscription.feed_id)

    service = OpinionAnalysisService(store=store, comment_limit=10)
    with pytest.raises(OpinionSnapshotMismatchError):
        service.request(first_article.article_id)


def test_opinion_persistence_uses_relationship_rows_for_counts(tmp_path: Path) -> None:
    reader = _reader_service(tmp_path / "opinion-t4-persistence.db")
    reader_article = reader.list_articles()[0]
    article = reader_article.article
    store = reader.store
    run = store.start_opinion_run(
        article.article_id,
        article_snapshot_id=reader_article.snapshot_id,
        content_hash=reader_article.content_hash,
        requested_limit=10,
    )
    comments = [
        {
            "comment_id": "reply-1",
            "source_url": f"{article.source_url}#reply-1",
            "author": "用户甲",
            "content": "支持这个说法。",
            "likes": 2,
            "published_at": None,
        },
        {
            "comment_id": "reply-2",
            "source_url": f"{article.source_url}#reply-2",
            "author": "用户乙",
            "content": "无法判断。",
            "likes": 1,
            "published_at": None,
        },
    ]
    classifications = [
        {
            "run_id": run.id,
            "evidence_id": 1,
            "comment_id": "reply-1",
            "classification_status": "classified",
            "stance": "support",
            "error_code": None,
        },
        {
            "run_id": run.id,
            "evidence_id": 1,
            "comment_id": "reply-2",
            "classification_status": "unclassified",
            "stance": None,
            "error_code": "classification_failed",
        },
    ]
    attempts = [
        {
            "stage": "opinion_analysis",
            "attempt": 1,
            "started_at": "2026-08-17T10:00:00+08:00",
            "finished_at": "2026-08-17T10:00:01+08:00",
            "outcome": "succeeded",
            "error_code": None,
            "error_summary": None,
        }
    ]
    store.complete_opinion_run(
        run.id,
        status="partial",
        result_payload={
            "article_id": article.article_id,
            "article_snapshot_id": reader_article.snapshot_id,
            "content_hash": reader_article.content_hash,
            "source_url": article.source_url,
            "requested_limit": 10,
            "analyzed_count": 2,
            "status": "partial",
            "status_reason": "partial_classification",
            "controversy_points": [
                {
                    "evidence_id": 1,
                    "trigger_quote": "效果提升 70%",
                    "question": "比较基线是否清楚？",
                    "platform": "bilibili",
                    "window_hours": 72,
                    "queries": [{"query": "效果提升", "purpose": "寻找公开讨论"}],
                }
            ],
            "summary": "仅描述当前评论样本。",
            "points": [
                {
                    "evidence_id": 1,
                    "question": "比较基线是否清楚？",
                    "summary": "评论集中讨论比较条件。",
                    "stance_counts": {
                        "support": 99,
                        "oppose": 99,
                        "mixed": 0,
                        "unclear": 0,
                    },
                    "representative_comment_ids": ["reply-1"],
                }
            ],
            "uncertainties": ["样本不代表全部观众。"],
            "errors": [],
        },
        comments=comments,
        classifications=classifications,
        attempts=attempts,
    )

    report = OpinionAnalysisService(store=store, comment_limit=10).get_status(article.article_id)

    assert report.collected_count == 2
    assert report.analyzed_count == 2
    assert report.classification_total == 2
    assert report.classified_count == 1
    assert report.unclassified_count == 1
    assert report.points[0].stance_counts == {
        "support": 1,
        "oppose": 0,
        "mixed": 0,
        "unclear": 0,
    }
    assert len(report.attempts) == 1

    with sqlite3.connect(store.database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(opinion_runs)")}
        assert {
            "article_snapshot_id",
            "content_hash",
            "requested_limit",
            "classification_total",
            "status_reason",
        } <= columns
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM opinion_classifications WHERE run_id = ?", (run.id,)
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM opinion_attempts WHERE run_id = ?", (run.id,)
            ).fetchone()[0]
            == 1
        )


def test_opinion_completion_rolls_back_on_sqlite_conflict(tmp_path: Path) -> None:
    reader = _reader_service(tmp_path / "opinion-t4-rollback.db")
    reader_article = reader.list_articles()[0]
    article = reader_article.article
    store = reader.store
    run = store.start_opinion_run(
        article.article_id,
        article_snapshot_id=reader_article.snapshot_id,
        content_hash=reader_article.content_hash,
        requested_limit=10,
    )
    existing_comment = {
        "comment_id": "reply-existing",
        "source_url": f"{article.source_url}#reply-existing",
        "author": "用户甲",
        "content": "已存在的评论。",
        "likes": 1,
        "published_at": None,
    }
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            INSERT INTO opinion_comments (
                run_id, comment_id, source_url, author, content,
                likes, published_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.id,
                existing_comment["comment_id"],
                existing_comment["source_url"],
                existing_comment["author"],
                existing_comment["content"],
                existing_comment["likes"],
                existing_comment["published_at"],
                json.dumps(existing_comment, ensure_ascii=False),
            ),
        )

    with pytest.raises(sqlite3.IntegrityError):
        store.complete_opinion_run(
            run.id,
            status="completed",
            result_payload={
                "article_snapshot_id": reader_article.snapshot_id,
                "content_hash": reader_article.content_hash,
                "requested_limit": 10,
                "analyzed_count": 0,
                "status_reason": "completed",
                "summary": "完成示例",
            },
            comments=[existing_comment, {**existing_comment, "comment_id": "reply-new"}],
            classifications=[],
            attempts=[],
        )

    with sqlite3.connect(store.database_path) as connection:
        status = connection.execute(
            "SELECT status FROM opinion_runs WHERE id = ?", (run.id,)
        ).fetchone()[0]
        comment_ids = {
            row[0]
            for row in connection.execute(
                "SELECT comment_id FROM opinion_comments WHERE run_id = ?", (run.id,)
            )
        }
    assert status == "running"
    assert comment_ids == {"reply-existing"}


def test_opinion_api_returns_202_for_active_run(tmp_path: Path) -> None:
    reader = _reader_service(tmp_path / "opinion-t4-202.db")
    article = reader.list_articles()[0].article
    analyzer = BlockingOpinionAnalyzer()
    collector = FakeCommentCollector()
    first_service = OpinionAnalysisService(
        store=reader.store,
        analyzer=analyzer,
        collector=collector,
        comment_limit=10,
    )
    second_service = OpinionAnalysisService(
        store=reader.store,
        analyzer=analyzer,
        collector=collector,
        comment_limit=10,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_future = executor.submit(first_service.request, article.article_id)
        assert analyzer.entered.wait(timeout=2)
        response = TestClient(create_app(reader, opinion_service=second_service)).post(
            f"/api/articles/{article.article_id}/opinion",
            json={"force_refresh": True},
        )
        analyzer.release.set()
        first_future.result(timeout=2)

    assert response.status_code == 202
    assert response.json()["status"] == "running"
