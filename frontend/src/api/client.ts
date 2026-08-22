import type {
  AgentReport,
  Article,
  ArticleAnswer,
  ArticleContext,
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
  article_id: string;
  answer: string;
};
type ArticleContextPayload = {
  context_id: string;
  source_url: string;
  title: string;
  is_local: boolean;
  confirmed: boolean;
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
): Promise<ArticleAnswer> {
  const payload = await request<ArticleAnswerPayload>(
    `/api/articles/${encodeURIComponent(articleId)}/ask`,
    {
      method: "POST",
      body: JSON.stringify({ question }),
      signal,
    },
  );
  return { articleId: payload.article_id, answer: payload.answer };
}

function toArticleContext(payload: ArticleContextPayload): ArticleContext {
  return {
    contextId: payload.context_id,
    sourceUrl: payload.source_url,
    title: payload.title,
    isLocal: payload.is_local,
    confirmed: payload.confirmed,
  };
}

export async function resolveArticleContext(
  url: string,
  signal?: AbortSignal,
): Promise<ArticleContext> {
  return toArticleContext(
    await request<ArticleContextPayload>("/api/article-context", {
      method: "POST",
      body: JSON.stringify({ url }),
      signal,
    }),
  );
}

export async function confirmArticleContext(
  contextId: string,
  signal?: AbortSignal,
): Promise<ArticleContext> {
  return toArticleContext(
    await request<ArticleContextPayload>(
      `/api/article-context/${encodeURIComponent(contextId)}/confirm`,
      { method: "POST", signal },
    ),
  );
}

export async function askArticleContext(
  contextId: string,
  question: string,
  signal?: AbortSignal,
): Promise<ArticleAnswer> {
  const payload = await request<ArticleAnswerPayload>(
    `/api/article-context/${encodeURIComponent(contextId)}/ask`,
    {
      method: "POST",
      body: JSON.stringify({ question }),
      signal,
    },
  );
  return { articleId: payload.article_id, answer: payload.answer };
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
