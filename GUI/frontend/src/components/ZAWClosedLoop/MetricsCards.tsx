import type { ZAWClosedLoopRunSummary } from "./types";

type MetricsCardsProps = {
  summary: ZAWClosedLoopRunSummary | undefined;
  busy: boolean;
};

const formatPercent = (value: number | null) =>
  value === null ? "—" : `${(value * 100).toFixed(3)}%`;

const formatInteger = (value: number | null) =>
  value === null ? "—" : String(value);

export function MetricsCards({ summary, busy }: MetricsCardsProps) {
  if (!summary) {
    return (
      <div className="zaw-metrics-grid">
        {[
          { label: "达标", value: "—" },
          { label: "完成状态", value: busy ? "运行中" : "—", tone: busy ? "work" : undefined },
          { label: "最佳迭代", value: "—" },
          { label: "最佳相对 RMS", value: "—" },
        ].map((item) => (
          <div className="zaw-metric" key={item.label}>
            <small>{item.label}</small>
            <span className={`zaw-metric-value ${item.tone ? `tone-${item.tone}` : ""}`}>{item.value}</span>
          </div>
        ))}
      </div>
    );
  }
  const reachedTone = summary.target_reached ? "ok" : "bad";
  const completionTone =
    summary.completion_status === "completed"
      ? "ok"
      : summary.completion_status === "failed"
        ? "bad"
        : summary.completion_status === "cancelled"
          ? "work"
          : undefined;
  return (
    <div className="zaw-metrics-grid">
      <div className="zaw-metric">
        <small>达标</small>
        <span className={`zaw-metric-value tone-${reachedTone}`}>
          {summary.target_reached ? "已达标" : "未达标"}
        </span>
      </div>
      <div className="zaw-metric">
        <small>完成状态</small>
        <span className={`zaw-metric-value ${completionTone ? `tone-${completionTone}` : ""}`}>
          {summary.completion_status}
        </span>
      </div>
      <div className="zaw-metric">
        <small>最佳迭代</small>
        <span className="zaw-metric-value">{formatInteger(summary.best_iteration === null ? null : summary.best_iteration + 1)}</span>
      </div>
      <div className="zaw-metric">
        <small>最佳相对 RMS</small>
        <span className="zaw-metric-value">{formatPercent(summary.best_relative_rms_error)}</span>
      </div>
    </div>
  );
}
