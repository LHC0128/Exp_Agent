import { useEffect, useState } from "react";
import { api } from "../api";
import { StaticSensitivityHistory } from "../components/StaticSensitivityHistory";
import { historyParameterValues } from "./experimentHelpers";
import { WorkflowExperimentPage, type RenderLatestContext, type WorkflowRunSummary } from "./WorkflowExperimentPage";

const ID = "static-sensitivity";
const ANALYSIS_KEYS = { z_v_to_nt: "z_v_to_nt" };
type StaticSummary = WorkflowRunSummary & { analysis: Record<string, unknown> };

const formatNumber = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toString() : "—";

function LatestPreview({ latest, job, session }: RenderLatestContext) {
  const [summary, setSummary] = useState<StaticSummary>();
  const busy = Boolean(job && ["queued", "running"].includes(job.status));
  const result = job?.result as { run_dir?: unknown } | undefined;
  const runDir = typeof result?.run_dir === "string" ? result.run_dir : "";
  const sessionRunId = session?.steps.A12?.status === "completed" ? session.run_id : undefined;
  const runId = sessionRunId ?? runDir.split(/[\\/]/).pop() ?? "";
  useEffect(() => {
    if (!runId) {
      setSummary(latest as StaticSummary | undefined);
      return;
    }
    let disposed = false;
    void api<StaticSummary>(`/api/experiments/${ID}/runs/${encodeURIComponent(runId)}/summary`)
      .then(value => { if (!disposed) setSummary(value); })
      .catch(() => undefined);
    return () => { disposed = true; };
  }, [job?.id, job?.status, sessionRunId, latest, runId]);
  if (!summary) return <p className="zaw-empty">{busy ? "正在采集，运行结束后会展示结果。" : "尚无运行结果，启动一次实验后这里会展示灵敏度分析。"}</p>;
  const analysis = summary.analysis ?? {};
  return <div className="zaw-latest">
    <div className="zaw-metrics-grid">
      <div className="zaw-metric"><small>完成状态</small><span className="zaw-metric-value">{summary.completion_status}</span></div>
      <div className="zaw-metric"><small>拟合状态</small><span className="zaw-metric-value">{analysis.fit_valid === undefined ? "未分析" : analysis.fit_valid ? "有效" : "无效"}</span></div>
      <div className="zaw-metric"><small>最佳 GS200 量程</small><span className="zaw-metric-value">{String(analysis.best_range_label ?? "—")}</span></div>
      <div className="zaw-metric"><small>平坦段灵敏度</small><span className="zaw-metric-value">{formatNumber(analysis.sensitivity_flat_fT_per_sqrt_Hz)} fT/√Hz</span></div>
    </div>
    <p className="zaw-latest-meta">{summary.timestamp} · {summary.run_tag}</p>
    {typeof analysis.error === "string" && <p className="alert error">{analysis.error}</p>}
    {Array.isArray(analysis.fit_rejection_reasons) && analysis.fit_rejection_reasons.length > 0 && <p className="alert error">{analysis.fit_rejection_reasons.join("；")}</p>}
    {analysis.success !== false && summary.artifacts?.["full_analysiswithoutpump.png"] && <figure className="zaw-figure">
      <figcaption>色散、噪声与灵敏度结果</figcaption>
      <img src={summary.artifacts["full_analysiswithoutpump.png"]} alt="静磁场灵敏度分析结果" loading="lazy" />
    </figure>}
  </div>;
}

export function StaticSensitivityPage() {
  return <WorkflowExperimentPage experimentId={ID} eyebrow="EXPERIMENT · STATIC_SENSITIVITY"
    analysisParameterKeys={ANALYSIS_KEYS}
    renderLatest={context => <LatestPreview {...context} />}
    renderHistory={({ fields, setValues, openRunTab, analysisJob, setAnalysisJob, busy }) =>
      <StaticSensitivityHistory experimentId={ID} fields={fields} busy={busy}
        analysisJob={analysisJob} setAnalysisJob={setAnalysisJob}
        onFillBack={parameters => {
          setValues(current => ({ ...current, ...historyParameterValues(fields, parameters) }));
          openRunTab();
        }} />} />;
}
