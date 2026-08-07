import { CheckCheck, Menu, Search, SlidersHorizontal, Star } from "lucide-react";

import type { Article } from "../types";

type ArticleListProps = {
  title: string;
  articles: Article[];
  selectedArticleId: string;
  savedIds: Set<string>;
  searchQuery: string;
  onSearchChange: (value: string) => void;
  onSelectArticle: (articleId: string) => void;
  onToggleSaved: (articleId: string) => void;
  onMarkAllRead: () => void;
  onOpenSidebar: () => void;
};

export function ArticleList({
  title,
  articles,
  selectedArticleId,
  savedIds,
  searchQuery,
  onSearchChange,
  onSelectArticle,
  onToggleSaved,
  onMarkAllRead,
  onOpenSidebar,
}: ArticleListProps) {
  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col border-r border-[#deded9] bg-[#f2f2ef]">
      <header className="shrink-0 border-b border-[#deded9] bg-[#f7f7f4] px-3 py-3">
        <div className="flex h-8 items-center gap-2">
          <button
            type="button"
            className="grid size-8 shrink-0 place-items-center rounded-md text-[#60636a] hover:bg-[#e7e7e2] hover:text-[#202124] xl:hidden"
            aria-label="打开资料库导航"
            title="打开资料库导航"
            onClick={onOpenSidebar}
          >
            <Menu size={18} />
          </button>
          <h1 className="min-w-0 flex-1 truncate text-[15px] font-semibold text-[#252629]">
            {title}
          </h1>
          <span className="text-xs tabular-nums text-[#85878c]">{articles.length}</span>
          <button
            type="button"
            className="grid size-8 place-items-center rounded-md text-[#676a70] hover:bg-[#e7e7e2] hover:text-[#202124]"
            aria-label="全部标为已读"
            title="全部标为已读"
            onClick={onMarkAllRead}
          >
            <CheckCheck size={17} />
          </button>
          <button
            type="button"
            className="grid size-8 place-items-center rounded-md text-[#676a70] hover:bg-[#e7e7e2] hover:text-[#202124]"
            aria-label="筛选文章"
            title="筛选文章"
          >
            <SlidersHorizontal size={16} />
          </button>
        </div>

        <label className="mt-2 flex h-9 items-center gap-2 rounded-md border border-[#d8d8d2] bg-white px-2.5 text-[#7a7d82] focus-within:border-[#bcbdb8]">
          <Search size={15} />
          <span className="sr-only">搜索文章</span>
          <input
            className="min-w-0 flex-1 bg-transparent text-sm text-[#252629] outline-none placeholder:text-[#9a9c9f]"
            type="search"
            placeholder="搜索标题、来源或主题"
            value={searchQuery}
            onChange={event => onSearchChange(event.target.value)}
          />
        </label>
      </header>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto" aria-label="文章列表">
        {articles.length === 0 ? (
          <div className="grid flex-1 place-items-center px-8 text-center">
            <div>
              <Search size={22} className="mx-auto text-[#a4a5a5]" />
              <p className="mt-3 text-sm font-medium text-[#53555a]">没有匹配的文章</p>
              <p className="mt-1 text-xs leading-5 text-[#929398]">调整搜索或切换订阅源</p>
            </div>
          </div>
        ) : (
          articles.map(article => {
            const selected = article.id === selectedArticleId;
            const saved = savedIds.has(article.id);
            return (
              <article
                key={article.id}
                className={`group relative border-b border-[#deded9] transition-colors ${
                  selected ? "bg-white" : "bg-[#f7f7f4] hover:bg-white"
                }`}
              >
                {selected && <span className="absolute inset-y-0 left-0 w-0.5 bg-[#ef8354]" />}
                <button
                  type="button"
                  className="grid w-full grid-cols-[minmax(0,1fr)_72px] gap-3 px-4 py-3.5 text-left"
                  onClick={() => onSelectArticle(article.id)}
                >
                  <span className="min-w-0">
                    <span className="flex min-w-0 items-center gap-1.5 text-[11px] text-[#777a80]">
                      {article.unread && (
                        <span className="size-1.5 shrink-0 rounded-full bg-[#ef8354]" />
                      )}
                      <span className="truncate font-medium text-[#55585e]">{article.source}</span>
                      <span>·</span>
                      <span className="shrink-0">{article.publishedAt}</span>
                    </span>
                    <strong
                      className={`mt-1.5 block text-[14px] leading-[1.4] tracking-[0] ${
                        article.unread
                          ? "font-semibold text-[#222326]"
                          : "font-medium text-[#4f5156]"
                      }`}
                    >
                      {article.title}
                    </strong>
                    <span className="mt-1.5 line-clamp-2 block text-xs leading-[1.55] text-[#77797e]">
                      {article.summary}
                    </span>
                    <span className="mt-2 flex items-center gap-2 text-[10px] text-[#999b9e]">
                      <span className="rounded border border-[#deded9] px-1.5 py-0.5">
                        {article.category}
                      </span>
                      <span>{article.readingMinutes} 分钟</span>
                    </span>
                  </span>
                  <img
                    className="mt-0.5 h-14 w-[72px] rounded-md object-cover grayscale-[0.08]"
                    src={article.imageUrl}
                    alt=""
                  />
                </button>
                <button
                  type="button"
                  className={`absolute right-3 bottom-2.5 grid size-7 place-items-center rounded-md opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100 ${
                    saved ? "text-[#ef8354] opacity-100" : "text-[#86888d] hover:bg-[#eeeeea]"
                  }`}
                  aria-label={saved ? "取消收藏" : "收藏文章"}
                  title={saved ? "取消收藏" : "收藏文章"}
                  onClick={() => onToggleSaved(article.id)}
                >
                  <Star size={15} fill={saved ? "currentColor" : "none"} />
                </button>
              </article>
            );
          })
        )}
      </div>
    </section>
  );
}
