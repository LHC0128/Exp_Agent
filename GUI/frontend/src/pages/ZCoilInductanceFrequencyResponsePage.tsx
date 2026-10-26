import { useEffect, useState } from "react";
import { api } from "../api";
import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";

type FrequencyReport = { capture_count: number; frequency_count: number; theory_fit: {
  available: boolean; reason?: string; amplitude_zero_frequency_v?: number;
  amplitude_high_frequency_v?: number; corner_frequency_hz?: number; r_squared?: number;
} };
const statusLabel = (value: string) => ({ completed: "完成", failed: "失败", cancelled: "已取消",
  running: "运行中", not_started: "未分析", quality_failed: "质量未通过", stale: "需重算",
} as Record<string, string>)[value] ?? value;

function FrequencyResult({ summary }: { summary: WorkflowRunSummary }) {
  const artifacts = summary.artifacts ?? {};
  const reportUrl = artifacts["results/frequency_response.json"];
  const [report, setReport] = useState<FrequencyReport>();
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    setReport(undefined); setError("");
    if (reportUrl) void api<FrequencyReport>(reportUrl).then(value => {
      if (!disposed) setReport(value);
    }).catch(reason => { if (!disposed) setError(`频响诊断加载失败：${String(reason)}`); });
    return () => { disposed = true; };
  }, [reportUrl]);
  const fit = report?.theory_fit;
  return <div className="zaw-latest">
    <p>{summary.run_id} · {summary.timestamp} · 采集：{statusLabel(summary.completion_status)} · 分析：{statusLabel(summary.analysis_status)}</p>
    {summary.analysis_error && <p role="alert">{summary.analysis_error}</p>}
    {error && <p role="alert">{error}</p>}
    <p>理论参考来自 DG 正弦设定值。端电压的幅值、相位与一阶模型诊断用于频率响应比较，不能独立确定唯一电感。</p>
    {report && <p>频率点：{report.frequency_count} · 采集帧：{report.capture_count}</p>}
    {fit && <p>{fit.available
      ? `一阶幅值模型：A0 = ${fit.amplitude_zero_frequency_v} V，Ainf = ${fit.amplitude_high_frequency_v} V，fc = ${fit.corner_frequency_hz} Hz，R² = ${fit.r_squared}`
      : `一阶幅值模型不可用：${fit.reason ?? "报告未记录原因"}`}</p>}
    {[ ["results/frequency_response.png", "幅值比、相位、残差与归一化幅值下降"],
       ["results/waveform_overview.png", "各频率的实测、理想参考与正弦拟合波形"] ].map(([name, title]) =>
      artifacts[name] && <figure className="zaw-figure" key={name}><figcaption>{title}</figcaption>
        <img src={artifacts[name]} alt={name} loading="lazy" /></figure>)}
    {!Object.keys(artifacts).length && <p>本次运行暂无结果文件。</p>}
    <p className="xy-artifact-links">{Object.entries(artifacts).map(([name, url]) =>
      <a key={name} href={url} download>{name}</a>)}</p>
  </div>;
}

export function ZCoilInductanceFrequencyResponsePage() {
  return <WorkflowExperimentPage experimentId="z-coil-inductance-frequency-response"
    renderResult={summary => <FrequencyResult summary={summary} />} />;
}
