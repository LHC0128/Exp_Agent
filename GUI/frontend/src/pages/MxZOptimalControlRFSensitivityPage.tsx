import { useEffect, useState } from "react";
import { api } from "../api";
import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";

const statuses: Record<string, string> = { completed: "完成", quality_failed: "质量未通过", failed: "失败",
  cancelled: "已取消", running: "运行中", acquired: "待封存", partial: "部分完成", not_started: "未开始分步分析" };
const status = (value: string) => statuses[value] ?? value;

function Result({ value }: { value: WorkflowRunSummary }) {
  const artifacts = value.artifacts ?? {};
  const [report, setReport] = useState<Record<string, unknown>>();
  const [error, setError] = useState("");
  const reportUrl = artifacts["results/analysis.yaml"];
  useEffect(() => {
    let disposed = false;
    setReport(undefined); setError("");
    if (reportUrl || value.analysis_error) void api<Record<string, unknown>>(
      `/api/experiments/mx-z-optimal-control-rf-sensitivity/runs/${encodeURIComponent(value.run_id)}/result`,
    ).then(result => { if (!disposed) setReport(result); })
      .catch(reason => { if (!disposed) setError(`科学诊断加载失败：${String(reason)}`); });
    return () => { disposed = true; };
  }, [reportUrl, value.run_id, value.analysis_error]);
  return <div className="zaw-latest">
    <p>{value.run_id} · {value.timestamp} · 采集：{status(value.completion_status)} · 分析：{status(value.analysis_status)}</p>
    {value.analysis_error && <p role="alert">{value.analysis_error}</p>}
    {error && <p role="alert">{error}</p>}
    {report && <details open><summary>校相、拟合、灵敏度与剩磁诊断</summary>
      <pre style={{ maxHeight: 360, overflow: "auto", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
        {JSON.stringify(report, null, 2)}
      </pre>
    </details>}
    {!Object.keys(artifacts).length && <p>本次运行暂无结果文件。</p>}
    {Object.entries(artifacts).filter(([name]) => name.endsWith(".png")).map(([name, url]) =>
      <figure className="zaw-figure" key={name}><figcaption>{name}</figcaption><img src={url} alt={name} loading="lazy" /></figure>)}
    <p className="xy-artifact-links">{Object.entries(artifacts).map(([name, url]) => <a key={name} href={url} download>{name}</a>)}</p>
  </div>;
}

export function MxZOptimalControlRFSensitivityPage() {
  return <WorkflowExperimentPage experimentId="mx-z-optimal-control-rf-sensitivity" finishStepId="A12" runStepId="A04"
    renderResult={value => <Result value={value} />} />;
}
