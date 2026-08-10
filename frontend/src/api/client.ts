import type { Article, Feed } from "../types";

type FeedPayload = { id: string; url: string; title: string; article_count: number };
type ArticlePayload = {
  id: string;
  feed_id: string;
  source_url: string;
  title: string;
  author: string | null;
  categories: string[];
  published_at: string | null;
  content: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
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
    unread: 0,
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
    unread: true,
    starred: false,
    body: article.content.split(/\n+/).filter(Boolean),
    sourceUrl: article.source_url,
  }));
}
