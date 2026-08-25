export type LibraryView = "inbox" | "today" | "all" | "saved";

export type SummaryStatus = "pending" | "running" | "completed" | "failed";
export type ResearchStatus =
  "none" | "queued" | "running" | "completed" | "partial" | "failed" | "cancelled";
export type ResearchMode = "auto" | "manual";

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
  snapshotId: string;
  source: string;
  author: string;
  title: string;
  summary: string;
  summaryStatus: SummaryStatus;
  summaryError: string | null;
  publishedAt: string;
  publishedAtIso: string | null;
  researchStatus: ResearchStatus;
  researchMode: ResearchMode | null;
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
  requestId: string;
  snapshotId: string;
  question: string;
  answer: string;
  createdAt: string;
};

export type ArticleAnswerHistory = {
  articleId: string;
  snapshotId: string;
  answers: ArticleAnswer[];
  hasMore: boolean;
  pendingRequest: ArticleAnswer | null;
};

export type LLMSettings = {
  apiKeyConfigured: boolean;
  model: string;
  baseUrl: string;
  available: boolean;
};

export type SearchLLMSettings = {
  apiKeyConfigured: boolean;
  model: string;
  baseUrl: string;
  resultCount: number | null;
  contentSize: string | null;
  timeoutSeconds: number | null;
  available: boolean;
  error: string | null;
};

export type LogSettings = {
  fileCount: number;
  totalBytes: number;
  earliestAt: string | null;
  retentionDays: number;
  maxBytes: number;
};

export type ReaderAutomationSettings = {
  enabled: boolean;
  dwellSeconds: number;
  readRatio: number;
  agentTimeoutSeconds: number;
  maxSearches: number;
  maxAttempts: number;
  updatedAt: string;
};

export type ArticleResearchRunStatus =
  "queued" | "running" | "completed" | "partial" | "failed" | "cancelled";

export type ArticleResearchError = {
  type: string;
  message: string;
};

export type ArticleResearchRun = {
  id: string;
  articleId: string;
  snapshotId: string;
  topic: string;
  mode: ResearchMode;
  status: ArticleResearchRunStatus;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  requestId: string;
  analysisRunId: string | null;
  timeoutSeconds: number;
  maxSearches: number;
  maxAttempts: number;
  error: ArticleResearchError | null;
  agent: AgentTaskSnapshot | null;
};

export type ArticleResearchHistory = {
  articleId: string;
  runs: ArticleResearchRun[];
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
    trigger_quote: string;
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

export type AgentError = {
  type: string;
  message: string;
};

export type AgentAttemptDetail = {
  attempt_no: number;
  operation: string;
  status: "started" | "succeeded" | "failed" | "interrupted" | "cancelled";
  error: AgentError | null;
  retryable: boolean;
};

export type AgentStageDetail = {
  step_key: string;
  status: "pending" | "running" | "succeeded" | "failed" | "interrupted" | "skipped" | "cancelled";
  attempts: AgentAttemptDetail[];
  attempt: number;
  max_attempts: number;
  error: AgentError | null;
  retryable: boolean;
};

export type AgentTaskSnapshot = {
  request_id: string | null;
  run_id: string;
  analysis_run_id: string | null;
  status:
    | "created"
    | "running"
    | "paused"
    | "interrupted"
    | "completed"
    | "partial"
    | "skipped"
    | "failed"
    | "cancelled";
  phase: string;
  attempt: number;
  max_attempts: number;
  retryable: boolean | null;
  message: string;
  error: AgentError | null;
  stage_details: AgentStageDetail[];
  report: AgentReport | null;
};
