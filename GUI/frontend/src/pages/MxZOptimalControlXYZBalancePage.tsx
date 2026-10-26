import { useEffect, useState } from "react";
import { api } from "../api";
import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";

const ID = "mx-z-optimal-control-xyz-balance";

function Result({ value }: { value: WorkflowRunSummary }) {
  const artifacts = value.artifacts ?? {};
  const [report, setReport] = useState<Record<string, unknown>>();
  const [error, setError] = useState("");
  const reportUrl = artifacts["results/analysis.yaml"];
  useEffect(() => {
    let disposed = false;
    setReport(undefined); setError("");
    if (reportUrl || value.analysis_error) void api<Record<string, unknown>>(
      `/api/experiments/${ID}/runs/${encodeURIComponent(value.run_id)}/result`,
    ).then(result => { if (!disposed) setReport(result); })
      .catch(reason => { if (!disposed) setError(`三维平衡汇总加载失败：${String(reason)}`); });
    return () => { disposed = true; };
  }, [reportUrl, value.run_id, value.analysis_error]);
  return <div className="zaw-latest">
    <p>{value.run_id} · {value.timestamp} · 采集：{value.completion_status} · 分析：{value.analysis_status}</p>
    {value.analysis_error && <p role="alert">{value.analysis_error}</p>}
    {error && <p role="alert">{error}</p>}
    {report && <details open><summary>实测三维网格最小点与逐层 V 形拟合诊断（非绝对磁场标定）</summary>
      <pre style={{ maxHeight: 360, overflow: "auto", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
        {JSON.stringify(report, null, 2)}
      </pre>
    </details>}
    {!Object.keys(artifacts).length && <p>本次运行暂无结果文件。</p>}
    {Object.entries(artifacts).filter(([name]) => name.endsWith(".png")).map(([name, url]) =>
      <figure className="zaw-figure" key={name}><figcaption>{name}</figcaption><img src={url} alt={name} loading="lazy" /></figure>)}
    <p className="xy-artifact-links">{Object.entries(artifacts).map(([name, url]) =>
      <a key={name} href={url} download>{name}</a>)}</p>
  </div>;
}

export function MxZOptimalControlXYZBalancePage() {
  return <WorkflowExperimentPage experimentId={ID}
    analysisParameterPrefix="" renderResult={value => <Result value={value} />} />;
}
