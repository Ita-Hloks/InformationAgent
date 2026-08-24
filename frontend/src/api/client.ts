import type {
  Article,
  ArticleAnswer,
  ArticleAnswerHistory,
  ArticleResearchHistory,
  ArticleResearchRun,
  Feed,
  AgentTaskSnapshot,
  LLMSettings,
  LogSettings,
  ResearchIngestResult,
  ReaderAutomationSettings,
  ResearchMode,
  ResearchRun,
  ResearchStatus,
  SearchLLMSettings,
  SummaryStatus,
} from "../types";
import { formatArticleListDate } from "../utils/date";

type FeedPayload = {
  id: string;
  url: string;
  title: string;
  article_count: number;
  unread_count?: number;
};
type ArticlePayload = {
  id: string;
  feed_id: string;
  snapshot_id: string;
  source_url: string;
  title: string;
  author: string | null;
  categories: string[];
  published_at: string | null;
  content: string;
  summary: string | null;
  summary_status: SummaryStatus;
  summary_error: string | null;
  research_status: ResearchStatus;
  research_mode: ResearchMode | null;
  is_read?: boolean;
  is_saved?: boolean;
};
type ArticleStatePayload = {
  article_id: string;
  is_read: boolean;
  is_saved: boolean;
  read_at: string | null;
  saved_at: string | null;
  updated_at: string;
};
type ArticleAnswerPayload = {
  status: "running" | "completed";
  article_id: string;
  request_id: string;
  snapshot_id: string;
  question: string;
  answer: string;
  created_at: string;
  finished_at: string | null;
};
type ArticleAnswerHistoryPayload = {
  article_id: string;
  snapshot_id: string;
  answers: ArticleAnswerPayload[];
  has_more: boolean;
  pending_request: ArticleAnswerPayload | null;
};
type LLMSettingsPayload = {
  api_key_configured: boolean;
  model: string;
  base_url: string;
  available: boolean;
};
type SearchLLMSettingsPayload = {
  api_key_configured: boolean;
  model: string;
  base_url: string;
  result_count: number | null;
  content_size: string | null;
  timeout_seconds: number | null;
  available: boolean;
  error: string | null;
};
type LogSettingsPayload = {
  file_count: number;
  total_bytes: number;
  earliest_at: string | null;
  retention_days: number;
  max_bytes: number;
};
type AgentTaskSnapshotPayload = AgentTaskSnapshot;
type ResearchRunPayload = {
  run_id: string;
  topic: string;
  mode: ResearchMode;
  status: ResearchRun["status"];
  started_at: string;
  finished_at?: string;
  feed_count: number;
  snapshot_count: number;
  selected_evidence_count: number;
  collection_error_count: number;
};
type ReaderAutomationSettingsPayload = {
  enabled: boolean;
  dwell_seconds: number;
  read_ratio: number;
  agent_timeout_seconds: number;
  max_searches: number;
  max_attempts: number;
  updated_at: string;
};
type ReaderAutomationSettingsUpdatePayload = Omit<ReaderAutomationSettingsPayload, "updated_at">;
type ArticleResearchRunPayload = {
  run_id: string;
  article_id: string;
  snapshot_id: string;
  topic: string;
  mode: ResearchMode;
  status: ArticleResearchRun["status"];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  request_id: string;
  analysis_run_id: string | null;
  timeout_seconds: number;
  max_searches: number;
  max_attempts: number;
  error: ArticleResearchRun["error"];
  agent: AgentTaskSnapshot | null;
};
type ArticleResearchHistoryPayload = {
  article_id: string;
  runs: ArticleResearchRunPayload[];
};

type ApiErrorDetail = {
  code?: unknown;
  message?: unknown;
};

export type ArticleStateUpdate = {
  isRead?: boolean;
  isSaved?: boolean;
};

const SAFE_ERROR_MESSAGES: Record<string, string> = {
  confirmation_required: "请先确认后再执行操作",
  env_open_failed: "无法打开项目 .env 文件，请确认文件存在并已安装默认编辑器",
  invalid_request: "请求参数不符合约定，请检查后重试",
  llm_unavailable: "模型服务暂时不可用，请稍后重试",
  assistant_failed: "文章问答失败，请稍后重试",
  research_ingest_failed: "采集入库失败，请稍后重试",
  research_agent_failed: "Agent 运行失败，请稍后重试",
  main_llm_unavailable: "主模型配置未完成，请先补全环境变量",
  search_llm_unavailable: "搜索模型配置未完成，请先补全环境变量",
  agent_not_found: "不存在的 Agent 运行，请刷新后重试",
  article_not_found: "文章不存在，请刷新后重试",
  answer_not_found: "问答记录不存在，请刷新后重试",
  request_id_conflict: "请求已存在或已被占用，请稍后重试",
  storage_failed: "数据读取失败，请稍后重试",
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let detail = defaultErrorMessage(response.status);
    try {
      const payload = (await response.json()) as { detail?: unknown };
      detail = formatApiErrorMessage(response.status, payload.detail);
    } catch {
      // Preserve the status when the server did not return JSON.
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function defaultErrorMessage(status: number): string {
  if (status === 504) return "请求超时，请稍后重试";
  if (status === 502 || status === 503) return "服务暂时不可用，请稍后重试";
  if (status >= 500) return "服务暂时不可用，请稍后重试";
  return `请求失败（${status}）`;
}

function formatApiErrorMessage(status: number, detail: unknown): string {
  if (detail && typeof detail === "object") {
    const error = detail as ApiErrorDetail;
    if (typeof error.code === "string" && SAFE_ERROR_MESSAGES[error.code]) {
      return SAFE_ERROR_MESSAGES[error.code];
    }
    if (typeof error.message === "string") {
      if (status >= 500) return defaultErrorMessage(status);
      return error.message;
    }
  }
  if (typeof detail === "string") {
    return status >= 500 ? defaultErrorMessage(status) : detail;
  }
  return defaultErrorMessage(status);
}

function toFeed(feed: FeedPayload): Feed {
  const parsed = new URL(feed.url);
  return {
    id: feed.id,
    url: feed.url,
    name: feed.title,
    domain: `${parsed.host}${parsed.pathname === "/" ? "" : parsed.pathname}`,
    unread: feed.unread_count ?? 0,
    color: "#3978a8",
  };
}

export async function getFeeds(): Promise<Feed[]> {
  return (await request<FeedPayload[]>("/api/feeds")).map(toFeed);
}

export async function addFeed(input: { url: string; title?: string }): Promise<Feed> {
  return toFeed(
    await request<FeedPayload>("/api/feeds", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  );
}

export async function removeFeed(feedId: string): Promise<void> {
  await request<void>(`/api/feeds/${encodeURIComponent(feedId)}`, { method: "DELETE" });
}

export async function refreshFeed(feedId: string): Promise<Feed> {
  return toFeed(
    await request<FeedPayload>(`/api/feeds/${encodeURIComponent(feedId)}/refresh`, {
      method: "POST",
    }),
  );
}

export async function getArticles(feedId?: string): Promise<Article[]> {
  const path = feedId ? `/api/articles?feed_id=${encodeURIComponent(feedId)}` : "/api/articles";
  const articles = await request<ArticlePayload[]>(path);
  return articles.map(toArticle);
}

export async function deleteArticle(articleId: string): Promise<void> {
  await request<void>(`/api/articles/${encodeURIComponent(articleId)}`, { method: "DELETE" });
}

function toArticle(article: ArticlePayload): Article {
  return {
    id: article.id,
    feedId: article.feed_id,
    snapshotId: article.snapshot_id,
    source: "RSS",
    author: article.author ?? "未知作者",
    title: article.title,
    summary: article.summary?.trim() ?? "",
    summaryStatus: article.summary_status,
    summaryError: article.summary_error,
    publishedAt: formatArticleListDate(article.published_at),
    publishedAtIso: article.published_at,
    researchStatus: article.research_status,
    researchMode: article.research_mode,
    readingMinutes: Math.max(1, Math.ceil(article.content.length / 400)),
    category: article.categories[0] ?? "未分类",
    imageUrl: "",
    unread: !(article.is_read ?? false),
    starred: article.is_saved ?? false,
    body: article.content.split(/\n+/).filter(Boolean),
    sourceUrl: article.source_url,
  };
}

function toReaderAutomationSettings(
  payload: ReaderAutomationSettingsPayload,
): ReaderAutomationSettings {
  return {
    enabled: payload.enabled,
    dwellSeconds: payload.dwell_seconds,
    readRatio: payload.read_ratio,
    agentTimeoutSeconds: payload.agent_timeout_seconds,
    maxSearches: payload.max_searches,
    maxAttempts: payload.max_attempts,
    updatedAt: payload.updated_at,
  };
}

function toArticleResearchRun(payload: ArticleResearchRunPayload): ArticleResearchRun {
  return {
    id: payload.run_id,
    articleId: payload.article_id,
    snapshotId: payload.snapshot_id,
    topic: payload.topic,
    mode: payload.mode,
    status: payload.status,
    createdAt: payload.created_at,
    startedAt: payload.started_at,
    finishedAt: payload.finished_at,
    requestId: payload.request_id,
    analysisRunId: payload.analysis_run_id,
    timeoutSeconds: payload.timeout_seconds,
    maxSearches: payload.max_searches,
    maxAttempts: payload.max_attempts,
    error: payload.error,
    agent: payload.agent,
  };
}

export async function getReaderAutomationSettings(): Promise<ReaderAutomationSettings> {
  const payload = await request<ReaderAutomationSettingsPayload>("/api/settings/reader-automation");
  return toReaderAutomationSettings(payload);
}

export async function updateReaderAutomationSettings(
  settings: Omit<ReaderAutomationSettings, "updatedAt">,
): Promise<ReaderAutomationSettings> {
  const payload = await request<ReaderAutomationSettingsPayload>(
    "/api/settings/reader-automation",
    {
      method: "PUT",
      body: JSON.stringify({
        enabled: settings.enabled,
        dwell_seconds: settings.dwellSeconds,
        read_ratio: settings.readRatio,
        agent_timeout_seconds: settings.agentTimeoutSeconds,
        max_searches: settings.maxSearches,
        max_attempts: settings.maxAttempts,
      } satisfies ReaderAutomationSettingsUpdatePayload),
    },
  );
  return toReaderAutomationSettings(payload);
}

export async function retryArticleSummary(articleId: string): Promise<Article> {
  const payload = await request<ArticlePayload>(
    `/api/articles/${encodeURIComponent(articleId)}/summary/retry`,
    { method: "POST" },
  );
  return toArticle(payload);
}

export async function getArticleResearch(
  articleId: string,
  options: { mode?: ResearchMode; limit?: number; signal?: AbortSignal } = {},
): Promise<ArticleResearchHistory> {
  const query = new URLSearchParams();
  if (options.mode) query.set("mode", options.mode);
  if (options.limit !== undefined) query.set("limit", String(options.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const payload = await request<ArticleResearchHistoryPayload>(
    `/api/articles/${encodeURIComponent(articleId)}/research${suffix}`,
    { signal: options.signal },
  );
  return {
    articleId: payload.article_id,
    runs: payload.runs.map(toArticleResearchRun),
  };
}

export async function runArticleResearch(
  articleId: string,
  input: { mode: ResearchMode; requestId?: string },
): Promise<ArticleResearchRun> {
  const payload = await request<ArticleResearchRunPayload>(
    `/api/articles/${encodeURIComponent(articleId)}/research`,
    {
      method: "POST",
      body: JSON.stringify({ mode: input.mode, request_id: input.requestId }),
    },
  );
  return toArticleResearchRun(payload);
}

export async function updateArticleStates(
  articleIds: string[],
  update: ArticleStateUpdate,
): Promise<ArticleStatePayload[]> {
  return request<ArticleStatePayload[]>("/api/articles/state", {
    method: "PUT",
    body: JSON.stringify({
      article_ids: articleIds,
      is_read: update.isRead,
      is_saved: update.isSaved,
    }),
  });
}

export async function getLLMSettings(): Promise<LLMSettings> {
  const payload = await request<LLMSettingsPayload>("/api/settings");
  return {
    apiKeyConfigured: payload.api_key_configured,
    model: payload.model,
    baseUrl: payload.base_url,
    available: payload.available,
  };
}

export async function getSearchLLMSettings(): Promise<SearchLLMSettings> {
  const payload = await request<SearchLLMSettingsPayload>("/api/settings/search");
  return {
    apiKeyConfigured: payload.api_key_configured,
    model: payload.model,
    baseUrl: payload.base_url,
    resultCount: payload.result_count,
    contentSize: payload.content_size,
    timeoutSeconds: payload.timeout_seconds,
    available: payload.available,
    error: payload.error,
  };
}

export async function getLogSettings(): Promise<LogSettings> {
  const payload = await request<LogSettingsPayload>("/api/settings/logs");
  return {
    fileCount: payload.file_count,
    totalBytes: payload.total_bytes,
    earliestAt: payload.earliest_at,
    retentionDays: payload.retention_days,
    maxBytes: payload.max_bytes,
  };
}

export async function clearLogDirectory(): Promise<LogSettings> {
  const payload = await request<LogSettingsPayload & { deleted_count: number }>(
    "/api/settings/logs/clear",
    {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    },
  );
  return {
    fileCount: payload.file_count,
    totalBytes: payload.total_bytes,
    earliestAt: payload.earliest_at,
    retentionDays: payload.retention_days,
    maxBytes: payload.max_bytes,
  };
}

export async function openProjectEnvFile(): Promise<void> {
  await request<{ status: "opened" }>("/api/settings/env/open", { method: "POST" });
}

export async function askArticle(
  articleId: string,
  question: string,
  signal?: AbortSignal,
  requestId = createArticleQuestionRequestId(),
): Promise<ArticleAnswer> {
  const payload = await request<ArticleAnswerPayload>(
    `/api/articles/${encodeURIComponent(articleId)}/ask`,
    {
      method: "POST",
      body: JSON.stringify({ question, request_id: requestId }),
      signal,
    },
  );
  return waitForArticleAnswer(articleId, requestId, payload, signal);
}

export async function resumeArticleAnswer(
  articleId: string,
  requestId: string,
  signal?: AbortSignal,
): Promise<ArticleAnswer> {
  const payload = await request<ArticleAnswerPayload>(
    `/api/articles/${encodeURIComponent(articleId)}/ask/${encodeURIComponent(requestId)}`,
    { signal },
  );
  return waitForArticleAnswer(articleId, requestId, payload, signal);
}

async function waitForArticleAnswer(
  articleId: string,
  requestId: string,
  initialPayload: ArticleAnswerPayload,
  signal?: AbortSignal,
): Promise<ArticleAnswer> {
  let payload = initialPayload;
  let attempts = 0;
  while (payload.status === "running" && attempts < 600) {
    await waitForArticleAnswerPoll(signal);
    payload = await request<ArticleAnswerPayload>(
      `/api/articles/${encodeURIComponent(articleId)}/ask/${encodeURIComponent(requestId)}`,
      { signal },
    );
    attempts += 1;
  }
  if (payload.status !== "completed" || !payload.answer) {
    throw new Error("文章问答仍在运行，请稍后重试");
  }
  return toArticleAnswer(payload);
}

export function createArticleQuestionRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `article-question-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function createResearchAgentRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `research-agent-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export async function getArticleAnswerHistory(
  articleId: string,
  signal?: AbortSignal,
  offset = 0,
): Promise<ArticleAnswerHistory> {
  const payload = await request<ArticleAnswerHistoryPayload>(
    `/api/articles/${encodeURIComponent(articleId)}/answers?limit=50&offset=${offset}`,
    { signal },
  );
  return {
    articleId: payload.article_id,
    snapshotId: payload.snapshot_id,
    answers: payload.answers
      .filter(item => item.status === "completed" && Boolean(item.answer))
      .map(toArticleAnswer),
    hasMore: payload.has_more,
    pendingRequest:
      payload.pending_request?.status === "running"
        ? toArticleAnswer(payload.pending_request)
        : null,
  };
}

export async function clearArticleAnswerHistory(
  articleId: string,
  scope: "current" | "all",
): Promise<number> {
  const suffix = scope === "current" ? "/current" : "";
  const payload = await request<{ deleted_count: number }>(
    `/api/articles/${encodeURIComponent(articleId)}/answers${suffix}`,
    { method: "DELETE" },
  );
  return payload.deleted_count;
}

function toArticleAnswer(payload: ArticleAnswerPayload): ArticleAnswer {
  return {
    articleId: payload.article_id,
    requestId: payload.request_id,
    snapshotId: payload.snapshot_id,
    question: payload.question,
    answer: payload.answer,
    createdAt: payload.created_at,
  };
}

function waitForArticleAnswerPoll(signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("请求已取消", "AbortError"));
      return;
    }
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, 500);
    const onAbort = () => {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(new DOMException("请求已取消", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function toResearchRun(run: ResearchRunPayload): ResearchRun {
  return {
    id: run.run_id,
    title: run.topic,
    mode: run.mode,
    status: run.status,
    articleCount: run.selected_evidence_count,
    feedCount: run.feed_count,
    errorCount: run.collection_error_count,
    startedAt: run.started_at,
    finishedAt: run.finished_at,
  };
}

export async function getResearchRuns(): Promise<ResearchRun[]> {
  const payload = await request<{ runs: ResearchRunPayload[] }>("/api/research/runs");
  return payload.runs.map(toResearchRun);
}

export async function getResearchAgentStatus(
  runId: string,
  requestId?: string | null,
  signal?: AbortSignal,
): Promise<AgentTaskSnapshot> {
  const query = requestId ? `?request_id=${encodeURIComponent(requestId)}` : "";
  return request<AgentTaskSnapshotPayload>(
    `/api/research/runs/${encodeURIComponent(runId)}/agent/status${query}`,
    { signal },
  );
}

export async function createResearchRun(input: {
  topic: string;
  feeds: string[];
  timeoutSeconds: number;
  limit: number;
}): Promise<ResearchIngestResult> {
  return request<ResearchIngestResult>("/api/research/ingest", {
    method: "POST",
    body: JSON.stringify({
      topic: input.topic,
      feeds: input.feeds,
      timeout_seconds: input.timeoutSeconds,
      limit: input.limit,
    }),
  });
}

export async function runResearchAgent(
  runId: string,
  input: { timeoutSeconds: number; maxSteps: number; maxAttempts: number },
  requestId?: string,
): Promise<AgentTaskSnapshot> {
  return request<AgentTaskSnapshotPayload>(
    `/api/research/runs/${encodeURIComponent(runId)}/agent`,
    {
      method: "POST",
      body: JSON.stringify({
        timeout_seconds: input.timeoutSeconds,
        max_steps: input.maxSteps,
        max_attempts: input.maxAttempts,
        request_id: requestId,
      }),
    },
  );
}

export async function stopResearchAgent(
  runId: string,
  requestId?: string | null,
): Promise<AgentTaskSnapshot> {
  return request<AgentTaskSnapshotPayload>(
    `/api/research/runs/${encodeURIComponent(runId)}/agent/stop`,
    {
      method: "POST",
      body: JSON.stringify({ request_id: requestId }),
    },
  );
}
