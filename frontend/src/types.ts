export type LibraryView = "inbox" | "today" | "all" | "saved" | "research";
export type MobilePane = "list" | "reader";

export type Feed = {
  id: string;
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
};

export type ResearchRun = {
  id: string;
  title: string;
  status: "completed" | "partial" | "running";
  articleCount: number;
};
