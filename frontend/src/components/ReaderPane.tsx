import {
  ArrowLeft,
  Bot,
  Check,
  ExternalLink,
  FileText,
  MoreHorizontal,
  Share2,
  Star,
} from "lucide-react";

import type { Article } from "../types";

type ReaderPaneProps = {
  article: Article | null;
  saved: boolean;
  read: boolean;
  onBack: () => void;
  onToggleSaved: () => void;
  onMarkRead: () => void;
  onAsk: () => void;
};

export function ReaderPane({
  article,
  saved,
  read,
  onBack,
  onToggleSaved,
  onMarkRead,
  onAsk,
}: ReaderPaneProps) {
  if (!article) {
    return (
      <section className="flex h-full min-h-0 min-w-0 flex-col bg-[#fbfbf8]">
        <header className="h-16 shrink-0 border-b border-[#e3e3de]" />
        <div className="grid min-h-0 flex-1 place-items-center px-6 text-center">
          <div>
            <FileText size={24} className="mx-auto text-[#a4a5a5]" />
            <p className="mt-3 text-sm font-medium text-[#5f6165]">选择一篇文章</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col bg-[#fbfbf8]">
      <header className="flex h-16 shrink-0 items-center justify-between gap-2 border-b border-[#e3e3de] px-3 sm:px-4">
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="grid size-9 place-items-center rounded-md text-[#60636a] hover:bg-[#efefeb] md:hidden"
            aria-label="返回文章列表"
            title="返回文章列表"
            onClick={onBack}
          >
            <ArrowLeft size={18} />
          </button>
          <button
            type="button"
            className={`grid size-9 place-items-center rounded-md hover:bg-[#efefeb] ${
              read ? "text-[#399269]" : "text-[#696c72]"
            }`}
            aria-label={read ? "已读" : "标为已读"}
            title={read ? "已读" : "标为已读"}
            onClick={onMarkRead}
          >
            <Check size={18} />
          </button>
          <button
            type="button"
            className={`grid size-9 place-items-center rounded-md hover:bg-[#efefeb] ${
              saved ? "text-[#ef8354]" : "text-[#696c72]"
            }`}
            aria-label={saved ? "取消收藏" : "收藏文章"}
            title={saved ? "取消收藏" : "收藏文章"}
            onClick={onToggleSaved}
          >
            <Star size={17} fill={saved ? "currentColor" : "none"} />
          </button>
          <button
            type="button"
            className="grid size-9 place-items-center rounded-md text-[#696c72] hover:bg-[#efefeb]"
            aria-label="分享文章"
            title="分享文章"
          >
            <Share2 size={17} />
          </button>
          <a
            href={article.sourceUrl}
            target="_blank"
            rel="noreferrer"
            className="grid size-9 place-items-center rounded-md text-[#696c72] hover:bg-[#efefeb]"
            aria-label="打开原文"
            title="打开原文"
          >
            <ExternalLink size={17} />
          </a>
        </div>

        <div className="flex items-center gap-1">
          <button
            type="button"
            className="flex h-9 items-center gap-2 rounded-md border border-[#dddcd6] bg-white px-3 text-xs font-medium text-[#34363a] shadow-sm hover:border-[#c9c8c1] hover:bg-[#f7f7f4]"
            onClick={onAsk}
          >
            <Bot size={16} className="text-[#b75f39]" />
            向助手提问
          </button>
          <button
            type="button"
            className="grid size-9 place-items-center rounded-md text-[#696c72] hover:bg-[#efefeb]"
            aria-label="更多文章操作"
            title="更多文章操作"
          >
            <MoreHorizontal size={18} />
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <article className="mx-auto w-full max-w-[760px] px-6 pt-10 pb-20 sm:px-10 sm:pt-14">
          <div className="flex items-center gap-3 text-xs text-[#77797e]">
            <span className="grid size-8 place-items-center rounded-md bg-[#24272c] text-[10px] font-semibold text-white">
              {article.source.slice(0, 2).toUpperCase()}
            </span>
            <span className="min-w-0">
              <strong className="block truncate font-medium text-[#3b3d42]">
                {article.source}
              </strong>
              <span className="mt-0.5 block text-[11px] text-[#8a8c90]">
                {article.author} · {article.publishedAt} · {article.readingMinutes} 分钟阅读
              </span>
            </span>
          </div>

          <h1 className="mt-7 text-[32px] leading-[1.18] font-semibold tracking-[0] text-[#202124] sm:text-[40px]">
            {article.title}
          </h1>
          <p className="mt-4 text-[17px] leading-7 text-[#696b70]">{article.summary}</p>

          {article.imageUrl && (
            <img
              className="mt-8 aspect-[16/8.5] w-full rounded-lg object-cover"
              src={article.imageUrl}
              alt=""
            />
          )}

          <div className="reader-copy mt-9">
            {article.body.map(paragraph => (
              <p key={paragraph}>{paragraph}</p>
            ))}
          </div>

          <div className="mt-10 border-y border-[#e1e1db] py-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <span className="text-[11px] font-semibold text-[#56585d]">证据状态</span>
                <p className="mt-1 text-xs leading-5 text-[#85878b]">
                  来源身份已确认，具体断言仍需在研究运行中绑定正文证据
                </p>
              </div>
              <span className="shrink-0 rounded-full border border-[#e2b8a5] bg-[#fff4ee] px-2.5 py-1 text-[10px] font-medium text-[#a7512d]">
                待验证
              </span>
            </div>
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-2">
            <span className="rounded-md bg-[#ecece7] px-2.5 py-1 text-[11px] text-[#66686d]">
              {article.category}
            </span>
            <span className="rounded-md bg-[#ecece7] px-2.5 py-1 text-[11px] text-[#66686d]">
              RSS
            </span>
          </div>
        </article>
      </div>
    </section>
  );
}
