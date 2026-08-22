from __future__ import annotations

import inspect
import sqlite3
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..opinion import BilibiliTargetError, OpinionAnalysisService, OpinionStatus
from ..opinion.service import OpinionArticleNotFoundError, OpinionSnapshotMismatchError
from ..orchestration import agent_run, ingest
from ..reader import (
    ArticleAssistant,
    ArticleNotFoundError,
    FeedNotFoundError,
    FeedUnavailableError,
    ReaderService,
)
from ..serialization import (
    agent_report_to_payload,
    opinion_report_to_payload,
    persisted_collection_to_payload,
    research_run_summaries_to_payload,
)
from ..settings import EnvFileOpenError, MainLLMConfig, open_project_env_file
from ..storage import ResearchRunNotFoundError, ResearchRunNotReadyError
from .models import (
    AgentRunRequest,
    ArticleAnswerClearResponse,
    ArticleAnswerHistoryResponse,
    ArticleAnswerResponse,
    ArticleQuestionRequest,
    ArticleResponse,
    ArticleStateResponse,
    ArticleStateUpdate,
    EnvFileOpenResponse,
    FeedCreate,
    FeedResponse,
    LLMSettingsResponse,
    OpinionRequest,
    OpinionResponse,
    ResearchIngestRequest,
    ResearchRunsResponse,
    article_answer_response,
    article_response,
    article_state_response,
    feed_response,
)


def create_app(
    service: ReaderService | None = None,
    opinion_service: OpinionAnalysisService | None = None,
    article_assistant: ArticleAssistant | None = None,
) -> FastAPI:
    load_dotenv()
    reader = service or ReaderService()
    opinion = opinion_service or OpinionAnalysisService(store=reader.store)
    assistant = article_assistant or ArticleAssistant()
    app = FastAPI(title="Information Agent API", version="0.1.0")

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "invalid_request",
                    "message": "请求参数不符合约定",
                }
            },
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/settings", response_model=LLMSettingsResponse)
    def get_settings() -> LLMSettingsResponse:
        return LLMSettingsResponse(**MainLLMConfig.from_env().to_public_status())

    @app.post("/api/settings/env/open", response_model=EnvFileOpenResponse)
    def open_env_file() -> EnvFileOpenResponse:
        try:
            open_project_env_file()
        except (EnvFileOpenError, OSError) as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "env_open_failed",
                    "message": "无法打开项目 .env 文件，请确认文件存在并已安装默认编辑器",
                },
            ) from exc
        return EnvFileOpenResponse(status="opened")

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

    @app.post("/api/articles/{article_id}/ask", response_model=ArticleAnswerResponse)
    def ask_article(
        article_id: str,
        request: ArticleQuestionRequest,
    ) -> ArticleAnswerResponse:
        request_id = request.request_id or uuid4().hex
        try:
            article = reader.get_article(article_id)
            claim = reader.store.claim_article_answer(
                article,
                request_id=request_id,
                question=request.question,
            )
        except ArticleNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "article_not_found", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "request_id_conflict", "message": str(exc)},
            ) from exc
        if not claim.owner:
            return article_answer_response(claim.record)
        try:
            answer = _answer_article(assistant, article, request.question, request_id)
            return article_answer_response(reader.store.complete_article_answer(request_id, answer))
        except RuntimeError as exc:
            reader.store.fail_article_answer(request_id)
            raise HTTPException(
                status_code=503,
                detail={"code": "llm_unavailable", "message": str(exc)},
            ) from exc
        except (ValueError, TypeError, KeyError) as exc:
            reader.store.fail_article_answer(request_id)
            raise HTTPException(
                status_code=502,
                detail={"code": "assistant_failed", "message": "文章问答失败，请重试"},
            ) from exc
        except Exception as exc:
            reader.store.fail_article_answer(request_id)
            raise HTTPException(
                status_code=502,
                detail={"code": "assistant_failed", "message": "文章问答失败，请重试"},
            ) from exc

    @app.get(
        "/api/articles/{article_id}/ask/{request_id}",
        response_model=ArticleAnswerResponse,
    )
    def get_article_answer(article_id: str, request_id: str) -> ArticleAnswerResponse:
        try:
            reader.get_article(article_id)
            record = reader.store.get_article_answer(request_id)
            if record is None or record.article_id != article_id:
                raise ArticleNotFoundError(f"不存在的文章问答请求：{request_id}")
            return article_answer_response(record)
        except ArticleNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "answer_not_found", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/articles/{article_id}/answers",
        response_model=ArticleAnswerHistoryResponse,
    )
    def list_article_answers(
        article_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> ArticleAnswerHistoryResponse:
        try:
            article = reader.get_article(article_id)
            if article.snapshot_id is None:
                raise ValueError("文章缺少正文快照标识")
            answers, has_more = reader.store.list_article_answers(
                article_id,
                article.snapshot_id,
                limit=limit,
                offset=offset,
            )
            return ArticleAnswerHistoryResponse(
                article_id=article_id,
                snapshot_id=article.snapshot_id,
                answers=[article_answer_response(item) for item in answers],
                has_more=has_more,
            )
        except ArticleNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "article_not_found", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_pagination", "message": str(exc)},
            ) from exc

    @app.delete(
        "/api/articles/{article_id}/answers/current",
        response_model=ArticleAnswerClearResponse,
    )
    def clear_current_article_answers(article_id: str) -> ArticleAnswerClearResponse:
        try:
            article = reader.get_article(article_id)
            if article.snapshot_id is None:
                raise ValueError("文章缺少正文快照标识")
            deleted_count = reader.store.clear_article_answers(
                article_id,
                snapshot_id=article.snapshot_id,
            )
            return ArticleAnswerClearResponse(article_id=article_id, deleted_count=deleted_count)
        except ArticleNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "article_not_found", "message": str(exc)},
            ) from exc

    @app.delete(
        "/api/articles/{article_id}/answers",
        response_model=ArticleAnswerClearResponse,
    )
    def clear_all_article_answers(article_id: str) -> ArticleAnswerClearResponse:
        try:
            reader.get_article(article_id)
            deleted_count = reader.store.clear_article_answers(article_id)
            return ArticleAnswerClearResponse(article_id=article_id, deleted_count=deleted_count)
        except ArticleNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "article_not_found", "message": str(exc)},
            ) from exc

    @app.get("/api/articles/{article_id}/opinion", response_model=OpinionResponse)
    def get_opinion_status(article_id: str) -> OpinionResponse:
        try:
            return OpinionResponse(**opinion_report_to_payload(opinion.get_status(article_id)))
        except OpinionArticleNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "article_not_found", "message": str(exc)},
            ) from exc
        except OpinionSnapshotMismatchError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(
                status_code=500,
                detail={"code": "storage_failed", "message": "舆情状态读取失败"},
            ) from exc

    @app.post("/api/articles/{article_id}/opinion", response_model=OpinionResponse)
    def request_opinion_analysis(
        article_id: str,
        request: OpinionRequest | None = None,
    ) -> OpinionResponse | JSONResponse:
        try:
            report = opinion.request(
                article_id, force_refresh=request.force_refresh if request else False
            )
            payload = opinion_report_to_payload(report)
            if report.status is OpinionStatus.RUNNING:
                return JSONResponse(status_code=202, content=payload)
            return OpinionResponse(**payload)
        except OpinionArticleNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "article_not_found", "message": str(exc)},
            ) from exc
        except OpinionSnapshotMismatchError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except BilibiliTargetError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(
                status_code=500,
                detail={"code": "storage_failed", "message": "舆情运行收尾失败"},
            ) from exc

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

    @app.get("/api/research/runs/{run_id}/agent")
    def get_research_agent_report(run_id: str) -> dict[str, Any] | None:
        try:
            return reader.store.load_latest_agent_report(run_id)
        except ResearchRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
        except ResearchRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ResearchRunNotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return agent_report_to_payload(report)

    return app


def _answer_article(assistant: Any, article: Any, question: str, request_id: str) -> str:
    """Pass request identity to the built-in assistant without breaking injected adapters."""

    try:
        parameters = inspect.signature(assistant.answer).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_request_id = any(
        parameter.name == "request_id" or parameter.kind is parameter.VAR_KEYWORD
        for parameter in parameters
    )
    if accepts_request_id:
        return assistant.answer(article, question, request_id=request_id)
    return assistant.answer(article, question)
