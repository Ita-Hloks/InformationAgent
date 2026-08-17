from __future__ import annotations

from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query

from ..orchestration import agent_run, ingest
from ..reader import (
    ArticleNotFoundError,
    FeedNotFoundError,
    FeedUnavailableError,
    ReaderService,
)
from ..serialization import (
    agent_report_to_payload,
    persisted_collection_to_payload,
    research_run_summaries_to_payload,
)
from .models import (
    AgentRunRequest,
    ArticleResponse,
    ArticleStateResponse,
    ArticleStateUpdate,
    FeedCreate,
    FeedResponse,
    ResearchIngestRequest,
    ResearchRunsResponse,
    article_response,
    article_state_response,
    feed_response,
)


def create_app(service: ReaderService | None = None) -> FastAPI:
    load_dotenv()
    reader = service or ReaderService()
    app = FastAPI(title="Information Agent API", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/feeds", response_model=list[FeedResponse])
    def list_feeds() -> list[FeedResponse]:
        return [feed_response(item) for item in reader.list_subscriptions()]

    @app.post("/api/feeds", response_model=FeedResponse)
    def create_feed(request: FeedCreate) -> FeedResponse:
        try:
            return feed_response(reader.subscribe(request.url, title=request.title))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FeedUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/feeds/{feed_id}/refresh", response_model=FeedResponse)
    def refresh_feed(feed_id: str) -> FeedResponse:
        try:
            return feed_response(reader.refresh(feed_id))
        except FeedNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FeedUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/articles", response_model=list[ArticleResponse])
    def list_articles(
        feed_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> list[ArticleResponse]:
        try:
            articles = reader.list_articles(feed_id=feed_id, limit=limit, offset=offset)
        except FeedNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [article_response(item) for item in articles]

    @app.put("/api/articles/state", response_model=list[ArticleStateResponse])
    def update_article_states(request: ArticleStateUpdate) -> list[ArticleStateResponse]:
        try:
            states = reader.update_article_states(
                request.article_ids,
                is_read=request.is_read,
                is_saved=request.is_saved,
            )
        except ArticleNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return [article_state_response(state) for state in states]

    @app.get("/api/articles/{article_id}", response_model=ArticleResponse)
    def get_article(article_id: str) -> ArticleResponse:
        try:
            return article_response(reader.get_article(article_id))
        except ArticleNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/research/runs", response_model=ResearchRunsResponse)
    def list_research_runs(
        limit: int = Query(default=20, ge=1, le=100),
        status: str | None = Query(
            default=None,
            pattern="^(collecting|completed|partial|failed)$",
        ),
    ) -> dict[str, list[dict[str, Any]]]:
        return research_run_summaries_to_payload(reader.store.list_runs(limit=limit, status=status))

    @app.post("/api/research/ingest")
    def ingest_research(request: ResearchIngestRequest) -> dict[str, Any]:
        try:
            result = ingest(
                request.topic,
                request.feeds,
                database_path=reader.store.database_path,
                timeout_seconds=request.timeout_seconds,
                limit=request.limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return persisted_collection_to_payload(result)

    @app.post("/api/research/runs/{run_id}/agent")
    def run_research_agent(run_id: str, request: AgentRunRequest) -> dict[str, Any]:
        try:
            report = agent_run(
                run_id,
                database_path=reader.store.database_path,
                timeout_seconds=request.timeout_seconds,
                max_steps=request.max_steps,
                max_attempts=request.max_attempts,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return agent_report_to_payload(report)

    return app
