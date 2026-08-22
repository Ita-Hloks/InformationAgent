import type {
  AgentReport,
  Article,
  ArticleAnswer,
  ArticleAnswerHistory,
  Feed,
  LLMSettings,
  ResearchIngestResult,
  ResearchRun,
} from "../types";

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
  source_url: string;
  title: string;
  author: string | null;
  categories: string[];
  published_at: string | null;
  content: string;
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
};
type LLMSettingsPayload = {
  api_key_configured: boolean;
  model: string;
  base_url: string;
  available: boolean;
};
type ResearchRunPayload = {
  run_id: string;
  topic: string;
  status: ResearchRun["status"];
  started_at: string;
  finished_at?: string;
  feed_count: number;
  snapshot_count: number;
  selected_evidence_count: number;
  collection_error_count: number;
};

export type ArticleStateUpdate = {
  isRead?: boolean;
  isSaved?: boolean;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        detail = payload.detail;
      } else if (
        payload.detail !== null &&
        typeof payload.detail === "object" &&
        "message" in payload.detail &&
        typeof payload.detail.message === "string"
      ) {
        detail = payload.detail.message;
      }
    } catch {
      // Preserve the status when the server did not return JSON.
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
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

export async function getArticles(feedId?: string): Promise<Article[]> {
  const path = feedId ? `/api/articles?feed_id=${encodeURIComponent(feedId)}` : "/api/articles";
  const articles = await request<ArticlePayload[]>(path);
  return articles.map(article => ({
    id: article.id,
    feedId: article.feed_id,
    source: "RSS",
    author: article.author ?? "未知作者",
    title: article.title,
    summary: article.content.slice(0, 220),
    publishedAt: article.published_at ?? "未标注时间",
    readingMinutes: Math.max(1, Math.ceil(article.content.length / 400)),
    category: article.categories[0] ?? "未分类",
    imageUrl: "",
    unread: !(article.is_read ?? false),
    starred: article.is_saved ?? false,
    body: article.content.split(/\n+/).filter(Boolean),
    sourceUrl: article.source_url,
  }));
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

export async function openProjectEnvFile(): Promise<void> {
  await request<{ status: "opened" }>("/api/settings/env/open", { method: "POST" });
}

export async function askArticle(
  articleId: string,
  question: string,
  signal?: AbortSignal,
  requestId = createArticleQuestionRequestId(),
): Promise<ArticleAnswer> {
  let payload = await request<ArticleAnswerPayload>(
    `/api/articles/${encodeURIComponent(articleId)}/ask`,
    {
      method: "POST",
      body: JSON.stringify({ question, request_id: requestId }),
      signal,
    },
  );
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

export async function getResearchAgentReport(
  runId: string,
  signal?: AbortSignal,
): Promise<AgentReport | null> {
  return request<AgentReport | null>(`/api/research/runs/${encodeURIComponent(runId)}/agent`, {
    signal,
  });
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
): Promise<AgentReport> {
  return request<AgentReport>(`/api/research/runs/${encodeURIComponent(runId)}/agent`, {
    method: "POST",
    body: JSON.stringify({
      timeout_seconds: input.timeoutSeconds,
      max_steps: input.maxSteps,
      max_attempts: input.maxAttempts,
    }),
  });
}
