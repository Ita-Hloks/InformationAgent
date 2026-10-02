from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from information_agent.api import create_app
from information_agent.collection import FeedFetchResult, RawFeedEntry
from information_agent.contracts import PROJECT_TIMEZONE, ContentType
from information_agent.investigation import OpinionPlan, SearchQuery
from information_agent.opinion import (
    BilibiliVideoCandidate,
    OpinionSnapshotMismatchError,
    ReferenceDiscoveryResult,
    ReferenceDiscoveryService,
    ReferenceDiscoveryStatus,
)
from information_agent.opinion.llm import LLMOpinionAnalyzer
from information_agent.opinion.reference_selection import LLMVideoSelector, VideoSelection
from information_agent.opinion.service import OpinionArticleNotFoundError
from information_agent.reader import ReaderService


def _reader(tmp_path: Path) -> ReaderService:
    def fetcher(feed_url: str, _timeout: float, **_: object) -> FeedFetchResult:
        return FeedFetchResult(
            feed_url=feed_url,
            entries=[
                RawFeedEntry(
                    source_url="https://example.com/article-1",
                    title="文章标题不作为查询词",
                    content="某机构宣布新政策，引发公众争议，文章正文用于生成视频搜索词。",
                    feed_url=feed_url,
                    content_type=ContentType.RSS_CONTENT,
                )
            ],
            etag=None,
            last_modified=None,
        )

    reader = ReaderService(tmp_path / "references.db", fetcher=fetcher)
    reader.subscribe("https://example.com/feed.xml")
    return reader


class FakePlanner:
    def __init__(self, plans: list[OpinionPlan]) -> None:
        self.plans = plans
        self.article = None

    def extract_video_queries(self, article, timeout: float) -> list[OpinionPlan]:
        self.article = article
        assert timeout > 0
        return self.plans


class FakeVideoSearcher:
    def __init__(self, payloads: list[object]) -> None:
        self.payloads = list(payloads)
        self.calls: list[tuple[str, float]] = []

    def search(self, query: str, timeout: float) -> object:
        self.calls.append((query, timeout))
        assert timeout > 0
        payload = self.payloads.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        return payload


class FakeSelector:
    def select(self, article, plans, candidates, timeout):
        assert article.content and plans and timeout > 0
        return tuple(
            VideoSelection(item.video_id, "selected" if index == 0 else "rejected", "主题判断")
            for index, item in enumerate(candidates)
        )


def _plan() -> OpinionPlan:
    return OpinionPlan(
        evidence_id=1,
        trigger_quote="引发公众争议",
        question="该政策是否存在值得参考的公开讨论？",
        queries=(SearchQuery("某机构 新政策 争议", "寻找同一事件的视频讨论"),),
    )


def test_reference_discovery_uses_article_content_and_returns_only_video_candidates(
    tmp_path: Path,
) -> None:
    reader = _reader(tmp_path)
    searcher = FakeVideoSearcher(
        [
            {
                "result": [
                    {
                        "type": "video",
                        "bvid": "BV1abc",
                        "aid": 456,
                        "arcurl": "http://www.bilibili.com/video/av456",
                        "title": '视频甲 <em class="keyword">政策</em>',
                        "description": "视频甲的摘要",
                        "author": "UP主甲",
                        "tag": "时事,观点",
                        "pubdate": 1700000000,
                    },
                    {
                        "type": "video",
                        "bvid": "BV1abc",
                        "aid": 456,
                        "arcurl": "https://www.bilibili.com/video/BV1abc?utm_source=x",
                        "title": "视频甲重复",
                    },
                    {
                        "type": "video",
                        "bvid": "BVcolumn",
                        "aid": 123,
                        "arcurl": "https://www.bilibili.com/read/cv123",
                        "title": "专栏",
                    },
                    {
                        "type": "video",
                        "bvid": "BVexternal",
                        "aid": 789,
                        "arcurl": "https://example.com/video",
                        "title": "站外",
                    },
                    {
                        "type": "video",
                        "bvid": "BV2def",
                        "aid": 999,
                        "arcurl": "https://www.bilibili.com/video/av999",
                        "title": "视频乙",
                        "description": "视频乙摘要",
                        "author": "UP主乙",
                        "tag": "观点",
                        "pubdate": 1700000001,
                    },
                ]
            }
        ]
    )
    planner = FakePlanner([_plan()])

    result = ReferenceDiscoveryService(
        store=reader.store,
        planner=planner,
        video_searcher=searcher,
        selector=FakeSelector(),
    ).discover(reader.list_articles()[0].article.article_id)

    assert planner.article.content == "某机构宣布新政策，引发公众争议，文章正文用于生成视频搜索词。"
    assert [call[0] for call in searcher.calls] == ["某机构 新政策 争议"]
    assert [candidate.video_id for candidate in result.candidates] == ["BV1abc", "BV2def"]
    assert result.status is ReferenceDiscoveryStatus.COMPLETED
    assert result.status_reason == "completed"
    assert result.candidates[0].title == "视频甲 政策"
    assert result.candidates[0].url == "http://www.bilibili.com/video/av456"
    assert result.candidates[0].snippet == "视频甲的摘要"
    assert result.candidates[0].site_name == "UP主甲"
    assert result.candidates[0].author == "UP主甲"
    assert result.candidates[0].tag == "时事,观点"
    assert result.candidates[0].published_at == datetime.fromtimestamp(
        1700000000,
        tz=PROJECT_TIMEZONE,
    ).isoformat(timespec="seconds")
    assert result.candidates[0].search_query == "某机构 新政策 争议"
    assert [item.video_id for item in result.selected_candidates] == ["BV1abc"]
    assert result.selections[1].decision == "rejected"


def test_reference_discovery_returns_empty_success_when_planner_has_no_plan(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    searcher = FakeVideoSearcher([])

    result = ReferenceDiscoveryService(
        store=reader.store,
        planner=FakePlanner([]),
        video_searcher=searcher,
    ).discover(reader.list_articles()[0].article.article_id)

    assert result.status is ReferenceDiscoveryStatus.COMPLETED
    assert result.status_reason == "no_queries"
    assert result.candidates == ()
    assert searcher.calls == []


def test_reference_discovery_preserves_partial_candidates_when_one_search_fails(
    tmp_path: Path,
) -> None:
    reader = _reader(tmp_path)
    plan = _plan()

    searcher = FakeVideoSearcher([RuntimeError("搜索服务不可用")])

    result = ReferenceDiscoveryService(
        store=reader.store,
        planner=FakePlanner([plan]),
        video_searcher=searcher,
    ).discover(reader.list_articles()[0].article.article_id)

    assert result.status is ReferenceDiscoveryStatus.PARTIAL
    assert result.status_reason == "search_failed"
    assert result.candidates == ()
    assert "视频搜索失败" in result.errors[0]


def test_reference_discovery_rejects_malformed_bilibili_response(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    searcher = FakeVideoSearcher([{"unexpected": []}])

    result = ReferenceDiscoveryService(
        store=reader.store,
        planner=FakePlanner([_plan()]),
        video_searcher=searcher,
    ).discover(reader.list_articles()[0].article.article_id)

    assert result.status is ReferenceDiscoveryStatus.PARTIAL
    assert result.status_reason == "search_failed"
    assert result.candidates == ()
    assert "视频搜索失败" in result.errors[0]


def test_reference_discovery_rejects_plan_for_another_article(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    invalid = OpinionPlan(
        evidence_id=2,
        trigger_quote="引发公众争议",
        question="是否有讨论？",
        queries=(SearchQuery("某机构 新政策", "寻找视频"),),
    )

    result = ReferenceDiscoveryService(
        store=reader.store,
        planner=FakePlanner([invalid]),
        video_searcher=FakeVideoSearcher([]),
    ).discover(reader.list_articles()[0].article.article_id)

    assert result.status is ReferenceDiscoveryStatus.PARTIAL
    assert result.status_reason == "planning_failed"
    assert result.candidates == ()


def test_candidate_dataclass_does_not_contain_comments() -> None:
    assert not hasattr(BilibiliVideoCandidate, "comments")


class _ReferenceApiService:
    def __init__(self, result: ReferenceDiscoveryResult | None = None, error=None) -> None:
        self.result = result
        self.error = error
        self.article_ids: list[str] = []

    def discover(self, article_id: str) -> ReferenceDiscoveryResult:
        self.article_ids.append(article_id)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def test_reference_api_returns_queries_and_video_candidates_without_comments(
    tmp_path: Path,
) -> None:
    reader = _reader(tmp_path)
    article = reader.list_articles()[0]
    plan = _plan()
    candidate = BilibiliVideoCandidate(
        video_id="BV1abc",
        bvid="BV1abc",
        url="https://www.bilibili.com/video/BV1abc",
        title="视频甲",
        search_query="某机构 新政策 争议",
    )
    discovery = _ReferenceApiService(
        ReferenceDiscoveryResult(
            article_id=article.article.article_id,
            snapshot_id=article.snapshot_id,
            content_hash=article.content_hash,
            plans=(plan,),
            candidates=(candidate,),
            status=ReferenceDiscoveryStatus.COMPLETED,
            status_reason="completed",
            selections=(VideoSelection("BV1abc", "selected", "讨论同一政策"),),
        )
    )
    response = TestClient(create_app(reader, reference_discovery_service=discovery)).post(
        f"/api/articles/{article.article.article_id}/opinion/references"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["article_id"] == article.article.article_id
    assert payload["snapshot_id"] == article.snapshot_id
    assert payload["queries"] == [
        {
            "evidence_id": 1,
            "trigger_quote": "引发公众争议",
            "question": "该政策是否存在值得参考的公开讨论？",
            "query": "某机构 新政策 争议",
            "purpose": "寻找同一事件的视频讨论",
        }
    ]
    assert payload["candidates"][0]["video_id"] == "BV1abc"
    assert payload["selected_video_ids"] == ["BV1abc"]
    assert payload["selections"][0]["reason"] == "讨论同一政策"
    assert "comments" not in payload
    assert discovery.article_ids == [article.article.article_id]


def test_reference_api_maps_article_not_found(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    discovery = _ReferenceApiService(error=OpinionArticleNotFoundError("不存在的文章"))

    response = TestClient(create_app(reader, reference_discovery_service=discovery)).post(
        "/api/articles/missing/opinion/references"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "article_not_found"


def test_reference_api_maps_snapshot_error(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    discovery = _ReferenceApiService(error=OpinionSnapshotMismatchError("快照缺失"))

    response = TestClient(create_app(reader, reference_discovery_service=discovery)).post(
        "/api/articles/article-1/opinion/references"
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "article_snapshot_mismatch"


def test_reference_service_rejects_article_without_snapshot() -> None:
    class MissingSnapshotStore:
        def get_reader_article(self, _article_id: str):
            return type(
                "ReaderArticleStub",
                (),
                {"snapshot_id": None, "content_hash": None},
            )()

    with pytest.raises(OpinionSnapshotMismatchError):
        ReferenceDiscoveryService(store=MissingSnapshotStore()).discover("article-1")


def test_keyword_extraction_reads_full_article_and_uses_existing_plan_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from information_agent.opinion import llm

    article = replace(
        _reader(tmp_path).list_articles()[0].article,
        content="前文" * 3500 + "这里介绍机器人的运动控制",
    )
    requests = []

    def completion(**kwargs):
        requests.append(kwargs)
        return json.dumps(
            {
                "opinion_plans": [
                    {
                        "evidence_id": 1,
                        "trigger_quote": "这里介绍机器人的运动控制",
                        "question": "机器人如何控制运动？",
                        "queries": [{"query": "机器人 运动控制", "purpose": "寻找同主题视频"}],
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(llm, "request_json_completion", completion)
    plans = LLMOpinionAnalyzer(client=object(), model="test").extract_video_queries(article, 30)
    assert plans[0].trigger_quote in article.content
    assert json.loads(requests[0]["messages"][1]["content"])["content"] == article.content
    assert "不要求文章具有争议" in requests[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    "decisions",
    [
        [{"video_id": "invented", "decision": "selected", "reason": "相关"}],
        [],
        [{"video_id": "BV1abc", "decision": "selected", "reason": "相关"}] * 2,
        [{"video_id": "BV1abc", "decision": "selected", "reason": " "}],
        [{"video_id": "BV1abc", "decision": "maybe", "reason": "不确定"}],
    ],
)
def test_selector_rejects_invalid_or_incomplete_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decisions
) -> None:
    from information_agent.opinion import reference_selection

    monkeypatch.setattr(
        reference_selection,
        "request_json_completion",
        lambda **_: json.dumps({"selections": decisions}),
    )
    selector = LLMVideoSelector(LLMOpinionAnalyzer(client=object(), model="test"))
    candidate = BilibiliVideoCandidate(
        "BV1abc", "BV1abc", "https://www.bilibili.com/video/BV1abc", "视频", "政策"
    )
    with pytest.raises(ValueError):
        selector.select(_reader(tmp_path).list_articles()[0].article, (_plan(),), (candidate,), 30)


@pytest.mark.parametrize(
    "decision,reason",
    [
        ("selected", "同一机构的新政策"),
        ("rejected", "讨论不同机构"),
        ("uncertain", "简介不足以确认对象"),
    ],
)
def test_reference_pipeline_returns_agent_decision_without_collecting_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str, reason: str
) -> None:
    from information_agent.opinion import reference_selection

    reader = _reader(tmp_path)
    monkeypatch.setattr(
        reference_selection,
        "request_json_completion",
        lambda **_: json.dumps(
            {
                "selections": [{"video_id": "BV1abc", "decision": decision, "reason": reason}],
            }
        ),
    )
    service = ReferenceDiscoveryService(
        store=reader.store,
        planner=FakePlanner([_plan()]),
        video_searcher=FakeVideoSearcher(
            [
                {
                    "result": [
                        {
                            "bvid": "BV1abc",
                            "arcurl": "https://www.bilibili.com/video/BV1abc",
                            "title": "某机构新政策",
                            "description": "政策讨论",
                        }
                    ]
                }
            ]
        ),
        selector=LLMVideoSelector(LLMOpinionAnalyzer(client=object(), model="test")),
    )
    article_id = reader.list_articles()[0].article.article_id
    response = TestClient(create_app(reader, reference_discovery_service=service)).post(
        f"/api/articles/{article_id}/opinion/references"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["selected_video_ids"] == (["BV1abc"] if decision == "selected" else [])
    assert payload["status_reason"] == ("completed" if decision == "selected" else "no_matches")
    assert payload["selections"][0]["reason"] == reason
    assert reader.store.get_latest_opinion_run(article_id) is None


def test_selection_failure_keeps_candidates_but_does_not_select_them(tmp_path: Path) -> None:
    class FailingSelector:
        def select(self, *args):
            raise ValueError("模型响应无法解析")

    reader = _reader(tmp_path)
    result = ReferenceDiscoveryService(
        store=reader.store,
        planner=FakePlanner([_plan()]),
        video_searcher=FakeVideoSearcher(
            [
                {
                    "result": [
                        {
                            "bvid": "BV1abc",
                            "arcurl": "https://www.bilibili.com/video/BV1abc",
                            "title": "视频",
                        }
                    ]
                }
            ]
        ),
        selector=FailingSelector(),
    ).discover(reader.list_articles()[0].article.article_id)
    assert result.status_reason == "selection_failed"
    assert result.status is ReferenceDiscoveryStatus.PARTIAL
    assert len(result.candidates) == 1
    assert not result.selected_candidates


def test_partial_search_still_selects_available_candidates(tmp_path: Path) -> None:
    from dataclasses import replace

    reader = _reader(tmp_path)
    plan = replace(
        _plan(),
        queries=(
            SearchQuery("某机构 新政策", "查找政策讨论"),
            SearchQuery("某机构 政策影响", "查找影响讨论"),
        ),
    )
    result = ReferenceDiscoveryService(
        store=reader.store,
        planner=FakePlanner([plan]),
        selector=FakeSelector(),
        video_searcher=FakeVideoSearcher(
            [
                RuntimeError("一次搜索失败"),
                {
                    "result": [
                        {
                            "bvid": "BV1abc",
                            "title": "新政策讨论",
                            "arcurl": "https://www.bilibili.com/video/BV1abc",
                        }
                    ]
                },
            ]
        ),
    ).discover(reader.list_articles()[0].article.article_id)
    assert result.status_reason == "partial_search"
    assert result.status is ReferenceDiscoveryStatus.PARTIAL
    assert [item.video_id for item in result.selected_candidates] == ["BV1abc"]
    assert len(result.errors) == 1


def test_shared_deadline_stops_selection_after_search_uses_budget(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    elapsed = [0.0]

    class SlowSearcher:
        def search(self, query, timeout):
            elapsed[0] = 31.0
            return {
                "result": [
                    {
                        "bvid": "BV1abc",
                        "title": "新政策讨论",
                        "arcurl": "https://www.bilibili.com/video/BV1abc",
                    }
                ]
            }

    class UnexpectedSelector:
        def select(self, *args):
            pytest.fail("预算耗尽后不能调用模型")

    result = ReferenceDiscoveryService(
        store=reader.store,
        planner=FakePlanner([_plan()]),
        video_searcher=SlowSearcher(),
        selector=UnexpectedSelector(),
        timeout_seconds=30,
        clock=lambda: elapsed[0],
    ).discover(reader.list_articles()[0].article.article_id)
    assert result.status_reason == "selection_failed"
    assert len(result.candidates) == 1
    assert not result.selected_candidates
