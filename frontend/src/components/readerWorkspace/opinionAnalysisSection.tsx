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
  const canRun = unavailable || failed;

  return (
    <section className="mt-12 border-t border-[var(--reader-workspace-border)] pt-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <BarChart3 size={17} className="text-[#a45132]" />
          <h2 className="text-sm font-semibold text-[#34363a]">舆情分析</h2>
          {report && !unavailable && (
            <span className="text-xs text-[#85878c]">
              {opinionStatusLabel(report.status)} · {report.windowHours} 小时
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
            {failed ? "重新分析" : "开始分析"}
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

      {(error || failed) && (
        <div
          className="mt-4 flex items-start gap-2 border-l-2 border-[#df8e7a] pl-3 text-sm leading-6 text-[#a33f31]"
          role="alert"
        >
          <CircleAlert size={16} className="mt-1 shrink-0" />
          <p>{error ?? report?.errors[0]?.message ?? "舆情分析失败"}</p>
        </div>
      )}

      {report && (report.status === "completed" || report.status === "partial") && (
        <div className="mt-5">
          <div className="grid grid-cols-2 gap-x-5 gap-y-3 border-y border-[var(--reader-workspace-border)] py-4 sm:grid-cols-4">
            <Metric label="采集评论" value={report.collectedCount} />
            <Metric label="已分析" value={report.analyzedCount} />
            <Metric label="已分类" value={report.classifiedCount} />
            <Metric label="未分类" value={report.unclassifiedCount} />
          </div>
          {report.summary && (
            <p className="mt-5 text-sm leading-7 text-[#5f6064]">{report.summary}</p>
          )}
          {report.points.length > 0 && (
            <div className="mt-6 space-y-5">
              {report.points.map(point => (
                <section key={point.evidenceId} className="border-l-2 border-[#d6a270] pl-4">
                  <h3 className="text-sm font-medium leading-6 text-[#3b3d42]">{point.question}</h3>
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

function opinionStatusLabel(status: OpinionReport["status"]): string {
  return {
    running: "分析中",
    completed: "已完成",
    partial: "部分完成",
    failed: "分析失败",
    not_requested: "未分析",
  }[status];
}
