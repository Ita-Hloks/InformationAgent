import { BarChart3, CircleAlert, Loader2, RotateCw } from "lucide-react";

import type { OpinionReport } from "../../types";

type OpinionAnalysisSectionProps = {
  report: OpinionReport | null;
  loading: boolean;
  starting: boolean;
  error: string | null;
  onRun: () => void;
};

const stanceLabels: Record<string, string> = {
  support: "支持",
  oppose: "反对",
  mixed: "混合",
  unclear: "不明确",
};

const stanceColors: Record<string, string> = {
  support: "bg-emerald-600",
  oppose: "bg-rose-600",
  mixed: "bg-amber-500",
  unclear: "bg-slate-400",
};

export function OpinionAnalysisSection({
  report,
  loading,
  starting,
  error,
  onRun,
}: OpinionAnalysisSectionProps) {
  const active = starting || report?.status === "running";
  const unavailable = !report || report.status === "not_requested";
  const failed = report?.status === "failed";
  const canRun = !active;
  const empty = report !== null && report.collectedCount === 0;
  const showDetails = report !== null && (empty || report.status === "partial");
  const selectedSources =
    report?.sources.filter(
      source => source.decision === "selected" || source.decision === "direct",
    ) ?? [];
  const visibleSources = selectedSources.length > 0 ? selectedSources : (report?.sources ?? []);
  const primaryError = report?.errors[0] ?? null;
  const commentsById = new Map(report?.comments.map(comment => [comment.commentId, comment]) ?? []);

  return (
    <section className="mt-12 border-t border-[var(--reader-workspace-border)] pt-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <BarChart3 size={17} className="text-[#a45132]" />
          <h2 className="text-sm font-semibold text-[#34363a]">舆情分析</h2>
          {report && !unavailable && (
            <span className="text-xs text-[#85878c]">
              {report.statusReason === "sample_empty"
                ? "未取得样本"
                : opinionStatusLabel(report.status)}{" "}
              · 近 {report.windowHours / 24} 天
            </span>
          )}
        </div>
        {canRun && (
          <button
            type="button"
            className="flex h-8 items-center gap-1.5 rounded-md border border-[var(--reader-workspace-border)] bg-white px-2.5 text-xs font-medium text-[#56585d] hover:bg-[var(--reader-workspace-raised)] disabled:cursor-wait disabled:opacity-70"
            disabled={starting}
            onClick={onRun}
          >
            {starting ? <Loader2 size={14} className="animate-spin" /> : <RotateCw size={14} />}
            {unavailable ? "开始分析" : "重新分析"}
          </button>
        )}
      </div>

      {loading && !report && (
        <p className="mt-4 flex items-center gap-2 text-sm text-[#85878c]">
          <Loader2 size={15} className="animate-spin" />
          读取舆情状态中
        </p>
      )}

      {active && (
        <p
          className="mt-4 flex items-center gap-2 text-sm text-[#8a5b3d]"
          role="status"
          aria-live="polite"
        >
          <Loader2 size={15} className="animate-spin" />
          正在分析公开评论样本
        </p>
      )}

      {(error || failed || (report?.status === "partial" && report.errors.length > 0)) && (
        <div
          className="mt-4 flex items-start gap-2 border-l-2 border-[#df8e7a] pl-3 text-sm leading-6 text-[#a33f31]"
          role="alert"
        >
          <CircleAlert size={16} className="mt-1 shrink-0" />
          <div>
            <p>{error ?? primaryError?.message ?? "舆情分析失败"}</p>
            {!error && primaryError && (
              <p className="mt-1 text-xs text-[#9b554a]">
                处理阶段：{opinionStageLabel(primaryError.stage)} ·{" "}
                {opinionStageBoundary(primaryError.stage)}
              </p>
            )}
          </div>
        </div>
      )}

      {report &&
        (report.status === "completed" ||
          report.status === "partial" ||
          report.status === "failed") && (
          <div className="mt-5">
            {!empty && (
              <>
                <div className="grid grid-cols-2 gap-x-5 gap-y-3 border-y border-[var(--reader-workspace-border)] py-4 sm:grid-cols-4">
                  <Metric label="采集评论" value={report.collectedCount} />
                  <Metric label="已分析" value={report.analyzedCount} />
                  <Metric label="已分类" value={report.classifiedCount} />
                  <Metric label="未分类" value={report.unclassifiedCount} />
                </div>
                {report.requestedLimit !== null && (
                  <p className="mt-2 text-xs text-[#85878c]">
                    采集上限 {report.requestedLimit} 条 · 统计仅代表已取得的评论样本
                  </p>
                )}
              </>
            )}
            {empty && report.status !== "failed" && (
              <p className="text-sm leading-7 text-[#5f6064]">{emptyReason(report)}</p>
            )}
            {showDetails && visibleSources.length > 0 && (
              <div className="mt-3 space-y-2">
                {visibleSources.map(source => (
                  <div key={source.url} className="text-xs leading-5 text-[#77797e]">
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[#8a5b3d] hover:underline"
                    >
                      {source.title}
                    </a>
                    <span> · {sourceDetail(source)}</span>
                  </div>
                ))}
              </div>
            )}
            {empty &&
              report.sources.length === 0 &&
              report.statusReason === "sample_empty" &&
              report.referenceStatusReason === null && (
                <p className="mt-2 text-xs text-[#85878c]">本次报告未记录采集明细，请重新分析</p>
              )}
            {report.summary && !empty && (
              <p className="mt-5 text-sm leading-7 text-[#5f6064]">{report.summary}</p>
            )}
            {report.summary && empty && report.statusReason === "no_controversy_points" && (
              <p className="mt-2 text-sm leading-7 text-[#5f6064]">{report.summary}</p>
            )}
            {report.points.length > 0 && (
              <div className="mt-6 space-y-5">
                {report.points.map(point => (
                  <section key={point.evidenceId} className="border-l-2 border-[#d6a270] pl-4">
                    <h3 className="text-sm font-medium leading-6 text-[#3b3d42]">
                      {point.question}
                    </h3>
                    {point.summary && (
                      <p className="mt-2 text-sm leading-6 text-[#66686d]">{point.summary}</p>
                    )}
                    <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#77797e]">
                      {Object.entries(point.stanceCounts).map(([stance, count]) => (
                        <span key={stance}>
                          {stanceLabels[stance] ?? stance} {count}
                        </span>
                      ))}
                    </div>
                    {Object.values(point.stanceCounts).some(count => count > 0) && (
                      <div
                        className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-slate-100"
                        aria-label="评论立场分布"
                      >
                        {Object.entries(point.stanceCounts).map(([stance, count]) => (
                          <span
                            key={stance}
                            className={stanceColors[stance] ?? "bg-slate-400"}
                            style={{
                              width: `${(count / Object.values(point.stanceCounts).reduce((sum, value) => sum + value, 0)) * 100}%`,
                            }}
                          />
                        ))}
                      </div>
                    )}
                    {point.representativeCommentIds.length > 0 && (
                      <div className="mt-4 space-y-3">
                        {point.representativeCommentIds.map(commentId => {
                          const comment = commentsById.get(commentId);
                          if (!comment) return null;
                          return (
                            <blockquote key={commentId} className="border-l border-[#c7c9cc] pl-3">
                              <p className="whitespace-pre-wrap text-sm leading-6 text-[#55575b]">
                                {comment.content}
                              </p>
                              <a
                                href={comment.sourceUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="mt-1 inline-block text-xs text-[#8a5b3d] hover:underline"
                              >
                                {comment.author} · {comment.publishedAt?.slice(0, 10) ?? "时间未知"}{" "}
                                · 赞 {comment.likes}
                              </a>
                            </blockquote>
                          );
                        })}
                      </div>
                    )}
                  </section>
                ))}
              </div>
            )}
            {report.uncertainties.length > 0 && (
              <p className="mt-5 text-xs leading-5 text-[#85878c]">
                {report.uncertainties.join("；")}
              </p>
            )}
          </div>
        )}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <span className="block text-lg font-semibold tabular-nums text-[#34363a]">{value}</span>
      <span className="mt-0.5 block text-xs text-[#85878c]">{label}</span>
    </div>
  );
}

function emptyReason(report: OpinionReport): string {
  if (report.statusReason === "no_controversy_points")
    return "未生成可分析的争议点，因此没有采集评论";
  if (report.referenceStatusReason === "no_candidates") return "视频搜索未返回可用候选视频";
  if (report.referenceStatusReason === "no_matches")
    return `搜索到 ${report.candidateCount} 个候选视频，但没有筛选出内容接近的视频`;
  const selected = report.sources.filter(
    source => source.decision === "selected" || source.decision === "direct",
  );
  if (selected.length > 0) {
    const prefix =
      report.candidateCount > 0
        ? `从 ${report.candidateCount} 个候选视频中筛选出 ${selected.length} 个`
        : `已确定 ${selected.length} 个`;
    return `${prefix}评论来源，近 ${report.windowHours} 小时未取得可分析评论`;
  }
  return report.summary || "未取得可分析评论";
}

function sourceDetail(source: OpinionReport["sources"][number]): string {
  if (source.decision === "selection_failed") return "相关性筛选失败";
  if (source.decision !== "selected" && source.decision !== "direct") {
    return source.selectionReason ? `未选中：${source.selectionReason}` : "未选中";
  }
  if (source.collectionStatus === "failed") {
    return `采集失败：${source.error ?? "原因未记录"}${source.collectedCount > 0 ? `，保留 ${source.collectedCount} 条评论` : ""}`;
  }
  if (source.collectionStatus === "not_attempted") return "未采集";
  if (source.collectionStatus === "no_visible_comments") return "接口未返回可见评论";
  if (source.collectionStatus === "no_parseable_comments") return "接口返回的评论无法解析";
  if (source.collectionStatus === "outside_window") {
    const latest = source.latestVisibleCommentAt?.slice(0, 16).replace("T", " ");
    const start = source.windowStartAt?.slice(0, 16).replace("T", " ");
    return `本次读取的 ${source.visibleReplyCount} 条评论均早于${start ? ` ${start} 的截止时间` : "时间窗"}${latest ? `，最新发布于 ${latest}` : ""}`;
  }
  if (source.collectionStatus === "no_sample_observed")
    return "未取得样本，采集过程未记录更具体原因";
  return `采集 ${source.collectedCount} 条`;
}

function opinionStatusLabel(status: OpinionReport["status"]): string {
  return {
    running: "分析中",
    completed: "已完成",
    partial: "部分完成",
    failed: "分析失败",
    not_requested: "未分析",
  }[status];
}

function opinionStageLabel(stage: string): string {
  return (
    {
      opinion_planning: "文章检索计划",
      video_search: "B站视频搜索",
      video_selection: "视频相关性筛选",
      aid_resolution: "视频标识解析",
      comment_collection: "评论采集",
      opinion_analysis: "观点生成与评论分类",
      classification: "评论分类",
    }[stage] ?? stage
  );
}

function opinionStageBoundary(stage: string): string {
  return (
    {
      opinion_planning: "尚未搜索视频或采集评论",
      video_search: "尚未筛选视频或采集评论",
      video_selection: "尚未采集评论或生成观点",
      aid_resolution: "尚未读取评论或生成观点",
      comment_collection: "已停止评论采集，未进入观点生成",
      opinion_analysis: "评论采集已完成，部分或全部观点未生成",
      classification: "观点生成已完成，评论分类未完成",
    }[stage] ?? "后续阶段未完成"
  );
}
