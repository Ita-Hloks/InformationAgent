from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..storage import FeedSubscription, ReaderArticle, ReaderArticleAnswer, ReaderArticleState


class FeedCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=120)


class FeedResponse(BaseModel):
    id: str
    url: str
    title: str
    site_url: str | None
    subscribed_at: str
    last_refreshed_at: str | None
    last_error: str | None
    article_count: int
    unread_count: int


class ArticleStateUpdate(BaseModel):
    article_ids: list[str] = Field(min_length=1, max_length=200)
    is_read: bool | None = None
    is_saved: bool | None = None


class ArticleStateResponse(BaseModel):
    article_id: str
    is_read: bool
    is_saved: bool
    read_at: str | None
    saved_at: str | None
    updated_at: str


class ArticleResponse(BaseModel):
    id: str
    feed_id: str
    snapshot_id: str
    source_url: str
    feed_url: str | None
    site_url: str | None
    title: str
    author: str | None
    categories: list[str]
    language: str | None
    content_type: str
    published_at: str | None
    collected_at: str
    content: str
    is_read: bool
    is_saved: bool


class ArticleQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    question: str = Field(min_length=1, max_length=2000)
    request_id: str | None = Field(default=None, max_length=200)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("question 不能为空")
        return normalized

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id 不能为空")
        return normalized


class ArticleAnswerResponse(BaseModel):
    status: Literal["running", "completed"]
    article_id: str
    request_id: str
    snapshot_id: str
    question: str
    answer: str
    created_at: str
    finished_at: str | None


class ArticleAnswerHistoryResponse(BaseModel):
    article_id: str
    snapshot_id: str
    answers: list[ArticleAnswerResponse]
    has_more: bool
    pending_request: ArticleAnswerResponse | None = None


class ArticleAnswerClearResponse(BaseModel):
    article_id: str
    deleted_count: int


class LLMSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key_configured: bool
    model: str
    base_url: str
    available: bool


class SearchLLMSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key_configured: bool
    model: str
    base_url: str
    result_count: int | None
    content_size: str | None
    timeout_seconds: float | None
    available: bool
    error: str | None


class LogSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_count: int
    total_bytes: int
    earliest_at: str | None
    retention_days: int
    max_bytes: int


class LogClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    confirm: bool


class LogClearResponse(LogSettingsResponse):
    deleted_count: int


class EnvFileOpenResponse(BaseModel):
    status: Literal["opened"]


class OpinionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    force_refresh: bool = False


class OpinionResponse(BaseModel):
    product_name: str
    article_id: str
    article_snapshot_id: str | None
    content_hash: str | None
    source_url: str
    status: str
    platform: str
    window_hours: int
    requested_limit: int | None
    collected_count: int
    analyzed_count: int
    classification_total: int
    classified_count: int
    unclassified_count: int
    status_reason: str
    run_id: str | None
    requested_at: str | None
    finished_at: str | None
    last_heartbeat_at: str | None
    controversy_points: list[dict[str, object]]
    comments: list[dict[str, object]]
    classifications: list[dict[str, object]]
    summary: str
    points: list[dict[str, object]]
    uncertainties: list[str]
    errors: list[dict[str, object]]
    attempts: list[dict[str, object]]


class ResearchIngestRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    feeds: list[str] = Field(min_length=1, max_length=20)
    timeout_seconds: float = Field(default=300, gt=0, le=600)
    limit: int = Field(default=20, ge=1, le=100)


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    timeout_seconds: float = Field(default=180, gt=0, le=600)
    max_steps: int = Field(default=3, ge=1, le=10)
    max_attempts: int = Field(default=3, ge=1, le=3)
    request_id: str | None = Field(default=None, max_length=200)

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id 不能为空")
        return normalized


class AgentStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str | None = Field(default=None, max_length=200)

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id 不能为空")
        return normalized


class AgentAttemptDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_no: int
    operation: str
    status: Literal["started", "succeeded", "failed", "interrupted", "cancelled"]
    error: dict[str, Any] | None
    retryable: bool


class AgentStageDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_key: str
    status: Literal[
        "pending", "running", "succeeded", "failed", "interrupted", "skipped", "cancelled"
    ]
    attempts: list[AgentAttemptDetailResponse]
    attempt: int
    max_attempts: int
    error: dict[str, Any] | None
    retryable: bool


class AgentTaskSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str | None
    run_id: str
    analysis_run_id: str | None
    status: Literal[
        "created",
        "running",
        "paused",
        "interrupted",
        "completed",
        "partial",
        "skipped",
        "failed",
        "cancelled",
    ]
    phase: str
    attempt: int
    max_attempts: int
    retryable: bool | None
    message: str
    error: dict[str, Any] | None
    stage_details: list[AgentStageDetailResponse]
    report: dict[str, Any] | None


class ResearchRunsResponse(BaseModel):
    runs: list[dict[str, Any]]


def feed_response(subscription: FeedSubscription) -> FeedResponse:
    return FeedResponse(
        id=subscription.feed_id,
        url=subscription.feed_url,
        title=subscription.title,
        site_url=subscription.site_url,
        subscribed_at=subscription.subscribed_at,
        last_refreshed_at=subscription.last_refreshed_at,
        last_error=subscription.last_error,
        article_count=subscription.article_count,
        unread_count=subscription.unread_count,
    )


def article_response(reader_article: ReaderArticle) -> ArticleResponse:
    article = reader_article.article
    return ArticleResponse(
        id=article.article_id,
        feed_id=reader_article.feed_id,
        snapshot_id=reader_article.snapshot_id,
        source_url=article.source_url,
        feed_url=article.feed_url,
        site_url=article.site_url,
        title=article.title,
        author=article.author,
        categories=list(article.categories),
        language=article.language,
        content_type=article.content_type.value,
        published_at=article.published_at.isoformat() if article.published_at else None,
        collected_at=article.collected_at.isoformat(),
        content=article.content,
        is_read=reader_article.is_read,
        is_saved=reader_article.is_saved,
    )


def article_state_response(state: ReaderArticleState) -> ArticleStateResponse:
    return ArticleStateResponse(
        article_id=state.article_id,
        is_read=state.is_read,
        is_saved=state.is_saved,
        read_at=state.read_at,
        saved_at=state.saved_at,
        updated_at=state.updated_at,
    )


def article_answer_response(answer: ReaderArticleAnswer) -> ArticleAnswerResponse:
    if answer.status == "completed" and not answer.answer:
        raise ValueError("已完成的文章问答缺少 answer")
    return ArticleAnswerResponse(
        status=answer.status,  # type: ignore[arg-type]
        article_id=answer.article_id,
        request_id=answer.request_id,
        snapshot_id=answer.snapshot_id,
        question=answer.question,
        answer=answer.answer or "",
        created_at=answer.created_at,
        finished_at=answer.finished_at,
    )
