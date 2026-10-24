import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";

type Decision = {
  baseline_median?: number | null; baseline_drift_ratio?: number | null; baseline_drift_warning?: boolean;
  baseline_valid_count?: number; recommendation?: string; source?: string;
  best_run?: { parameter: string; value: number; sens_flat_fT_per_sqrt_Hz: number } | null;
};
const number = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "—" : value.toPrecision(5);
const labels: Record<string, string> = { completed: "完成", failed: "失败", cancelled: "已取消", running: "运行中", acquired: "等待封存", not_started: "未分析", partial: "部分完成", quality_failed: "质量未通过", unknown: "未记录" };

function Result({ summary }: { summary: WorkflowRunSummary }) {
  const result = (summary as WorkflowRunSummary & { optimization_summary?: Decision }).optimization_summary ?? {};
  const artifacts = summary.artifacts ?? {};
  return <div className="zaw-latest">
    <p>{summary.run_id} · 采集：{labels[summary.completion_status] ?? summary.completion_status} · 分析：{labels[summary.analysis_status] ?? summary.analysis_status}</p>
    {summary.analysis_error && <p role="alert" className="xy-step-error" tabIndex={0}>{summary.analysis_error}</p>}
    {result.source === "legacy_existing_summary" && <p>旧 RAMP 批次：只读取已有单点结论，不转换为 DC 数据，也不重新拟合。</p>}
    <dl><dt>基准中位灵敏度</dt><dd>{number(result.baseline_median)} fT/√Hz</dd>
      <dt>有效基准重复点</dt><dd>{result.baseline_valid_count ?? "—"}</dd>
      <dt>基准漂移</dt><dd>{result.baseline_drift_ratio == null ? "—" : `${(result.baseline_drift_ratio * 100).toFixed(2)}%`}</dd>
      <dt>最佳有效点</dt><dd>{result.best_run ? `${result.best_run.parameter} = ${result.best_run.value}，${number(result.best_run.sens_flat_fT_per_sqrt_Hz)} fT/√Hz` : "无有效点或尚未分析"}</dd>
    </dl>
    {result.baseline_drift_warning && <p role="alert">基准重复点漂移超过 30%，本轮优化可靠性偏低。</p>}
    <p>{result.recommendation ?? "暂无优化结论"}；最佳点仅为建议，不自动写入仪器。</p>
    {Object.entries(artifacts).filter(([name]) => /\.png$/i.test(name)).map(([name, url]) =>
      <figure className="zaw-figure" key={name}><figcaption>{name}</figcaption><img src={url} alt={name} loading="lazy" /></figure>)}
    <p className="xy-artifact-links">{Object.entries(artifacts).map(([name, url]) => <a key={name} href={url} download>{name}</a>)}</p>
  </div>;
}

const renderResult = (summary: WorkflowRunSummary) => <Result summary={summary} />;
const analysisParameterKeys = {};
export function StaticSensitivityOptimizeLocalPage() {
  return <WorkflowExperimentPage experimentId="static-sensitivity-optimize-local" runStepId="A04" finishStepId="A06"
    analysisParameterKeys={analysisParameterKeys} renderResult={renderResult} />;
}
