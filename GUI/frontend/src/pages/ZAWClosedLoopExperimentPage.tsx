import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";
import { MetricsCards } from "../components/ZAWClosedLoop/MetricsCards";
import type { ZAWClosedLoopRunSummary } from "../components/ZAWClosedLoop/types";

function Result({ summary }: { summary: WorkflowRunSummary }) {
  const closedLoop = summary as WorkflowRunSummary & ZAWClosedLoopRunSummary;
  const artifacts = summary.artifacts ?? {};
  const calibration = closedLoop.static_calibration ?? {};
  const plots: Record<string, string> = { "convergence.png": "收敛曲线", "waveform_comparison.png": "波形对比" };
  return <div className="zaw-latest">
    <MetricsCards summary={closedLoop} busy={false} />
    <p>{summary.run_id} · {closedLoop.stop_reason} · 分析：{summary.analysis_status}</p>
    <p>迭代数：{closedLoop.iteration_count ?? "—"} · 阈值：{typeof closedLoop.target_relative_rms === "number"
      ? `${(closedLoop.target_relative_rms * 100).toFixed(2)}%` : "—"}</p>
    {Object.keys(calibration).length > 0 && <details className="zaw-calibration">
      <summary>静态标定信息</summary><pre style={{ overflowX: "auto" }}>{JSON.stringify(calibration, null, 2)}</pre>
    </details>}
    {summary.analysis_error && <p role="alert">{summary.analysis_error}</p>}
    {!Object.keys(artifacts).length && <p>本次运行暂无结果文件。</p>}
    {Object.entries(artifacts).filter(([name]) => /\.(png|jpe?g|svg|webp)$/i.test(name)).map(([name, url]) =>
      <figure className="zaw-figure" key={name}><figcaption>{plots[name] ?? name}</figcaption><img src={url} alt={plots[name] ?? name} loading="lazy" /></figure>)}
    <p className="xy-artifact-links">{Object.entries(artifacts).map(([name, url]) =>
      <a key={name} href={url} download>{name}</a>)}</p>
  </div>;
}
const renderResult = (summary: WorkflowRunSummary) => <Result summary={summary} />;

export function ZAWClosedLoopExperimentPage() {
  return <WorkflowExperimentPage experimentId="z-aw-closed-loop-waveform-correction"
    finishStepId="A06" runStepId="A05" renderResult={renderResult} />;
}
