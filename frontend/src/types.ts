export type LibraryView = "inbox" | "today" | "all" | "saved" | "research";

export type Feed = {
  id: string;
  url: string;
  name: string;
  domain: string;
  unread: number;
  color: string;
};

export type Article = {
  id: string;
  feedId: string;
  source: string;
  author: string;
  title: string;
  summary: string;
  publishedAt: string;
  readingMinutes: number;
  category: string;
  imageUrl: string;
  unread: boolean;
  starred: boolean;
  body: string[];
  sourceUrl: string;
};

export type ArticleAnswer = {
  articleId: string;
  answer: string;
};

export type ArticleContext = {
  contextId: string;
  sourceUrl: string;
  title: string;
  isLocal: boolean;
  confirmed: boolean;
};

export type ResearchRun = {
  id: string;
  title: string;
  status: "collecting" | "completed" | "partial" | "failed";
  articleCount: number;
  feedCount: number;
  errorCount: number;
  startedAt: string;
  finishedAt?: string;
};

export type ResearchIngestResult = {
  run_id: string;
  topic: string;
  status: ResearchRun["status"];
  articles: unknown[];
  errors: string[];
};

export type AgentReport = {
  run_id: string;
  analysis_run_id: string | null;
  topic: string;
  status: "completed" | "partial" | "failed";
  final_answer: string | null;
  evidence_ids: number[];
  citations: Array<{
    claim: string;
    evidence_ids: number[];
    source_urls: string[];
  }>;
  uncertainties: string[];
  plans: Array<{
    evidence_id: number;
    question: string;
    queries: Array<{ query: string; purpose: string }>;
  }>;
  answers: Array<{
    evidence_id: number;
    question: string;
    answer: string;
    status: string;
    sources: Array<{ title: string; url: string }>;
  }>;
  steps: number;
  stop_reason: string;
  errors: string[];
};
