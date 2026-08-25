import type { LibraryView } from "../types";

export const viewPaths: Record<LibraryView, string> = {
  inbox: "/inbox",
  today: "/today",
  all: "/all",
  saved: "/saved",
};

export const viewTitles: Record<LibraryView, string> = {
  inbox: "收件箱",
  today: "今天",
  all: "全部文章",
  saved: "已收藏",
};

export function viewFromPath(pathname: string): LibraryView {
  const match = Object.entries(viewPaths).find(([, path]) => path === pathname);
  return (match?.[0] as LibraryView | undefined) ?? "all";
}

export function feedPath(feedId: string) {
  return `/feeds/${encodeURIComponent(feedId)}`;
}
