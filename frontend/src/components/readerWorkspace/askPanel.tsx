import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  Bot,
  Check,
  Link2,
  Loader2,
  RotateCw,
  Send,
  Sparkles,
  TriangleAlert,
  X,
} from "lucide-react";

import {
  askArticle,
  askArticleContext,
  confirmArticleContext,
  resolveArticleContext,
} from "../../api/client";
import { useOverlayDialog } from "../../hooks/useOverlayDialog";
import type { Article, ArticleContext } from "../../types";

type AskPanelProps = {
  article: Article;
  open: boolean;
  onClose: () => void;
};

type AnswerPhase = "idle" | "loading" | "success" | "error";
type ContextPhase = "idle" | "loading" | "ready" | "confirming" | "confirmed" | "error";

const suggestions = ["总结核心观点", "列出待验证断言", "这对产品团队意味着什么？"];

export function AskPanel({ article, open, onClose }: AskPanelProps) {
  const questionInputRef = useRef<HTMLTextAreaElement>(null);
  const [question, setQuestion] = useState("");
  const [phase, setPhase] = useState<AnswerPhase>("idle");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [url, setUrl] = useState("");
  const [context, setContext] = useState<ArticleContext | null>(null);
  const [contextPhase, setContextPhase] = useState<ContextPhase>("idle");
  const [contextError, setContextError] = useState("");
  const requestControllerRef = useRef<AbortController | null>(null);
  const contextControllerRef = useRef<AbortController | null>(null);

  useOverlayDialog(open, onClose, questionInputRef);

  useEffect(() => {
    requestControllerRef.current?.abort();
    contextControllerRef.current?.abort();
    setQuestion("");
    setPhase("idle");
    setAnswer("");
    setError("");
    setUrl("");
    setContext(null);
    setContextPhase("idle");
    setContextError("");
    return () => {
      requestControllerRef.current?.abort();
      contextControllerRef.current?.abort();
    };
  }, [article.id, open]);

  const resolveUrl = async () => {
    const normalizedUrl = url.trim();
    if (!normalizedUrl || contextPhase === "loading" || contextPhase === "confirming") return;

    requestControllerRef.current?.abort();
    contextControllerRef.current?.abort();
    const controller = new AbortController();
    contextControllerRef.current = controller;
    setContextPhase("loading");
    setContext(null);
    setContextError("");
    setPhase("idle");
    setAnswer("");
    setError("");
    try {
      const result = await resolveArticleContext(normalizedUrl, controller.signal);
      if (controller.signal.aborted) return;
      setContext(result);
      setContextPhase("ready");
    } catch (requestError) {
      if (controller.signal.aborted) return;
      setContextError(
        requestError instanceof Error ? requestError.message : "文章解析失败，请重试",
      );
      setContextPhase("error");
    }
  };

  const confirmUrl = async () => {
    if (!context || contextPhase === "confirming") return;

    contextControllerRef.current?.abort();
    const controller = new AbortController();
    contextControllerRef.current = controller;
    setContextPhase("confirming");
    setContextError("");
    try {
      const result = await confirmArticleContext(context.contextId, controller.signal);
      if (controller.signal.aborted) return;
      setContext(result);
      setContextPhase("confirmed");
    } catch (requestError) {
      if (controller.signal.aborted) return;
      setContextError(
        requestError instanceof Error ? requestError.message : "文章确认失败，请重试",
      );
      setContextPhase("error");
    }
  };

  const requestAnswer = async () => {
    const normalizedQuestion = question.trim();
    const confirmedContext = context?.confirmed ? context : null;
    if (!normalizedQuestion || phase === "loading") return;
    if (url.trim() && !confirmedContext) return;

    requestControllerRef.current?.abort();
    const controller = new AbortController();
    requestControllerRef.current = controller;
    setPhase("loading");
    setAnswer("");
    setError("");
    try {
      const result = confirmedContext
        ? await askArticleContext(confirmedContext.contextId, normalizedQuestion, controller.signal)
        : await askArticle(article.id, normalizedQuestion, controller.signal);
      if (controller.signal.aborted) return;
      setAnswer(result.answer);
      setPhase("success");
    } catch (requestError) {
      if (controller.signal.aborted) return;
      setError(requestError instanceof Error ? requestError.message : "文章问答失败，请重试");
      setPhase("error");
    }
  };

  const submitQuestion = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void requestAnswer();
  };

  const usingUrlContext = Boolean(url.trim() || context);
  const questionEnabled = !usingUrlContext || context?.confirmed === true;

  return (
    <>
      {open && (
        <button
          type="button"
          className="fixed inset-0 z-40 cursor-default bg-black/20"
          aria-label="关闭提问面板"
          onClick={onClose}
        />
      )}
      <aside
        className={`fixed inset-y-0 right-0 z-50 flex w-full max-w-[410px] flex-col border-l border-[#303238] bg-[#1c1e23] text-[#ecece8] shadow-[-18px_0_50px_rgba(0,0,0,0.18)] transition-transform duration-200 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        aria-label="向文章助手提问"
        aria-hidden={!open}
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
            <h3 className="mt-1.5 text-sm leading-5 font-medium text-[#dedfdb]">
              {context?.title ?? article.title}
            </h3>
            <p className="mt-1 break-all text-[11px] text-[#858992]">
              {context?.sourceUrl ?? article.source}
            </p>
          </div>

          <div className="mt-6 border-t border-white/10 pt-5">
            <label className="text-[10px] font-medium text-[#8f939b]" htmlFor="article-url">
              文章 URL
            </label>
            <div className="mt-2 flex gap-2">
              <input
                id="article-url"
                className="min-w-0 flex-1 rounded-md border border-white/15 bg-[#24272d] px-3 py-2 text-xs text-white outline-none placeholder:text-[#737780] focus:border-[#ef8354]/70"
                placeholder="粘贴公开文章 URL"
                value={url}
                onChange={event => {
                  contextControllerRef.current?.abort();
                  setUrl(event.target.value);
                  setContext(null);
                  setContextPhase("idle");
                  setContextError("");
                  setPhase("idle");
                  setAnswer("");
                  setError("");
                }}
                disabled={contextPhase === "loading" || contextPhase === "confirming"}
              />
              <button
                type="button"
                className="grid size-9 shrink-0 place-items-center rounded-md border border-white/15 text-[#dedfdb] hover:border-white/30 hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="解析文章 URL"
                title="解析文章 URL"
                onClick={() => void resolveUrl()}
                disabled={
                  !url.trim() || contextPhase === "loading" || contextPhase === "confirming"
                }
              >
                {contextPhase === "loading" ? (
                  <Loader2 size={15} className="animate-spin" />
                ) : (
                  <Link2 size={15} />
                )}
              </button>
            </div>

            {(contextPhase === "ready" ||
              contextPhase === "confirming" ||
              contextPhase === "confirmed" ||
              (contextPhase === "error" && context !== null)) &&
              context && (
                <div className="mt-3 border border-white/10 bg-white/[0.03] p-3">
                  <p className="text-xs leading-5 text-[#dedfdb]">{context.title}</p>
                  {context.confirmed ? (
                    <span className="mt-2 flex items-center gap-1.5 text-[10px] text-[#9dc5a6]">
                      <Check size={13} />
                      已确认
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="mt-3 flex h-8 items-center gap-1.5 rounded-md bg-[#ef8354] px-3 text-xs font-medium text-[#21130d] hover:bg-[#f09670] disabled:cursor-not-allowed disabled:opacity-50"
                      onClick={() => void confirmUrl()}
                      disabled={contextPhase === "confirming"}
                    >
                      {contextPhase === "confirming" && (
                        <Loader2 size={13} className="animate-spin" />
                      )}
                      确认文章
                    </button>
                  )}
                </div>
              )}

            {contextPhase === "error" && (
              <div className="mt-3 flex items-start gap-2 text-xs leading-5 text-[#c7c9cd]">
                <TriangleAlert size={15} className="mt-0.5 shrink-0 text-[#ef8354]" />
                <span>{contextError}</span>
              </div>
            )}
          </div>

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
                    onClick={() => setQuestion(suggestion)}
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
          <div className="rounded-lg border border-white/15 bg-[#24272d] p-2 focus-within:border-[#ef8354]/70">
            <textarea
              ref={questionInputRef}
              id="agent-question"
              className="min-h-20 w-full resize-none bg-transparent px-1 py-1 text-sm leading-5 text-white outline-none placeholder:text-[#737780]"
              placeholder="询问当前文章..."
              value={question}
              onChange={event => {
                setQuestion(event.target.value);
                if (phase === "success" || phase === "error") setPhase("idle");
              }}
              disabled={phase === "loading" || !questionEnabled}
            />
            <div className="mt-1 flex items-center justify-between">
              <span className="px-1 text-[10px] text-[#70747c]">文章上下文</span>
              <button
                type="submit"
                className="grid size-8 place-items-center rounded-md bg-[#ef8354] text-[#21130d] hover:bg-[#f09670] disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="提交问题"
                title="提交问题"
                disabled={!question.trim() || phase === "loading" || !questionEnabled}
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
