from __future__ import annotations

import sqlite3
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..common import (
    LOG_MAX_BYTES,
    LOG_RETENTION_DAYS,
    cleanup_log_directory,
    clear_log_directory,
    inspect_log_directory,
)
from ..opinion import BilibiliTargetError, OpinionAnalysisService, OpinionStatus
from ..opinion.service import OpinionArticleNotFoundError, OpinionSnapshotMismatchError
from ..orchestration import AgentTaskManager, ArticleResearchTaskManager, agent_run, ingest
from ..orchestration.summary_tasks import SummaryTaskManager
from ..reader import (
    ArticleAssistant,
    ArticleNotFoundError,
    ArticleSummaryAssistant,
    FeedNotFoundError,
    FeedUnavailableError,
    ReaderService,
)
from ..search import public_status_from_env
from ..serialization import (
    opinion_report_to_payload,
    persisted_collection_to_payload,
    research_run_summaries_to_payload,
)
from ..settings import EnvFileOpenError, MainLLMConfig, open_project_env_file
from ..storage import ResearchRunNotFoundError, ResearchRunNotReadyError
from .models import (
    AgentRunRequest,
    AgentStopRequest,
    AgentTaskSnapshotResponse,
    ArticleAnswerClearResponse,
    ArticleAnswerHistoryResponse,
    ArticleAnswerResponse,
    ArticleQuestionRequest,
    ArticleResearchHistoryResponse,
    ArticleResearchRequest,
    ArticleResearchResponse,
    ArticleResponse,
    ArticleStateResponse,
    ArticleStateUpdate,
    EnvFileOpenResponse,
    FeedCreate,
    FeedResponse,
    LLMSettingsResponse,
    LogClearRequest,
    LogClearResponse,
    LogSettingsResponse,
    OpinionRequest,
    OpinionResponse,
    ReaderAutomationSettingsResponse,
    ReaderAutomationSettingsUpdate,
    ResearchIngestRequest,
    ResearchRunsResponse,
    SearchLLMSettingsResponse,
    article_answer_response,
    article_research_response,
    article_response,
    article_state_response,
    feed_response,
    reader_automation_settings_response,
)


def create_app(
    service: ReaderService | None = None,
    opinion_service: OpinionAnalysisService | None = None,
    article_assistant: ArticleAssistant | None = None,
    summary_assistant: ArticleSummaryAssistant | None = None,
    summary_task_manager: SummaryTaskManager | None = None,
    article_research_task_manager: ArticleResearchTaskManager | None = None,
) -> FastAPI:
    load_dotenv()
    reader = service or ReaderService()
    opinion = opinion_service or OpinionAnalysisService(store=reader.store)
    assistant = article_assistant or ArticleAssistant()
    agent_tasks = AgentTaskManager(reader.store.database_path, runner=agent_run)
    summary_tasks = summary_task_manager or SummaryTaskManager(
        reader.store,
        runner=(summary_assistant or ArticleSummaryAssistant()).summarize,
    )
    article_research_tasks = article_research_task_manager or ArticleResearchTaskManager(
        reader.store.database_path,
        store=reader.store,
        agent_tasks=agent_tasks,
    )
    try:
        cleanup_log_directory()
    except OSError:
        pass
    app = FastAPI(title="Information Agent API")

    def shutdown_background_tasks() -> None:
        summary_tasks.shutdown(wait=False)
        article_research_tasks.shutdown()
        agent_tasks.shutdown()

    app.router.on_shutdown.append(shutdown_background_tasks)

    def require_agent_settings() -> None:
        main_settings = MainLLMConfig.from_env()
        search_settings = public_status_from_env()
        if not main_settings.available:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "main_llm_unavailable",
                    "message": "主 LLM 配置不完整，无法运行 Agent",
                },
            )
        if not search_settings["available"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "search_llm_unavailable",
                    "message": "联网搜索配置不完整，无法运行 Agent",
                },
            )

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

    @app.get(
        "/api/settings/reader-automation",
        response_model=ReaderAutomationSettingsResponse,
    )
    def get_reader_automation_settings() -> ReaderAutomationSettingsResponse:
        return reader_automation_settings_response(reader.store.get_reader_automation_settings())

    @app.put(
        "/api/settings/reader-automation",
        response_model=ReaderAutomationSettingsResponse,
    )
    def update_reader_automation_settings(
        request: ReaderAutomationSettingsUpdate,
    ) -> ReaderAutomationSettingsResponse:
        try:
            settings = reader.store.update_reader_automation_settings(**request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return reader_automation_settings_response(settings)

    @app.get("/api/settings/search", response_model=SearchLLMSettingsResponse)
    def get_search_settings() -> SearchLLMSettingsResponse:
        return SearchLLMSettingsResponse(**public_status_from_env())

    @app.get("/api/settings/logs", response_model=LogSettingsResponse)
    def get_log_settings() -> LogSettingsResponse:
        usage = inspect_log_directory()
        return LogSettingsResponse(
            file_count=usage.file_count,
            total_bytes=usage.total_bytes,
            earliest_at=usage.earliest_at,
            retention_days=LOG_RETENTION_DAYS,
            max_bytes=LOG_MAX_BYTES,
        )

    @app.post("/api/settings/logs/clear", response_model=LogClearResponse)
    def clear_logs(request: LogClearRequest) -> LogClearResponse:
        if not request.confirm:
            raise HTTPException(
                status_code=422,
                detail={"code": "confirmation_required", "message": "清理日志需要确认"},
            )
        deleted_count = clear_log_directory()
        usage = inspect_log_directory()
        return LogClearResponse(
            file_count=usage.file_count,
            total_bytes=usage.total_bytes,
            earliest_at=usage.earliest_at,
            retention_days=LOG_RETENTION_DAYS,
            max_bytes=LOG_MAX_BYTES,
            deleted_count=deleted_count,
        )

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
            subscription = reader.subscribe(request.url, title=request.title)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FeedUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        summary_tasks.wake()
        return feed_response(subscription)

    @app.delete("/api/feeds/{feed_id}", status_code=204)
    def delete_feed(feed_id: str) -> None:
        try:
            reader.unsubscribe(feed_id)
        except FeedNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/feeds/{feed_id}/refresh", response_model=FeedResponse)
    def refresh_feed(feed_id: str) -> FeedResponse:
        try:
            subscription = reader.refresh(feed_id)
        except FeedNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FeedUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        summary_tasks.wake()
        return feed_response(subscription)

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
            article = reader.get_article(article_id)
        except ArticleNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if article.snapshot_id is not None:
            summary_tasks.submit(article.article.article_id, article.snapshot_id)
        return article_response(article)

    @app.post(
        "/api/articles/{article_id}/summary/retry",
        response_model=ArticleResponse,
    )
    def retry_article_summary(article_id: str) -> ArticleResponse:
        try:
            reader.get_article(article_id)
            summary_tasks.retry(article_id)
            article = reader.get_article(article_id)
        except (ArticleNotFoundError, KeyError) as exc:
            raise HTTPException(status_code=404, detail=f"不存在的文章：{article_id}") from exc
        return article_response(article)

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
            answer = assistant.answer(article, request.question, request_id=request_id)
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
            article = reader.get_article(article_id)
            record = reader.store.get_article_answer(request_id)
            if (
                record is None
                or record.article_id != article_id
                or record.snapshot_id != article.snapshot_id
            ):
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
            pending_request = reader.store.get_latest_running_article_answer(
                article_id,
                article.snapshot_id,
            )
            return ArticleAnswerHistoryResponse(
                article_id=article_id,
                snapshot_id=article.snapshot_id,
                answers=[article_answer_response(item) for item in answers],
                has_more=has_more,
                pending_request=(
                    article_answer_response(pending_request)
                    if pending_request is not None
                    else None
                ),
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

    @app.get(
        "/api/articles/{article_id}/research",
        response_model=ArticleResearchHistoryResponse,
    )
    def list_article_research(
        article_id: str,
        mode: str | None = Query(default=None, pattern="^(auto|manual)$"),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> ArticleResearchHistoryResponse:
        try:
            reader.get_article(article_id)
            runs = reader.store.list_article_research_runs(
                article_id,
                mode=mode,
                limit=limit,
            )
        except ArticleNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ArticleResearchHistoryResponse(
            article_id=article_id,
            runs=[
                article_research_response(
                    run,
                    agent=article_research_tasks.agent_snapshot(run),
                )
                for run in runs
            ],
        )

    @app.post(
        "/api/articles/{article_id}/research",
        response_model=ArticleResearchResponse,
    )
    def run_article_research(
        article_id: str,
        request: ArticleResearchRequest,
    ) -> ArticleResearchResponse:
        try:
            article = reader.get_article(article_id)
        except ArticleNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        settings = reader.store.get_reader_automation_settings()
        if request.mode == "auto" and not settings.enabled:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "reader_automation_disabled",
                    "message": "阅读触发研究未启用",
                },
            )
        require_agent_settings()
        try:
            run = article_research_tasks.submit(
                article,
                mode=request.mode,
                settings=settings,
                request_id=request.request_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return article_research_response(
            run,
            agent=article_research_tasks.agent_snapshot(run),
        )

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
        mode: str | None = Query(default=None, pattern="^(auto|manual)$"),
    ) -> dict[str, list[dict[str, Any]]]:
        return research_run_summaries_to_payload(
            reader.store.list_runs(limit=limit, status=status, mode=mode)
        )

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
        summary_tasks.wake()
        return persisted_collection_to_payload(result)

    @app.post("/api/research/runs/{run_id}/agent")
    def run_research_agent(
        run_id: str,
        request: AgentRunRequest,
    ) -> AgentTaskSnapshotResponse:
        try:
            reader.store.load_planning_input(run_id)
        except ResearchRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ResearchRunNotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        require_agent_settings()
        try:
            snapshot = agent_tasks.submit(
                run_id,
                request_id=request.request_id or uuid4().hex,
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
        return AgentTaskSnapshotResponse(**snapshot)

    @app.get(
        "/api/research/runs/{run_id}/agent/status",
        response_model=AgentTaskSnapshotResponse,
    )
    def get_research_agent_status(
        run_id: str,
        request_id: str | None = Query(default=None, max_length=200),
    ) -> AgentTaskSnapshotResponse:
        try:
            snapshot = agent_tasks.get(run_id, request_id=request_id)
        except ResearchRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "agent_not_found", "message": "不存在的 Agent 运行"},
            )
        return AgentTaskSnapshotResponse(**snapshot)

    @app.post(
        "/api/research/runs/{run_id}/agent/stop",
        response_model=AgentTaskSnapshotResponse,
    )
    def stop_research_agent(
        run_id: str,
        request: AgentStopRequest | None = None,
    ) -> AgentTaskSnapshotResponse:
        try:
            snapshot = agent_tasks.stop_and_wait(
                run_id,
                request_id=request.request_id if request else None,
            )
        except ResearchRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "agent_not_found", "message": "不存在的 Agent 运行"},
            )
        return AgentTaskSnapshotResponse(**snapshot)

    return app
