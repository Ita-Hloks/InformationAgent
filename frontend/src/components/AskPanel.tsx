import { type FormEvent, useEffect, useState } from "react";
import { Bot, LoaderCircle, Quote, Send, Sparkles, X } from "lucide-react";

import type { Article } from "../types";

type AskPanelProps = {
  article: Article;
  open: boolean;
  onClose: () => void;
};

type AnswerPhase = "idle" | "thinking" | "ready";

const suggestions = ["总结核心观点", "列出待验证断言", "这对产品团队意味着什么？"];

export function AskPanel({ article, open, onClose }: AskPanelProps) {
  const [question, setQuestion] = useState("");
  const [phase, setPhase] = useState<AnswerPhase>("idle");

  useEffect(() => {
    setQuestion("");
    setPhase("idle");
  }, [article.id]);

  const submitQuestion = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!question.trim() || phase === "thinking") return;

    setPhase("thinking");
    window.setTimeout(() => setPhase("ready"), 850);
  };

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
        aria-label="向 Information Agent 提问"
        aria-hidden={!open}
      >
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-white/10 px-4">
          <div className="flex items-center gap-2.5">
            <span className="grid size-8 place-items-center rounded-md bg-[#ef8354] text-[#21130d]">
              <Bot size={17} />
            </span>
            <div>
              <h2 className="text-sm font-semibold">Ask IA</h2>
              <p className="mt-0.5 text-[10px] text-[#838790]">基于当前文章 · 本地演示</p>
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
            <p className="mt-1 text-[11px] text-[#858992]">{article.source}</p>
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

          {phase === "thinking" && (
            <div className="mt-8 flex items-center gap-3 text-sm text-[#aeb1b7]">
              <LoaderCircle size={18} className="animate-spin text-[#ef8354]" />
              正在读取文章并整理证据...
            </div>
          )}

          {phase === "ready" && (
            <div className="mt-7">
              <div className="flex items-center gap-2 text-[11px] font-medium text-[#77c69d]">
                <span className="size-1.5 rounded-full bg-[#77c69d]" />
                已生成 · 2 条来源绑定
              </div>
              <div className="mt-4 space-y-4 text-[13px] leading-6 text-[#c7c9cd]">
                <p>
                  这篇文章的核心判断是：模型能力正在从唯一竞争变量变成基础条件，产品差异会更多落在推理成本、延迟和失败处理上。
                </p>
                <p>
                  对产品团队而言，这意味着普通代码需要承担路由、验证和状态管理，模型只处理真正需要语义判断的环节。
                </p>
              </div>
              <div className="mt-6 border-t border-white/10 pt-4">
                <div className="flex items-center gap-2 text-[10px] font-medium text-[#888c94]">
                  <Quote size={13} />
                  引用
                </div>
                <div className="mt-2 space-y-2">
                  <button
                    type="button"
                    className="block w-full rounded-md bg-white/[0.04] px-3 py-2 text-left text-[11px] leading-4 text-[#aeb1b7] hover:bg-white/[0.07]"
                  >
                    [01] “推理成本不再只是财务报表里的数字，而是体验的一部分。”
                  </button>
                  <button
                    type="button"
                    className="block w-full rounded-md bg-white/[0.04] px-3 py-2 text-left text-[11px] leading-4 text-[#aeb1b7] hover:bg-white/[0.07]"
                  >
                    [02] 当前文章正文 · 第 3 段
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>

        <form className="shrink-0 border-t border-white/10 p-3" onSubmit={submitQuestion}>
          <label className="sr-only" htmlFor="agent-question">
            向 Information Agent 提问
          </label>
          <div className="rounded-lg border border-white/15 bg-[#24272d] p-2 focus-within:border-[#ef8354]/70">
            <textarea
              id="agent-question"
              className="min-h-20 w-full resize-none bg-transparent px-1 py-1 text-sm leading-5 text-white outline-none placeholder:text-[#737780]"
              placeholder="询问当前文章..."
              value={question}
              onChange={event => {
                setQuestion(event.target.value);
                if (phase === "ready") setPhase("idle");
              }}
            />
            <div className="mt-1 flex items-center justify-between">
              <span className="px-1 text-[10px] text-[#70747c]">文章上下文</span>
              <button
                type="submit"
                className="grid size-8 place-items-center rounded-md bg-[#ef8354] text-[#21130d] hover:bg-[#f09670] disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="提交问题"
                title="提交问题"
                disabled={!question.trim() || phase === "thinking"}
              >
                <Send size={15} />
              </button>
            </div>
          </div>
        </form>
      </aside>
    </>
  );
}
