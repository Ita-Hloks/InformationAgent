import { type FormEvent, useEffect, useMemo, useState } from "react";
import {
  Bot,
  ChevronRight,
  ExternalLink,
  FlaskConical,
  Loader2,
  Play,
  RotateCw,
  Square,
} from "lucide-react";

import type {
  AgentReport,
  AgentTaskSnapshot,
  ResearchIngestResult,
  ResearchRun,
} from "../../types";

type ResearchWorkspaceProps = {
  runs: ResearchRun[];
  selectedRunId: string | null;
  ingestResult: ResearchIngestResult | null;
  agentReport: AgentReport | null;
  agentTask: AgentTaskSnapshot | null;
  phase: "idle" | "ingesting" | "running-agent";
  error: string | null;
  onCreateRun: (input: {
    topic: string;
    feeds: string[];
    timeoutSeconds: number;
    limit: number;
  }) => Promise<void>;
  onRunAgent: (runId: string) => Promise<void>;
  onStopAgent: (runId: string) => Promise<void>;
  onSelectRun: (runId: string) => void;
  onRefreshRuns: () => Promise<void>;
};

const defaultFeeds = "https://www.geekpark.net/rss";

export function ResearchWorkspace({
  runs,
  selectedRunId,
  ingestResult,
  agentReport,
  agentTask,
  phase,
  error,
  onCreateRun,
  onRunAgent,
  onStopAgent,
  onSelectRun,
  onRefreshRuns,
}: ResearchWorkspaceProps) {
  const [topic, setTopic] = useState("AI");
  const [feeds, setFeeds] = useState(defaultFeeds);
  const [limit, setLimit] = useState(3);
  const [timeoutSeconds, setTimeoutSeconds] = useState(180);
  const [runFilter, setRunFilter] = useState<"all" | ResearchRun["mode"]>("all");

  const selectedRun = useMemo(
    () => runs.find(run => run.id === selectedRunId) ?? null,
    [runs, selectedRunId],
  );
  const activeRunId = ingestResult?.run_id ?? selectedRunId;
  const activeEvidenceCount = ingestResult?.articles.length ?? selectedRun?.articleCount ?? 0;
  const busy = phase !== "idle";
  const agentActive = Boolean(agentTask && ["created", "running"].includes(agentTask.status));
  const canRunAgent = Boolean(activeRunId) && activeEvidenceCount > 0 && !agentActive;
  const selectedRunTitle = selectedRun?.title;
  const visibleRuns = useMemo(
    () => (runFilter === "all" ? runs : runs.filter(run => run.mode === runFilter)),
    [runFilter, runs],
  );

  useEffect(() => {
    if (!selectedRunTitle || ingestResult) return;
    // Reuse the selected run's topic when starting a follow-up collection.
    setTopic(selectedRunTitle);
  }, [ingestResult, selectedRunTitle]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const feedList = feeds
      .split(/\r?\n/)
      .map(item => item.trim())
      .filter(Boolean);
    await onCreateRun({ topic: topic.trim(), feeds: feedList, timeoutSeconds, limit });
  };

  return (
    <section className="grid h-full min-h-0 overflow-hidden grid-cols-1 bg-[#f5f5f1] text-[#242528] lg:grid-cols-[390px_minmax(0,1fr)]">
      <div className="workspace-scroll min-h-0 overflow-y-auto border-r border-[#ddded8] px-5 py-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold">研究运行</h1>
            <p className="mt-1 text-xs text-[#73767b]">采集 RSS 证据并启动受限搜索 Agent</p>
          </div>
          <button
            type="button"
            className="grid size-8 place-items-center rounded-md border border-[#d5d6d0] bg-white text-[#5f6369] hover:bg-[#ecece7]"
            aria-label="刷新研究记录"
            title="刷新研究记录"
            onClick={() => void onRefreshRuns()}
          >
            <RotateCw size={15} />
          </button>
        </div>

        <form className="mt-5 space-y-4" onSubmit={submit}>
          <label className="block">
            <span className="text-xs font-medium text-[#62656b]">主题</span>
            <input
              className="mt-1 h-9 w-full rounded-md border border-[#d3d4ce] bg-white px-3 text-sm outline-none focus:border-[#3978a8]"
              value={topic}
              onChange={event => setTopic(event.target.value)}
            />
          </label>

          <label className="block">
            <span className="text-xs font-medium text-[#62656b]">RSS / Atom 地址</span>
            <textarea
              className="mt-1 min-h-24 w-full resize-y rounded-md border border-[#d3d4ce] bg-white px-3 py-2 text-sm leading-5 outline-none focus:border-[#3978a8]"
              value={feeds}
              onChange={event => setFeeds(event.target.value)}
            />
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-xs font-medium text-[#62656b]">文章上限</span>
              <input
                className="mt-1 h-9 w-full rounded-md border border-[#d3d4ce] bg-white px-3 text-sm outline-none focus:border-[#3978a8]"
                type="number"
                min={1}
                max={20}
                value={limit}
                onChange={event => setLimit(Number(event.target.value))}
              />
            </label>
            <label className="block">
              <span className="text-xs font-medium text-[#62656b]">超时秒数</span>
              <input
                className="mt-1 h-9 w-full rounded-md border border-[#d3d4ce] bg-white px-3 text-sm outline-none focus:border-[#3978a8]"
                type="number"
                min={1}
                max={600}
                value={timeoutSeconds}
                onChange={event => setTimeoutSeconds(Number(event.target.value))}
              />
            </label>
          </div>

          <button
            type="submit"
            className="flex h-10 w-full items-center justify-center gap-2 rounded-md bg-[#3978a8] px-3 text-sm font-medium text-white hover:bg-[#2f6d9c] disabled:cursor-not-allowed disabled:opacity-60"
            disabled={busy || !topic.trim() || !feeds.trim()}
          >
            {phase === "ingesting" ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Play size={16} />
            )}
            开始采集入库
          </button>
        </form>

        {error && (
          <div className="mt-4 rounded-md border border-[#e6b7a5] bg-[#fff2ec] px-3 py-2 text-xs leading-5 text-[#8a3e24]">
            {error}
          </div>
        )}

        <div className="mt-6">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">最近运行</h2>
            <span className="text-xs text-[#777a80]">{visibleRuns.length}</span>
          </div>
          <div className="mb-3 grid grid-cols-3 rounded-md border border-[#d5d6d0] bg-white p-0.5">
            {(
              [
                ["all", "全部"],
                ["auto", "自动"],
                ["manual", "手动"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={`h-8 rounded text-xs font-medium ${
                  runFilter === value
                    ? "bg-[#3978a8] text-white"
                    : "text-[#62656b] hover:bg-[#ecece7]"
                }`}
                onClick={() => setRunFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="space-y-2">
            {visibleRuns.map(run => (
              <button
                key={run.id}
                type="button"
                className={`w-full rounded-md border px-3 py-2 text-left ${
                  selectedRunId === run.id
                    ? "border-[#3978a8] bg-white"
                    : "border-[#dedfd9] bg-white/70 hover:bg-white"
                }`}
                onClick={() => onSelectRun(run.id)}
              >
                <div className="flex items-center gap-2">
                  <span className={`size-2 rounded-full ${statusColor(run.status)}`} />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{run.title}</span>
                  <span className="text-xs text-[#73767b]">{run.articleCount} 篇</span>
                </div>
                <div className="mt-1 truncate text-[11px] text-[#85888e]">{run.id}</div>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="workspace-scroll min-h-0 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-3xl">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs font-medium text-[#777a80]">当前运行</p>
              <h2 className="mt-1 text-xl font-semibold">
                {ingestResult?.topic ?? selectedRun?.title ?? "尚未选择运行"}
              </h2>
            </div>
            {activeRunId && (
              <div className="flex items-center gap-2">
                {agentActive && (
                  <button
                    type="button"
                    className="flex h-10 items-center gap-2 rounded-md border border-[#d59c87] bg-white px-4 text-sm font-medium text-[#8a3e24] hover:bg-[#fff2ec] disabled:cursor-not-allowed disabled:opacity-60"
                    onClick={() => void onStopAgent(activeRunId)}
                  >
                    <Square size={15} />
                    停止 Agent
                  </button>
                )}
                <button
                  type="button"
                  className="flex h-10 items-center gap-2 rounded-md bg-[#ef8354] px-4 text-sm font-medium text-[#25140d] hover:bg-[#f09670] disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={busy || !canRunAgent}
                  title={canRunAgent ? "运行 Agent" : "没有入选证据，不能运行 Agent"}
                  onClick={() => void onRunAgent(activeRunId)}
                >
                  {agentActive ? <Loader2 size={16} className="animate-spin" /> : <Bot size={16} />}
                  运行 Agent
                </button>
              </div>
            )}
          </div>

          {ingestResult && (
            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <Metric label="run_id" value={ingestResult.run_id} compact />
              <Metric label="状态" value={ingestResult.status} />
              <Metric label="入选证据" value={`${ingestResult.articles.length} 篇`} />
            </div>
          )}

          {!ingestResult && selectedRun && (
            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <Metric label="run_id" value={selectedRun.id} compact />
              <Metric label="状态" value={selectedRun.status} />
              <Metric label="入选证据" value={`${selectedRun.articleCount} 篇`} />
            </div>
          )}

          {activeRunId && activeEvidenceCount === 0 && (
            <div className="mt-5 rounded-md border border-[#e3d5a8] bg-[#fff9e8] px-4 py-3 text-sm leading-6 text-[#715b20]">
              当前运行没有入选证据。采集可能命中了缓存、没有新文章，或语义筛选未保留候选；
              请换一个主题、RSS 地址，或等待来源更新后重新采集。
            </div>
          )}

          {agentTask && <AgentDiagnostics task={agentTask} />}

          {agentReport ? (
            <div className="mt-6 space-y-4">
              <section className="rounded-md border border-[#d9dad4] bg-white p-4">
                <div className="flex flex-wrap items-center gap-3">
                  <FlaskConical size={18} className="text-[#3978a8]" />
                  <h3 className="text-sm font-semibold">Agent 结论</h3>
                  <span className="rounded bg-[#eef3f6] px-2 py-1 text-xs text-[#4f6574]">
                    {agentReport.status} / {agentReport.stop_reason}
                  </span>
                </div>
                <p className="mt-4 whitespace-pre-wrap text-sm leading-7 text-[#3f4248]">
                  {agentReport.final_answer ?? "Agent 未生成最终结论。"}
                </p>
              </section>

              {agentReport.uncertainties.length > 0 && (
                <section className="rounded-md border border-[#d9dad4] bg-white p-4">
                  <h3 className="text-sm font-semibold">不确定性</h3>
                  <ul className="mt-3 space-y-2 text-sm leading-6 text-[#555960]">
                    {agentReport.uncertainties.map(item => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </section>
              )}

              {agentReport.citations.length > 0 && (
                <section className="rounded-md border border-[#d9dad4] bg-white p-4">
                  <h3 className="text-sm font-semibold">结论引用</h3>
                  <div className="mt-3 space-y-3">
                    {agentReport.citations.map((citation, index) => {
                      const anchors = agentReport.plans.filter(plan =>
                        citation.evidence_ids.includes(plan.evidence_id),
                      );
                      return (
                        <div
                          key={`${citation.claim}-${index}`}
                          className="min-w-0 border-b border-[#ecece7] pb-3 text-sm leading-6 text-[#555960] last:border-b-0 last:pb-0"
                        >
                          <p>{citation.claim}</p>
                          <p className="mt-2 break-words text-xs text-[#85888e]">
                            证据：{citation.evidence_ids.join(", ") || "无"}
                          </p>
                          <div className="mt-2 min-w-0">
                            <p className="text-xs font-medium text-[#73767b]">原文锚点</p>
                            {anchors.length > 0 ? (
                              <ul className="mt-1 space-y-1 text-xs text-[#777a80]">
                                {anchors.map(plan => (
                                  <li key={plan.evidence_id} className="break-words">
                                    {plan.trigger_quote}
                                  </li>
                                ))}
                              </ul>
                            ) : (
                              <p className="mt-1 text-xs text-[#85888e]">无</p>
                            )}
                          </div>
                          <div className="mt-2 min-w-0">
                            <p className="text-xs font-medium text-[#73767b]">搜索来源</p>
                            {citation.source_urls.length > 0 ? (
                              <ul className="mt-1 space-y-1 text-xs">
                                {citation.source_urls.map(sourceUrl => (
                                  <li key={sourceUrl} className="min-w-0">
                                    <a
                                      className="inline-flex max-w-full min-w-0 items-start gap-1 break-all text-[#3978a8] hover:underline"
                                      href={sourceUrl}
                                      target="_blank"
                                      rel="noreferrer"
                                    >
                                      <ExternalLink size={13} className="mt-0.5 shrink-0" />
                                      <span className="min-w-0 break-all">{sourceUrl}</span>
                                    </a>
                                  </li>
                                ))}
                              </ul>
                            ) : (
                              <p className="mt-1 text-xs text-[#85888e]">无</p>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>
              )}

              {agentReport.plans.length > 0 && (
                <section className="rounded-md border border-[#d9dad4] bg-white p-4">
                  <h3 className="text-sm font-semibold">研究问题与查询</h3>
                  <div className="mt-3 space-y-3">
                    {agentReport.plans.map(plan => (
                      <div
                        key={`${plan.evidence_id}-${plan.question}`}
                        className="text-sm leading-6"
                      >
                        <p className="text-[#3f4248]">{plan.question}</p>
                        <p className="mt-1 break-words text-xs text-[#777a80]">
                          原文锚点：{plan.trigger_quote}
                        </p>
                        <ul className="mt-1 space-y-1 text-xs text-[#777a80]">
                          {plan.queries.map(query => (
                            <li key={query.query}>{query.query}</li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {agentReport.answers.length > 0 && (
                <section className="rounded-md border border-[#d9dad4] bg-white p-4">
                  <h3 className="text-sm font-semibold">搜索回答与来源</h3>
                  <div className="mt-3 space-y-4">
                    {agentReport.answers.map(answer => (
                      <div
                        key={`${answer.evidence_id}-${answer.question}`}
                        className="text-sm leading-6"
                      >
                        <p className="text-[#3f4248]">{answer.answer}</p>
                        {answer.sources.length > 0 && (
                          <ul className="mt-1 space-y-1 text-xs text-[#777a80]">
                            {answer.sources.map(source => (
                              <li key={source.url}>
                                <a
                                  className="text-[#3978a8] hover:underline"
                                  href={source.url}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  {source.title || source.url}
                                </a>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {agentReport.errors.length > 0 && (
                <section className="rounded-md border border-[#e6b7a5] bg-[#fff7f3] p-4 text-sm leading-6 text-[#8a3e24]">
                  {agentReport.errors.join("\n")}
                </section>
              )}
            </div>
          ) : (
            <div className="mt-8 rounded-md border border-dashed border-[#d1d2cc] bg-white/60 px-4 py-10 text-center text-sm text-[#777a80]">
              采集入库后点击“运行 Agent”，这里会显示最终回答、引用和不确定性。
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  compact = false,
}: {
  label: string;
  value: string;
  compact?: boolean;
}) {
  return (
    <div className="rounded-md border border-[#d9dad4] bg-white px-3 py-2">
      <p className="text-[11px] font-medium text-[#777a80]">{label}</p>
      <p className={`mt-1 text-sm font-semibold text-[#303238] ${compact ? "truncate" : ""}`}>
        {value}
      </p>
    </div>
  );
}

function AgentDiagnostics({ task }: { task: AgentTaskSnapshot }) {
  return (
    <section className="mt-6 rounded-md border border-[#d9dad4] bg-white p-4">
      <div className="flex min-w-0 items-center gap-3">
        {task.status === "created" || task.status === "running" ? (
          <Loader2 size={18} className="shrink-0 text-[#3978a8]" />
        ) : (
          <FlaskConical size={18} className="shrink-0 text-[#3978a8]" />
        )}
        <h3 className="shrink-0 text-sm font-semibold">Agent 状态</h3>
        <span className="min-w-0 break-words text-xs text-[#73767b]">{task.message}</span>
      </div>

      <dl className="mt-4 grid min-w-0 gap-3 sm:grid-cols-2">
        <DiagnosticField label="request_id" value={task.request_id ?? "无"} />
        <DiagnosticField label="analysis_run_id" value={task.analysis_run_id ?? "无"} />
        <DiagnosticField label="status" value={task.status} />
        <DiagnosticField label="phase" value={task.phase} />
        <DiagnosticField
          label="attempt / max_attempts"
          value={`${task.attempt} / ${task.max_attempts}`}
        />
        <DiagnosticField label="retryable" value={retryableLabel(task.retryable)} />
      </dl>

      {task.error && <DiagnosticError error={task.error} className="mt-4" />}

      <div className="mt-5 border-t border-[#ecece7] pt-4">
        <h4 className="text-sm font-semibold">阶段详情</h4>
        {task.stage_details.length > 0 ? (
          <div className="mt-3 divide-y divide-[#ecece7]">
            {task.stage_details.map(stage => (
              <details key={stage.step_key} className="group py-3 first:pt-0 last:pb-0">
                <summary className="flex min-w-0 cursor-pointer list-none items-center gap-2 text-sm [&::-webkit-details-marker]:hidden">
                  <ChevronRight
                    size={15}
                    className="shrink-0 text-[#73767b] transition-transform group-open:rotate-90"
                  />
                  <span className="min-w-0 flex-1 break-words font-medium text-[#3f4248]">
                    {stage.step_key}
                  </span>
                  <span className="shrink-0 text-xs text-[#73767b]">{stage.status}</span>
                  <span className="shrink-0 text-xs text-[#73767b]">
                    {stage.attempt} / {stage.max_attempts}
                  </span>
                  <span className="shrink-0 text-xs text-[#73767b]">
                    {retryableLabel(stage.retryable)}
                  </span>
                </summary>

                <div className="mt-3 pl-6">
                  {stage.error && <DiagnosticError error={stage.error} />}
                  {stage.attempts.length > 0 && (
                    <div className="space-y-3">
                      {stage.attempts.map(attempt => (
                        <div key={`${stage.step_key}-${attempt.attempt_no}`} className="min-w-0">
                          <dl className="grid min-w-0 gap-2 sm:grid-cols-4">
                            <DiagnosticField label="尝试" value={String(attempt.attempt_no)} />
                            <DiagnosticField label="操作" value={attempt.operation} />
                            <DiagnosticField label="状态" value={attempt.status} />
                            <DiagnosticField
                              label="retryable"
                              value={retryableLabel(attempt.retryable)}
                            />
                          </dl>
                          {attempt.error && (
                            <DiagnosticError error={attempt.error} className="mt-2" />
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </details>
            ))}
          </div>
        ) : (
          <p className="mt-3 text-xs text-[#85888e]">无</p>
        )}
      </div>
    </section>
  );
}

function DiagnosticField({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-medium text-[#777a80]">{label}</dt>
      <dd className="mt-1 min-w-0 break-all text-sm font-semibold text-[#303238]">{value}</dd>
    </div>
  );
}

function DiagnosticError({
  error,
  className = "",
}: {
  error: { type: string; message: string };
  className?: string;
}) {
  return (
    <div
      className={`min-w-0 rounded-md bg-[#fff7f3] px-3 py-2 text-xs leading-5 text-[#8a3e24] ${className}`}
    >
      <p className="break-words">提示：{friendlyDiagnosticMessage(error)}</p>
    </div>
  );
}

const internalErrorPatterns = [
  /Traceback/i,
  /File\s+["'`]/i,
  /\bline \d+\b/i,
  /\bNoneType\b/i,
  /\bnot iterable\b/i,
  /\bsubscriptable\b/i,
  /\bhas no attribute\b/i,
  /\bNameError\b/i,
  /\bTypeError\b/i,
  /\bValueError\b/i,
  /\bRuntimeError\b/i,
  /\bAssertionError\b/i,
];

function friendlyDiagnosticMessage(error: { type: string; message: string }) {
  const message = error.message.trim();
  if (!message) {
    return "Agent 运行失败，请稍后重试";
  }
  if (internalErrorPatterns.some(pattern => pattern.test(message))) {
    return "Agent 运行失败，请稍后重试";
  }
  return message;
}

function retryableLabel(value: boolean | null) {
  if (value === null) return "未知";
  return value ? "是" : "否";
}

function statusColor(status: ResearchRun["status"]) {
  if (status === "completed") return "bg-[#63b68d]";
  if (status === "collecting") return "bg-[#ef8354]";
  if (status === "failed") return "bg-[#b85c4c]";
  return "bg-[#c4a460]";
}
