import {
  Archive,
  Bookmark,
  ChevronDown,
  CircleUserRound,
  FlaskConical,
  Inbox,
  Library,
  Plus,
  Rss,
  Settings,
  Sun,
  X,
} from "lucide-react";

import type { Feed, LibraryView, ResearchRun } from "../types";

type AppSidebarProps = {
  activeView: LibraryView;
  feeds: Feed[];
  researchRuns: ResearchRun[];
  selectedFeedId: string | null;
  unreadTotal: number;
  open: boolean;
  onClose: () => void;
  onSelectView: (view: LibraryView) => void;
  onSelectFeed: (feedId: string) => void;
  onAddFeed: () => void;
};

const mainNavigation = [
  { id: "inbox" as const, label: "收件箱", icon: Inbox },
  { id: "today" as const, label: "今天", icon: Sun },
  { id: "all" as const, label: "全部文章", icon: Library },
  { id: "saved" as const, label: "已收藏", icon: Bookmark },
];

export function AppSidebar({
  activeView,
  feeds,
  researchRuns,
  selectedFeedId,
  unreadTotal,
  open,
  onClose,
  onSelectView,
  onSelectFeed,
  onAddFeed,
}: AppSidebarProps) {
  const selectView = (view: LibraryView) => {
    onSelectView(view);
    onClose();
  };

  const selectFeed = (feedId: string) => {
    onSelectFeed(feedId);
    onClose();
  };

  return (
    <aside
      className={`fixed inset-y-0 left-0 z-40 flex w-[252px] flex-col border-r border-white/8 bg-[#17191d] text-[#ecece7] transition-transform duration-200 xl:static xl:z-auto xl:translate-x-0 ${
        open ? "translate-x-0" : "-translate-x-full"
      }`}
      aria-label="资料库导航"
    >
      <div className="flex h-16 shrink-0 items-center justify-between border-b border-white/8 px-4">
        <button
          type="button"
          className="flex min-w-0 items-center gap-2.5 text-left"
          onClick={() => selectView("inbox")}
        >
          <span className="grid size-8 shrink-0 place-items-center rounded-md bg-[#ef8354] text-xs font-bold text-[#21130d]">
            IA
          </span>
          <span className="min-w-0">
            <strong className="block truncate text-sm font-semibold tracking-[0]">
              Information Agent
            </strong>
            <span className="block text-[11px] text-[#898d94]">Research reader</span>
          </span>
        </button>
        <button
          type="button"
          className="grid size-8 place-items-center rounded-md text-[#9da1a8] hover:bg-white/8 hover:text-white xl:hidden"
          aria-label="关闭资料库导航"
          title="关闭资料库导航"
          onClick={onClose}
        >
          <X size={17} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2.5 py-3">
        <nav className="space-y-0.5" aria-label="阅读视图">
          {mainNavigation.map(item => {
            const Icon = item.icon;
            const active = activeView === item.id && selectedFeedId === null;
            return (
              <button
                key={item.id}
                type="button"
                className={`flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors ${
                  active
                    ? "bg-white/9 text-white"
                    : "text-[#aeb1b7] hover:bg-white/6 hover:text-white"
                }`}
                onClick={() => selectView(item.id)}
              >
                <Icon size={16} strokeWidth={1.8} />
                <span className="flex-1 text-left">{item.label}</span>
                {item.id === "inbox" && unreadTotal > 0 && (
                  <span className="text-xs tabular-nums text-[#777c84]">{unreadTotal}</span>
                )}
              </button>
            );
          })}
        </nav>

        <div className="mt-6">
          <div className="mb-1.5 flex items-center justify-between px-2.5">
            <span className="text-[11px] font-medium text-[#72767e]">研究运行</span>
            <button
              type="button"
              className="grid size-6 place-items-center rounded text-[#777b82] hover:bg-white/8 hover:text-white"
              aria-label="新建研究运行"
              title="新建研究运行"
              onClick={() => selectView("research")}
            >
              <Plus size={14} />
            </button>
          </div>
          <button
            type="button"
            className={`flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-sm ${
              activeView === "research"
                ? "bg-white/9 text-white"
                : "text-[#aeb1b7] hover:bg-white/6 hover:text-white"
            }`}
            onClick={() => selectView("research")}
          >
            <FlaskConical size={16} strokeWidth={1.8} />
            <span className="flex-1 text-left">研究记录</span>
            <span className="text-xs text-[#777c84]">{researchRuns.length}</span>
          </button>
          {researchRuns.map(run => (
            <button
              key={run.id}
              type="button"
              className="group flex min-h-8 w-full items-center gap-2 rounded-md py-1 pr-2 pl-8 text-left text-xs text-[#858a92] hover:bg-white/6 hover:text-[#d7d8da]"
              onClick={() => selectView("research")}
            >
              <span
                className={`size-1.5 shrink-0 rounded-full ${
                  run.status === "completed"
                    ? "bg-[#63b68d]"
                    : run.status === "running"
                      ? "bg-[#ef8354]"
                      : "bg-[#c4a460]"
                }`}
              />
              <span className="min-w-0 flex-1 truncate">{run.title}</span>
            </button>
          ))}
        </div>

        <div className="mt-6">
          <div className="mb-1.5 flex items-center justify-between px-2.5">
            <button
              type="button"
              className="flex items-center gap-1 text-[11px] font-medium text-[#72767e]"
            >
              订阅源 <ChevronDown size={12} />
            </button>
            <button
              type="button"
              className="grid size-6 place-items-center rounded text-[#777b82] hover:bg-white/8 hover:text-white"
              aria-label="添加 RSS 订阅"
              title="添加 RSS 订阅"
              onClick={onAddFeed}
            >
              <Plus size={14} />
            </button>
          </div>
          <div className="space-y-0.5">
            {feeds.map(feed => (
              <button
                key={feed.id}
                type="button"
                className={`flex min-h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors ${
                  selectedFeedId === feed.id
                    ? "bg-white/9 text-white"
                    : "text-[#aeb1b7] hover:bg-white/6 hover:text-white"
                }`}
                onClick={() => selectFeed(feed.id)}
              >
                <span
                  className="grid size-5 shrink-0 place-items-center rounded text-[9px] font-semibold text-white"
                  style={{ backgroundColor: feed.color }}
                >
                  {feed.name.slice(0, 1).toUpperCase()}
                </span>
                <span className="min-w-0 flex-1 truncate text-left">{feed.name}</span>
                {feed.unread > 0 && (
                  <span className="text-xs tabular-nums text-[#777c84]">{feed.unread}</span>
                )}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="mt-2 flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-sm text-[#8f949c] hover:bg-white/6 hover:text-white"
            onClick={onAddFeed}
          >
            <Rss size={16} strokeWidth={1.8} />
            查找并添加来源
          </button>
        </div>

        <button
          type="button"
          className="mt-6 flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-sm text-[#8f949c] hover:bg-white/6 hover:text-white"
        >
          <Archive size={16} strokeWidth={1.8} />
          已归档
        </button>
      </div>

      <div className="shrink-0 border-t border-white/8 p-2.5">
        <button
          type="button"
          className="flex h-11 w-full items-center gap-2.5 rounded-md px-2 text-left hover:bg-white/6"
        >
          <CircleUserRound size={22} className="text-[#a3a7ad]" strokeWidth={1.6} />
          <span className="min-w-0 flex-1">
            <strong className="block truncate text-xs font-medium">本地工作区</strong>
            <span className="block text-[10px] text-[#72767e]">未连接 API</span>
          </span>
          <Settings size={15} className="text-[#777b82]" />
        </button>
      </div>
    </aside>
  );
}
