import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Bot, Loader2, RotateCw, Send, Sparkles, Trash2, TriangleAlert, X } from "lucide-react";

import {
  askArticle,
  clearArticleAnswerHistory,
  createArticleQuestionRequestId,
  getArticleAnswerHistory,
  resumeArticleAnswer,
} from "../../api/client";
import { useOverlayDialog } from "../../hooks/useOverlayDialog";
import type { Article, ArticleAnswer } from "../../types";

type AskPanelProps = {
  article: Article;
  open: boolean;
  onClose: () => void;
};

type AnswerPhase = "idle" | "loading" | "success" | "error";

const suggestions = ["总结核心观点", "找出关键事实", "这对产品团队意味着什么？"];

export function AskPanel({ article, open, onClose }: AskPanelProps) {
  const questionInputRef = useRef<HTMLTextAreaElement>(null);
  const [question, setQuestion] = useState("");
  const [phase, setPhase] = useState<AnswerPhase>("idle");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [history, setHistory] = useState<ArticleAnswer[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [historyError, setHistoryError] = useState("");
  const [clearPhase, setClearPhase] = useState<"idle" | "current" | "all">("idle");
  const requestControllerRef = useRef<AbortController | null>(null);
  const historyControllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef<string | null>(null);
  const requestQuestionRef = useRef("");
  const snapshotIdRef = useRef<string | null>(null);
  const requestGenerationRef = useRef(0);

  useOverlayDialog(open, onClose, questionInputRef);

  const loadHistoryPage = useCallback(
    async (offset: number, append: boolean, signal: AbortSignal) => {
      const result = await getArticleAnswerHistory(article.id, signal, offset);
      if (signal.aborted) return;
      setHistory(current =>
        appendArticleAnswerHistory(current, result.answers, article.id, result.snapshotId),
      );
      setHistoryOffset(offset + result.answers.length);
      setHistoryHasMore(result.hasMore);
      return result;
    },
    [article.id],
  );

  const applyAnswerResult = useCallback(
    (result: ArticleAnswer, generation: number, signal: AbortSignal) => {
      if (
        signal.aborted ||
        generation !== requestGenerationRef.current ||
        result.articleId !== article.id
      ) {
        return;
      }
      if (snapshotIdRef.current !== null && result.snapshotId !== snapshotIdRef.current) {
        setError("文章正文已更新，请重新提问");
        setPhase("error");
        return;
      }
      snapshotIdRef.current = result.snapshotId;
      setAnswer(result.answer);
      setHistory(current =>
        appendArticleAnswerHistory(current, [result], article.id, result.snapshotId),
      );
      setPhase("success");
    },
    [article.id],
  );

  useEffect(() => {
    const generation = ++requestGenerationRef.current;
    requestControllerRef.current?.abort();
    historyControllerRef.current?.abort();
    requestIdRef.current = null;
    requestQuestionRef.current = "";
    snapshotIdRef.current = null;
    setQuestion("");
    setPhase("idle");
    setAnswer("");
    setError("");
    setHistory([]);
    setHistoryOffset(0);
    setHistoryHasMore(false);
    setHistoryError("");
    if (open) {
      const controller = new AbortController();
      historyControllerRef.current = controller;
      setHistoryLoading(true);
      void loadHistoryPage(0, false, controller.signal)
        .then(result => {
          if (!result || controller.signal.aborted || generation !== requestGenerationRef.current) {
            return;
          }
          snapshotIdRef.current = result.snapshotId;
          const pendingRequest = result.pendingRequest;
          if (!pendingRequest) return;

          requestIdRef.current = pendingRequest.requestId;
          requestQuestionRef.current = pendingRequest.question;
          setQuestion(pendingRequest.question);
          setPhase("loading");
          setAnswer("");
          setError("");
          const resumeController = new AbortController();
          requestControllerRef.current = resumeController;
          void resumeArticleAnswer(article.id, pendingRequest.requestId, resumeController.signal)
            .then(result => applyAnswerResult(result, generation, resumeController.signal))
            .catch(requestError => {
              if (resumeController.signal.aborted || generation !== requestGenerationRef.current) {
                return;
              }
              setError(
                requestError instanceof Error ? requestError.message : "文章问答失败，请重试",
              );
              setPhase("error");
            });
        })
        .catch(historyRequestError => {
          if (controller.signal.aborted) return;
          setHistoryError(
            historyRequestError instanceof Error ? historyRequestError.message : "历史读取失败",
          );
        })
        .finally(() => {
          if (!controller.signal.aborted) setHistoryLoading(false);
        });
    }
    return () => {
      requestControllerRef.current?.abort();
      historyControllerRef.current?.abort();
    };
  }, [article.id, applyAnswerResult, loadHistoryPage, open]);

  const requestAnswer = async () => {
    const normalizedQuestion = question.trim();
    if (!normalizedQuestion || phase === "loading") return;

    if (requestQuestionRef.current !== normalizedQuestion) {
      requestQuestionRef.current = normalizedQuestion;
      requestIdRef.current = createArticleQuestionRequestId();
    }
    const requestId = requestIdRef.current ?? createArticleQuestionRequestId();
    requestIdRef.current = requestId;
    const generation = requestGenerationRef.current;

    questionInputRef.current?.focus();
    requestControllerRef.current?.abort();
    const controller = new AbortController();
    requestControllerRef.current = controller;
    setPhase("loading");
    setAnswer("");
    setError("");
    try {
      const result = await askArticle(article.id, normalizedQuestion, controller.signal, requestId);
      applyAnswerResult(result, generation, controller.signal);
    } catch (requestError) {
      if (controller.signal.aborted || generation !== requestGenerationRef.current) return;
      setError(requestError instanceof Error ? requestError.message : "文章问答失败，请重试");
      setPhase("error");
    }
  };

  const loadMoreHistory = async () => {
    if (historyLoading || !historyHasMore) return;
    historyControllerRef.current?.abort();
    const controller = new AbortController();
    historyControllerRef.current = controller;
    setHistoryLoading(true);
    setHistoryError("");
    try {
      await loadHistoryPage(historyOffset, true, controller.signal);
    } catch (historyRequestError) {
      if (controller.signal.aborted) return;
      setHistoryError(
        historyRequestError instanceof Error ? historyRequestError.message : "历史读取失败",
      );
    } finally {
      if (!controller.signal.aborted) setHistoryLoading(false);
    }
  };

  const clearHistory = async (scope: "current" | "all") => {
    if (clearPhase !== "idle") return;
    const message =
      scope === "current" ? "清理当前正文快照的问答历史？" : "清理这篇文章的全部问答历史？";
    if (!window.confirm(message)) return;
    setClearPhase(scope);
    setHistoryError("");
    try {
      await clearArticleAnswerHistory(article.id, scope);
      setHistory([]);
      setHistoryOffset(0);
      setHistoryHasMore(false);
      setAnswer("");
      setPhase("idle");
    } catch (clearError) {
      setHistoryError(clearError instanceof Error ? clearError.message : "历史清理失败");
    } finally {
      setClearPhase("idle");
    }
  };

  const submitQuestion = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void requestAnswer();
  };

  const selectSuggestion = (suggestion: string) => {
    setQuestion(suggestion);
    questionInputRef.current?.focus();
    window.requestAnimationFrame(() => {
      const input = questionInputRef.current;
      if (input && document.activeElement === input) {
        input.setSelectionRange(suggestion.length, suggestion.length);
      }
    });
  };

  return (
    <>
      {open && (
        <button
          type="button"
          className="fixed inset-0 z-40 cursor-default bg-black/20"
          aria-label="关闭提问面板"
          tabIndex={-1}
          onClick={onClose}
        />
      )}
      <aside
        className={`fixed inset-y-0 right-0 z-50 flex w-full max-w-[410px] flex-col border-l border-[#303238] bg-[#1c1e23] text-[#ecece8] shadow-[-18px_0_50px_rgba(0,0,0,0.18)] transition-transform duration-200 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        role="dialog"
        aria-modal={open}
        aria-label="向文章助手提问"
        aria-hidden={!open}
        inert={!open}
      >
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-white/10 px-4">
          <div className="flex items-center gap-2.5">
            <span className="grid size-8 place-items-center rounded-md bg-[#ef8354] text-[#21130d]">
              <Bot size={17} />
            </span>
            <div>
              <h2 className="text-sm font-semibold">文章助手</h2>
              <p className="mt-0.5 text-[10px] text-[#838790]">当前文章</p>
            </div>
          </div>
          <button
            type="button"
            className="grid size-8 place-items-center rounded-md text-[#9699a1] hover:bg-white/10 hover:text-white"
            aria-label="关闭提问面板"
            title="关闭提问面板"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5">
          <div className="border-l-2 border-[#ef8354] pl-3">
            <p className="text-[10px] font-medium text-[#8f939b]">当前上下文</p>
            <h3 className="mt-1.5 text-sm leading-5 font-medium text-[#dedfdb]">{article.title}</h3>
            <p className="mt-1 break-all text-[11px] text-[#858992]">{article.sourceUrl}</p>
          </div>

          {(historyLoading || historyError || history.length > 0) && (
            <section className="mt-6 border-t border-white/10 pt-5">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-xs font-medium text-[#b9bbc0]">历史提问</h3>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    className="grid size-7 place-items-center rounded-md text-[#92959d] hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                    aria-label="清理当前快照问答"
                    title="清理当前快照问答"
                    onClick={() => void clearHistory("current")}
                    disabled={clearPhase !== "idle" || history.length === 0}
                  >
                    <Trash2 size={14} />
                  </button>
                  <button
                    type="button"
                    className="grid size-7 place-items-center rounded-md text-[#92959d] hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                    aria-label="清理全部问答历史"
                    title="清理全部问答历史"
                    onClick={() => void clearHistory("all")}
                    disabled={clearPhase !== "idle" || history.length === 0}
                  >
                    <Trash2 size={14} className="text-[#ef8354]" />
                  </button>
                </div>
              </div>
              {historyError && (
                <p className="mt-2 text-xs leading-5 text-[#efaa8f]">{historyError}</p>
              )}
              <div className="mt-3 divide-y divide-white/10 border-y border-white/10">
                {history.map(item => (
                  <article key={item.requestId} className="py-3">
                    <p className="text-xs font-medium leading-5 text-[#dedfdb]">{item.question}</p>
                    <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-[#aeb1b8]">
                      {item.answer}
                    </p>
                    <time className="mt-2 block text-[10px] text-[#70747c]">
                      {formatAnswerTime(item.createdAt)}
                    </time>
                  </article>
                ))}
              </div>
              {historyHasMore && (
                <button
                  type="button"
                  className="mt-3 flex h-8 w-full items-center justify-center gap-2 rounded-md border border-white/15 text-xs text-[#c7c9cd] hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={() => void loadMoreHistory()}
                  disabled={historyLoading}
                >
                  {historyLoading && <Loader2 size={13} className="animate-spin" />}
                  加载更多
                </button>
              )}
            </section>
          )}

          {phase === "idle" && (
            <div className="mt-7">
              <div className="flex items-center gap-2 text-xs font-medium text-[#b9bbc0]">
                <Sparkles size={14} className="text-[#ef8354]" />
                快速提问
              </div>
              <div className="mt-3 grid gap-2">
                {suggestions.map(suggestion => (
                  <button
                    key={suggestion}
                    type="button"
                    className="rounded-md border border-white/10 bg-white/[0.03] px-3 py-2.5 text-left text-xs text-[#b8bbc1] hover:border-white/20 hover:bg-white/[0.06] hover:text-white"
                    onClick={() => selectSuggestion(suggestion)}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {phase === "loading" && (
            <div className="mt-8 flex items-center gap-3 text-sm text-[#c7c9cd]">
              <Loader2 size={18} className="animate-spin text-[#ef8354]" />
              正在阅读文章
            </div>
          )}

          {phase === "success" && (
            <div className="mt-8 whitespace-pre-wrap text-sm leading-7 text-[#dedfdb]">
              {answer}
            </div>
          )}

          {phase === "error" && (
            <div className="mt-8">
              <div className="flex items-start gap-3 text-sm leading-6 text-[#c7c9cd]">
                <TriangleAlert size={18} className="mt-0.5 shrink-0 text-[#ef8354]" />
                <span>{error}</span>
              </div>
              <button
                type="button"
                className="mt-4 flex h-9 items-center gap-2 rounded-md border border-white/15 px-3 text-xs font-medium text-[#dedfdb] hover:bg-white/[0.06]"
                onClick={() => void requestAnswer()}
              >
                <RotateCw size={14} />
                重试
              </button>
            </div>
          )}
        </div>

        <form className="shrink-0 border-t border-white/10 p-3" onSubmit={submitQuestion}>
          <label className="sr-only" htmlFor="agent-question">
            向文章助手提问
          </label>
          <div className="ask-panel-question-field rounded-lg border border-white/15 bg-[#24272d] p-2 focus-within:border-[#ef8354]/70">
            <textarea
              ref={questionInputRef}
              id="agent-question"
              className="ask-panel-question-input min-h-20 w-full resize-none bg-transparent px-1 py-1 text-sm leading-5 text-white outline-none placeholder:text-[#737780]"
              placeholder="询问当前文章..."
              value={question}
              onChange={event => {
                const nextQuestion = event.target.value;
                if (requestQuestionRef.current !== nextQuestion.trim()) {
                  requestIdRef.current = null;
                }
                setQuestion(nextQuestion);
                if (phase === "success" || phase === "error") setPhase("idle");
              }}
              readOnly={phase === "loading"}
            />
            <div className="mt-1 flex items-center justify-between">
              <span className="px-1 text-[10px] text-[#70747c]">文章上下文</span>
              <button
                type="submit"
                className="grid size-8 place-items-center rounded-md bg-[#ef8354] text-[#21130d] hover:bg-[#f09670] disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="提交问题"
                title="提交问题"
                onMouseDown={event => event.preventDefault()}
                disabled={!question.trim() || phase === "loading"}
              >
                {phase === "loading" ? (
                  <Loader2 size={15} className="animate-spin" />
                ) : (
                  <Send size={15} />
                )}
              </button>
            </div>
          </div>
        </form>
      </aside>
    </>
  );
}

function appendArticleAnswerHistory(
  current: ArticleAnswer[],
  incoming: ArticleAnswer[],
  articleId: string,
  snapshotId: string,
): ArticleAnswer[] {
  const byRequestId = new Map<string, ArticleAnswer>();
  [...incoming, ...current]
    .filter(item => item.articleId === articleId && item.snapshotId === snapshotId)
    .forEach(item => {
      if (!byRequestId.has(item.requestId)) byRequestId.set(item.requestId, item);
    });
  return [...byRequestId.values()].sort((left, right) =>
    right.createdAt.localeCompare(left.createdAt),
  );
}

function formatAnswerTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
