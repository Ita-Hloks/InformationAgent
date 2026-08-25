import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  Bot,
  Check,
  ChevronDown,
  ExternalLink,
  FileText,
  Loader2,
  Share2,
  Star,
  Square,
  Trash2,
} from "lucide-react";

import type { Article } from "../../types";
import type { ArticleResearchRun } from "../../types";
import { formatArticleFullDate } from "../../utils/date";
import { useClickOutside } from "../../hooks/useClickOutside";

type ReaderPaneProps = {
  article: Article | null;
  saved: boolean;
  read: boolean;
  onBack: () => void;
  onToggleSaved: () => void;
  onMarkRead: () => void;
  onAsk: () => void;
  onDelete: () => void;
  deleting?: boolean;
  deleteError?: string | null;
  onProgress?: (progress: number) => void;
  onVisibleSeconds?: (seconds: number) => void;
  onResearch?: () => void;
  onStopResearch?: () => void;
  onRetrySummary?: () => void;
  researchRuns?: ArticleResearchRun[];
  selectedResearchRunId?: string | null;
  onSelectResearchRun?: (runId: string) => void;
  researchRun?: ArticleResearchRun | null;
  researchRunning?: boolean;
  researchLoading?: boolean;
  researchError?: string | null;
};

export function ReaderPane({
  article,
  saved,
  read,
  onBack,
  onToggleSaved,
  onMarkRead,
  onAsk,
  onDelete,
  deleting = false,
  deleteError = null,
  onProgress,
  onVisibleSeconds,
  onResearch,
  onStopResearch,
  onRetrySummary,
  researchRuns = [],
  selectedResearchRunId = null,
  onSelectResearchRun,
  researchRun = null,
  researchRunning = false,
  researchLoading = false,
  researchError = null,
}: ReaderPaneProps) {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const articleKey = article ? `${article.id}:${article.snapshotId}` : null;
  const [deleteConfirming, setDeleteConfirming] = useState(false);
  const deleteActionRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setDeleteConfirming(false);
  }, [articleKey]);

  useClickOutside(deleteActionRef, () => setDeleteConfirming(false), deleteConfirming && !deleting);

  useEffect(() => {
    if (!articleKey || !onVisibleSeconds) return;

    let visibleSeconds = 0;
    const timer = window.setInterval(() => {
      const container = scrollContainerRef.current;
      if (document.visibilityState !== "visible" || !container?.getClientRects().length) return;
      visibleSeconds += 1;
      onVisibleSeconds(visibleSeconds);
    }, 1000);

    return () => window.clearInterval(timer);
  }, [articleKey, onVisibleSeconds]);

  useEffect(() => {
    if (!articleKey || !onProgress) return;

    const container = scrollContainerRef.current;
    if (!container) return;

    const reportProgress = () => {
      const scrollableHeight = container.scrollHeight - container.clientHeight;
      const progress = scrollableHeight <= 0 ? 1 : container.scrollTop / scrollableHeight;
      onProgress(Math.min(1, Math.max(0, progress)));
    };

    reportProgress();
    container.addEventListener("scroll", reportProgress, { passive: true });
    window.addEventListener("resize", reportProgress);
    return () => {
      container.removeEventListener("scroll", reportProgress);
      window.removeEventListener("resize", reportProgress);
    };
  }, [articleKey, onProgress]);

  if (!article) {
    return (
      <section className="flex h-full min-h-0 min-w-0 flex-col bg-[var(--reader-workspace-surface)]">
        <header className="h-16 shrink-0 border-b border-[var(--reader-workspace-border)]" />
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
    <section className="flex h-full min-h-0 min-w-0 flex-col bg-[var(--reader-workspace-surface)]">
      <header className="flex h-16 shrink-0 items-center justify-between gap-2 border-b border-[var(--reader-workspace-border)] px-3 sm:px-4">
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="grid size-9 place-items-center rounded-md text-[#60636a] hover:bg-[var(--reader-workspace-hover)] md:hidden"
            aria-label="返回文章列表"
            title="返回文章列表"
            onClick={onBack}
          >
            <ArrowLeft size={18} />
          </button>
          <button
            type="button"
            className={`grid size-9 place-items-center rounded-md hover:bg-[var(--reader-workspace-hover)] ${
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
            className={`grid size-9 place-items-center rounded-md hover:bg-[var(--reader-workspace-hover)] ${
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
            className="grid size-9 place-items-center rounded-md text-[#696c72] hover:bg-[var(--reader-workspace-hover)]"
            aria-label="分享文章"
            title="分享文章"
          >
            <Share2 size={17} />
          </button>
          <a
            href={article.sourceUrl}
            target="_blank"
            rel="noreferrer"
            className="grid size-9 place-items-center rounded-md text-[#696c72] hover:bg-[var(--reader-workspace-hover)]"
            aria-label="打开原文"
            title="打开原文"
          >
            <ExternalLink size={17} />
          </a>
        </div>

        <div className="flex items-center gap-1">
          <button
            type="button"
            className="flex h-9 items-center gap-2 rounded-md border border-[var(--reader-workspace-border)] bg-white px-3 text-xs font-medium text-[#34363a] shadow-sm hover:border-[#c9c8c1] hover:bg-[var(--reader-workspace-raised)]"
            onClick={onAsk}
          >
            <Bot size={16} className="text-[#b75f39]" />
            向助手提问
          </button>
          <button
            type="button"
            ref={deleteActionRef}
            className={`article-delete-action flex h-9 w-9 shrink-0 items-center justify-center gap-1.5 overflow-hidden rounded-md px-0 text-xs font-medium transition-[width,padding,color,background-color] duration-200 disabled:cursor-wait disabled:opacity-70 ${
              deleteConfirming
                ? "w-[108px] bg-[#fbe9e4] px-2.5 text-[#b7523c] hover:bg-[#f8dfd8]"
                : "text-[#696c72] hover:bg-[var(--reader-workspace-hover)]"
            }`}
            aria-label={deleteConfirming ? "确认删除文章" : "删除文章"}
            title={deleteConfirming ? "确认删除文章" : "删除文章"}
            disabled={deleting}
            onClick={() => {
              if (deleteConfirming) onDelete();
              else setDeleteConfirming(true);
            }}
          >
            {deleting ? (
              <Loader2 size={17} className="shrink-0 animate-spin" />
            ) : (
              <Trash2 size={17} className="shrink-0" />
            )}
            {deleteConfirming && !deleting && <span className="whitespace-nowrap">确认删除</span>}
          </button>
        </div>
      </header>

      {deleteError && (
        <p
          className="shrink-0 border-b border-[#efc6ba] bg-[#fff5f1] px-4 py-2 text-xs text-[#a64a35]"
          role="alert"
        >
          {deleteError}
        </p>
      )}

      <div ref={scrollContainerRef} className="workspace-scroll min-h-0 flex-1 overflow-y-auto">
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
                {article.author} · {formatArticleFullDate(article.publishedAtIso)} ·{" "}
                {article.readingMinutes} 分钟阅读
              </span>
            </span>
          </div>

          <h1 className="mt-7 text-[32px] leading-[1.18] font-semibold tracking-[0] text-[#202124] sm:text-[40px]">
            {article.title}
          </h1>
          {article.summaryStatus === "completed" && article.summary && (
            <p className="mt-4 text-[17px] leading-7 text-[#696b70]">{article.summary}</p>
          )}
          {(article.summaryStatus === "pending" || article.summaryStatus === "running") && (
            <p className="mt-4 text-[15px] leading-7 text-[#96989c]">摘要生成中</p>
          )}
          {article.summaryStatus === "failed" && (
            <div className="mt-4 flex flex-wrap items-center gap-2 text-[15px] leading-7 text-[#96989c]">
              <span>摘要生成失败</span>
              {onRetrySummary && (
                <button
                  type="button"
                  className="rounded-md border border-[var(--reader-workspace-border)] bg-white px-2.5 py-1 text-xs font-medium text-[#56585d] hover:bg-[var(--reader-workspace-raised)]"
                  onClick={onRetrySummary}
                >
                  重试摘要
                </button>
              )}
            </div>
          )}

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

          <ArticleResearchSection
            snapshotId={article.snapshotId}
            researchRuns={researchRuns}
            selectedResearchRunId={selectedResearchRunId}
            onSelectResearchRun={onSelectResearchRun}
            onStopResearch={onStopResearch}
            researchRun={researchRun}
            loading={researchLoading}
            error={researchError}
          />

          <div className="mt-10 flex flex-wrap items-center gap-2 border-t border-[var(--reader-workspace-border)] pt-5">
            <span className="rounded-md bg-[var(--reader-workspace-hover)] px-2.5 py-1 text-[11px] text-[#66686d]">
              {article.category}
            </span>
            {onResearch && (
              <button
                type="button"
                className="ml-auto rounded-md border border-[var(--reader-workspace-border)] bg-white px-2.5 py-1 text-[11px] font-medium text-[#56585d] hover:bg-[var(--reader-workspace-raised)]"
                disabled={researchRunning}
                onClick={onResearch}
              >
                {researchRunning ? "研究中" : "研究"}
              </button>
            )}
          </div>
        </article>
      </div>
    </section>
  );
}

function ArticleResearchSection({
  snapshotId,
  researchRuns,
  selectedResearchRunId,
  onSelectResearchRun,
  onStopResearch,
  researchRun,
  loading,
  error,
}: {
  snapshotId: string;
  researchRuns: ArticleResearchRun[];
  selectedResearchRunId: string | null;
  onSelectResearchRun?: (runId: string) => void;
  onStopResearch?: () => void;
  researchRun: ArticleResearchRun | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading && !researchRun && researchRuns.length === 0) {
    return (
      <section className="mt-12 border-t border-[var(--reader-workspace-border)] pt-6">
        <h2 className="text-sm font-semibold text-[#34363a]">文章研究</h2>
        <p className="mt-4 text-sm text-[#85878c]">读取研究记录中</p>
      </section>
    );
  }

  if (!researchRun && researchRuns.length === 0) {
    return error ? (
      <section className="mt-12 border-t border-[var(--reader-workspace-border)] pt-6">
        <h2 className="text-sm font-semibold text-[#34363a]">文章研究</h2>
        <p className="mt-4 text-sm leading-6 text-[#8a3e24]">{error}</p>
      </section>
    ) : null;
  }

  const selectedRun =
    researchRun ?? researchRuns.find(run => run.id === selectedResearchRunId) ?? null;
  const report = researchRun?.agent?.report ?? null;
  const isActive = selectedRun?.status === "queued" || selectedRun?.status === "running";
  const isHistorical = researchRun !== null && researchRun.snapshotId !== snapshotId;
  const noSearch =
    researchRun?.status === "completed" &&
    report !== null &&
    report.plans.length === 0 &&
    report.answers.length === 0;

  return (
    <section className="mt-12 border-t border-[var(--reader-workspace-border)] pt-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <h2 className="text-sm font-semibold text-[#34363a]">
            {isHistorical ? "历史文章研究" : "文章研究"}
          </h2>
          {selectedRun && (
            <span className="text-xs text-[#85878c]">
              {isHistorical ? "历史快照" : "当前快照"} · {researchModeLabel(selectedRun.mode)}
            </span>
          )}
        </div>
        {selectedRun && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-[#85878c]">
              {researchStatusLabel(selectedRun.status)}
            </span>
            {isActive && onStopResearch && (
              <button
                type="button"
                className="flex h-7 items-center gap-1.5 rounded-md border border-[#efc6ba] px-2 text-xs font-medium text-[#a64a35] hover:bg-[#fff5f1]"
                aria-label="停止研究"
                title="停止研究"
                onClick={onStopResearch}
              >
                <Square size={12} fill="currentColor" />
                停止
              </button>
            )}
          </div>
        )}
      </div>

      {researchRuns.length > 0 && (
        <div className="mt-5 border-b border-[var(--reader-workspace-border)] pb-5">
          <h3 className="text-xs font-semibold text-[#55585e]">历史记录</h3>
          <div className="mt-3 space-y-1.5">
            {researchRuns.map(run => {
              const selected = run.id === selectedResearchRunId;
              const current = run.snapshotId === snapshotId;
              return (
                <button
                  key={run.id}
                  type="button"
                  className={`flex w-full min-w-0 items-center justify-between gap-3 rounded-md border px-3 py-2 text-left transition-colors ${
                    selected
                      ? "border-[#d7b6a8] bg-[#fff8f4]"
                      : "border-[var(--reader-workspace-border)] hover:bg-[var(--reader-workspace-raised)]"
                  }`}
                  onClick={() => onSelectResearchRun?.(run.id)}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-xs font-medium text-[#55585e]">
                      {current ? "当前快照" : "历史快照"} · {researchModeLabel(run.mode)}
                    </span>
                    <span className="mt-1 block text-[11px] text-[#85878c]">
                      {formatArticleFullDate(run.createdAt)}
                    </span>
                  </span>
                  <span className="shrink-0 text-[11px] text-[#85878c]">
                    {researchStatusLabel(run.status)}
                  </span>
                </button>
              );
            })}
          </div>
          {loading && !researchRun && <p className="mt-3 text-xs text-[#85878c]">读取研究详情中</p>}
        </div>
      )}

      {!researchRun && error && <p className="mt-4 text-sm leading-6 text-[#8a3e24]">{error}</p>}

      {!researchRun ? null : (
        <>
          <section className="mt-6 border-b border-[var(--reader-workspace-border)] pb-5">
            <h3 className="text-xs font-semibold text-[#55585e]">结论与引用</h3>
            {report?.final_answer ? (
              <p className="mt-3 whitespace-pre-wrap text-[15px] leading-7 text-[#3f4248]">
                {report.final_answer}
              </p>
            ) : isActive ? (
              <p className="mt-3 text-sm leading-6 text-[#85878c]">研究处理中</p>
            ) : (
              <p className="mt-3 text-sm leading-6 text-[#85878c]">暂未生成结论</p>
            )}
            {noSearch && report && (
              <p className="mt-3 text-sm leading-6 text-[#696b70]">
                未发现需要外部搜索的关键缺口
                <span className="ml-2 text-xs text-[#96989c]">结束原因：{report.stop_reason}</span>
              </p>
            )}
            {report && report.citations.length > 0 && (
              <div className="mt-4 space-y-3">
                {report.citations.map((citation, index) => (
                  <div
                    key={`${citation.claim}-${index}`}
                    className="border-t border-[var(--reader-workspace-border)] pt-3 text-sm leading-6 text-[#55585e]"
                  >
                    <p>{citation.claim}</p>
                    {citation.source_urls.length > 0 && (
                      <ul className="mt-2 space-y-1 text-xs">
                        {citation.source_urls.map(sourceUrl => (
                          <li key={sourceUrl} className="min-w-0">
                            <a
                              className="break-all text-[#3978a8] hover:underline"
                              href={sourceUrl}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {sourceUrl}
                            </a>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="border-b border-[var(--reader-workspace-border)] py-5">
            <h3 className="text-xs font-semibold text-[#55585e]">不确定性</h3>
            {report && report.uncertainties.length > 0 ? (
              <ul className="mt-3 space-y-2 text-sm leading-6 text-[#696b70]">
                {report.uncertainties.map(item => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-sm leading-6 text-[#85878c]">暂无记录</p>
            )}
          </section>

          <details
            className="group border-b border-[var(--reader-workspace-border)] py-5"
            open={isActive}
          >
            <summary className="flex cursor-pointer list-none items-center justify-between text-xs font-semibold text-[#55585e] [&::-webkit-details-marker]:hidden">
              <span>研究轨迹</span>
              <ChevronDown
                size={14}
                className="text-[#85878c] transition-transform group-open:rotate-180"
              />
            </summary>
            {researchRun.agent ? (
              <div className="mt-4 space-y-4 text-sm leading-6 text-[#696b70]">
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#85878c]">
                  <span>阶段：{researchRun.agent.phase}</span>
                  <span>
                    尝试：{researchRun.agent.attempt} / {researchRun.agent.max_attempts}
                  </span>
                </div>
                {researchRun.agent.stage_details.length > 0 && (
                  <div className="space-y-2">
                    {researchRun.agent.stage_details.map(stage => (
                      <div
                        key={stage.step_key}
                        className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-t border-[var(--reader-workspace-border)] pt-2 text-xs"
                      >
                        <span className="min-w-0 break-words font-medium text-[#55585e]">
                          {stage.step_key}
                        </span>
                        <span className="text-[#85878c]">{stage.status}</span>
                      </div>
                    ))}
                  </div>
                )}
                {report && report.plans.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-[#85878c]">研究问题与查询</p>
                    <div className="mt-2 space-y-3">
                      {report.plans.map(plan => (
                        <div key={`${plan.evidence_id}-${plan.question}`}>
                          <p>{plan.question}</p>
                          <p className="mt-1 break-words text-xs text-[#85878c]">
                            原文锚点：{plan.trigger_quote}
                          </p>
                          <ul className="mt-1 space-y-1 text-xs text-[#85878c]">
                            {plan.queries.map(query => (
                              <li key={query.query}>{query.query}</li>
                            ))}
                          </ul>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {report && report.answers.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-[#85878c]">搜索回答</p>
                    <div className="mt-2 space-y-3">
                      {report.answers.map(answer => (
                        <div key={`${answer.evidence_id}-${answer.question}`}>
                          <p>{answer.answer}</p>
                          <p className="mt-1 break-words text-xs text-[#85878c]">
                            {answer.question}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {researchRun.agent.message && (
                  <p className="text-xs text-[#85878c]">{researchRun.agent.message}</p>
                )}
              </div>
            ) : (
              <p className="mt-4 text-sm text-[#85878c]">等待 Agent 启动</p>
            )}
          </details>

          <div className="pt-5">
            <h3 className="text-xs font-semibold text-[#55585e]">本次有效参数</h3>
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#85878c]">
              <span>超时 {researchRun.timeoutSeconds} 秒</span>
              <span>搜索最多 {researchRun.maxSearches} 次</span>
              <span>重试最多 {researchRun.maxAttempts} 次</span>
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function researchModeLabel(mode: ArticleResearchRun["mode"]): string {
  return mode === "auto" ? "自动触发" : "手动触发";
}

function researchStatusLabel(status: ArticleResearchRun["status"]): string {
  if (status === "queued") return "排队中";
  if (status === "running") return "运行中";
  if (status === "completed") return "已完成";
  if (status === "partial") return "部分完成";
  if (status === "failed") return "失败";
  return "已取消";
}
