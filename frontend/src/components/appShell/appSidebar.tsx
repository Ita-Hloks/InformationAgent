import { useState } from "react";
import {
  Bookmark,
  ChevronDown,
  CircleUserRound,
  Inbox,
  Library,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Rss,
  Settings,
  Sun,
  Trash2,
  X,
} from "lucide-react";

import type { Feed, LibraryView } from "../../types";

type AppSidebarProps = {
  activeView: LibraryView;
  feeds: Feed[];
  selectedFeedId: string | null;
  unreadTotal: number;
  apiStatus: "connecting" | "connected" | "unavailable";
  open: boolean;
  collapsed: boolean;
  onClose: () => void;
  onCollapse: () => void;
  onExpand: () => void;
  onSelectView: (view: LibraryView) => void;
  onSelectFeed: (feedId: string) => void;
  onAddFeed: () => void;
  onUnsubscribe: (feedId: string) => void;
  settingsActive: boolean;
  onOpenSettings: () => void;
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
  selectedFeedId,
  unreadTotal,
  apiStatus,
  open,
  collapsed,
  onClose,
  onCollapse,
  onExpand,
  onSelectView,
  onSelectFeed,
  onAddFeed,
  onUnsubscribe,
  settingsActive,
  onOpenSettings,
}: AppSidebarProps) {
  const [confirmingFeedId, setConfirmingFeedId] = useState<string | null>(null);

  const selectView = (view: LibraryView) => {
    onSelectView(view);
    onClose();
  };

  const selectFeed = (feedId: string) => {
    onSelectFeed(feedId);
    onClose();
  };

  const requestUnsubscribe = (feedId: string) => {
    if (confirmingFeedId === feedId) {
      setConfirmingFeedId(null);
      onUnsubscribe(feedId);
      return;
    }
    setConfirmingFeedId(feedId);
  };

  const sidebarToggleLabel = collapsed ? "展开侧边栏" : "收起侧边栏";

  return (
    <aside
      className={`fixed inset-y-0 left-0 z-40 flex min-h-0 w-[252px] min-w-0 flex-col overflow-hidden border-r border-white/8 bg-[#17191d] text-[#ecece7] transition-transform duration-200 xl:h-full xl:relative xl:w-full xl:translate-x-0 xl:self-stretch xl:z-auto ${
        open ? "translate-x-0" : "-translate-x-full"
      }`}
      aria-label="资料库导航"
    >
      <div className="sidebar-header flex h-16 shrink-0 items-center justify-between border-b border-white/8 px-4">
        <div className="sidebar-brand flex min-w-0 items-center gap-2.5">
          <span className="grid size-8 shrink-0 place-items-center rounded-md bg-[#ef8354] text-xs font-bold text-[#21130d]">
            IA
          </span>
          <span className="sidebar-label min-w-0">
            <strong className="block truncate text-sm font-semibold tracking-[0]">信息助手</strong>
            <span className="block text-[11px] text-[#898d94]">研究阅读器</span>
          </span>
        </div>
        <div className="sidebar-header-actions flex shrink-0 items-center gap-1">
          <button
            type="button"
            className="hidden size-8 place-items-center rounded-md text-[#9da1a8] hover:bg-white/8 hover:text-white xl:grid"
            aria-label={sidebarToggleLabel}
            title={sidebarToggleLabel}
            onClick={collapsed ? onExpand : onCollapse}
          >
            {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
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
      </div>

      <div className="sidebar-scroll min-h-0 flex-1 overflow-y-auto px-2.5 py-3">
        <nav className="space-y-0.5" aria-label="阅读视图">
          {mainNavigation.map(item => {
            const Icon = item.icon;
            const active = !settingsActive && activeView === item.id && selectedFeedId === null;
            return (
              <button
                key={item.id}
                type="button"
                className={`sidebar-compact-item flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors ${
                  active
                    ? "bg-white/9 text-white"
                    : "text-[#aeb1b7] hover:bg-white/6 hover:text-white"
                }`}
                aria-label={collapsed ? item.label : undefined}
                title={collapsed ? item.label : undefined}
                onClick={() => selectView(item.id)}
              >
                <Icon size={16} strokeWidth={1.8} />
                <span className="sidebar-label flex-1 text-left">{item.label}</span>
                {item.id === "inbox" && unreadTotal > 0 && (
                  <span className="sidebar-meta text-xs tabular-nums text-[#777c84]">
                    {unreadTotal}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        <div className="mt-6">
          <div className="sidebar-section-tools mb-1.5 flex items-center justify-between px-2.5">
            <button
              type="button"
              className="sidebar-label flex items-center gap-1 text-[11px] font-medium text-[#72767e]"
              aria-hidden={collapsed}
              tabIndex={collapsed ? -1 : 0}
            >
              订阅源 <ChevronDown size={12} />
            </button>
            <button
              type="button"
              className="sidebar-section-action grid size-6 place-items-center rounded text-[#777b82] hover:bg-white/8 hover:text-white"
              aria-label="添加 RSS 订阅"
              title="添加 RSS 订阅"
              onClick={onAddFeed}
            >
              <Plus size={14} />
            </button>
          </div>
          <div className="space-y-0.5">
            {feeds.map(feed => (
              <div
                key={feed.id}
                className={`sidebar-compact-item group flex min-h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors ${
                  selectedFeedId === feed.id
                    ? "bg-white/9 text-white"
                    : "text-[#aeb1b7] hover:bg-white/6 hover:text-white"
                }`}
              >
                <button
                  type="button"
                  className="sidebar-feed-select flex min-w-0 flex-1 items-center gap-2.5 text-left"
                  aria-label={collapsed ? feed.name : undefined}
                  title={collapsed ? feed.name : undefined}
                  onClick={() => selectFeed(feed.id)}
                >
                  <span
                    className="grid size-5 shrink-0 place-items-center rounded text-[9px] font-semibold text-white"
                    style={{ backgroundColor: feed.color }}
                  >
                    {feed.name.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="sidebar-label min-w-0 flex-1 truncate">{feed.name}</span>
                  {feed.unread > 0 && (
                    <span className="sidebar-meta text-xs tabular-nums text-[#777c84]">
                      {feed.unread}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  className={`sidebar-feed-action shrink-0 rounded text-xs transition-[width,padding,color,background-color] duration-200 disabled:cursor-wait ${
                    confirmingFeedId === feed.id
                      ? "flex h-7 items-center gap-1.5 bg-[#fbe9e4] px-2 text-[#b7523c] hover:bg-[#f8dfd8]"
                      : "grid size-6 place-items-center text-[#777b82] hover:bg-white/10 hover:text-white"
                  }`}
                  aria-label={
                    confirmingFeedId === feed.id
                      ? `确认取消订阅 ${feed.name}`
                      : `取消订阅 ${feed.name}`
                  }
                  title={
                    confirmingFeedId === feed.id
                      ? `确认取消订阅 ${feed.name}`
                      : `取消订阅 ${feed.name}`
                  }
                  onClick={() => requestUnsubscribe(feed.id)}
                >
                  <Trash2 size={14} className="shrink-0" />
                  {confirmingFeedId === feed.id && (
                    <span className="whitespace-nowrap">确认取消订阅</span>
                  )}
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            className="sidebar-compact-item mt-2 flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-sm text-[#8f949c] hover:bg-white/6 hover:text-white"
            aria-label={collapsed ? "查找并添加来源" : undefined}
            title={collapsed ? "查找并添加来源" : undefined}
            onClick={onAddFeed}
          >
            <Rss size={16} strokeWidth={1.8} />
            <span className="sidebar-label">查找并添加来源</span>
          </button>
        </div>
      </div>

      <div className="sidebar-footer shrink-0 border-t border-white/8 p-2.5">
        <button
          type="button"
          className={`sidebar-compact-item flex h-11 w-full items-center gap-2.5 rounded-md px-2 text-left hover:bg-white/6 ${
            settingsActive ? "bg-white/9" : ""
          }`}
          aria-current={settingsActive ? "page" : undefined}
          aria-label={collapsed ? "打开设置" : undefined}
          title={collapsed ? "打开设置" : undefined}
          onClick={onOpenSettings}
        >
          <CircleUserRound size={22} className="text-[#a3a7ad]" strokeWidth={1.6} />
          <span className="sidebar-label min-w-0 flex-1">
            <strong className="block truncate text-xs font-medium">本地工作区</strong>
            <span className="block text-[10px] text-[#72767e]">
              {apiStatus === "connected"
                ? "本地 API 已连接"
                : apiStatus === "connecting"
                  ? "正在连接 API"
                  : "本地 API 不可用"}
            </span>
          </span>
          <Settings size={15} className="sidebar-meta text-[#777b82]" />
        </button>
      </div>
    </aside>
  );
}
