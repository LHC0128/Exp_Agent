import { useEffect, useState } from "react";
import { api } from "../api";
import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";

type Diagnostics = {
  per_amplitude?: { amplitude_vpp: number; success: boolean; split?: boolean; r_squared?: number;
    parameters?: { gamma_hz: number; center_hz: number; saturation: number }; rejection_reasons?: string[] }[];
  saturation_scaling?: { success: boolean; slope_per_vpp2?: number; r_squared?: number; n_points?: number };
};
const labels: Record<string, string> = {
  completed: "完成", failed: "失败", cancelled: "已取消", running: "运行中", partial: "部分完成",
  not_started: "未开始分步分析", quality_failed: "质量未通过", unknown: "未记录",
};
const number = (value?: number) => value != null && Number.isFinite(value) ? value.toPrecision(5) : "—";

function FrequencyResult({ summary }: { summary: WorkflowRunSummary }) {
  const artifacts = summary.artifacts ?? {};
  const [diagnostics, setDiagnostics] = useState<Diagnostics>();
  const [error, setError] = useState("");
  const source = `/api/experiments/mx-y-rf-frequency-response/runs/${encodeURIComponent(summary.run_id)}/analysis`;
  useEffect(() => {
    let disposed = false;
    setDiagnostics(undefined); setError("");
    void api<Diagnostics & { summary?: Diagnostics }>(source).then(value => {
      if (!disposed) setDiagnostics(value.summary ?? value);
    }).catch(reason => { if (!disposed) setError(`拟合诊断读取失败：${String(reason)}`); });
    return () => { disposed = true; };
  }, [source, summary.analysis_status, artifacts["results/analysis.json"], artifacts["results/fit_diagnostics.yaml"]]);
  const scaling = diagnostics?.saturation_scaling;
  return <div className="zaw-latest">
    <p><strong>{summary.run_id}</strong> · {summary.timestamp} · 采集：{labels[summary.completion_status] ?? summary.completion_status}
      {" · 分析："}{labels[summary.analysis_status] ?? summary.analysis_status}</p>
    {summary.analysis_error && <p role="alert">{summary.analysis_error}</p>}
    {error && <p role="alert">{error}</p>}
    {diagnostics?.per_amplitude && <div className="xy-diagnostic" style={{ overflowX: "auto" }}>
      <table aria-label="布洛赫拟合诊断"><thead><tr><th>RF 幅度 (Vpp)</th><th>拟合状态</th><th>中心 (Hz)</th>
        <th>γ (Hz)</th><th>饱和度 S</th><th>R²</th><th>诊断</th></tr></thead><tbody>
        {diagnostics.per_amplitude.map((curve, index) => <tr key={index}>
          <td>{number(curve.amplitude_vpp)}</td><td>{curve.success ? "通过" : "未通过"}</td>
          <td>{number(curve.parameters?.center_hz)}</td><td>{number(curve.parameters?.gamma_hz)}</td>
          <td>{number(curve.parameters?.saturation)}</td><td>{number(curve.r_squared)}</td>
          <td>{curve.success ? curve.split ? "Rabi 劈裂双峰" : "弱驱动单峰" : curve.rejection_reasons?.join("；")}</td>
        </tr>)}
      </tbody></table>
    </div>}
    {scaling && <p>{scaling.success
      ? `S–Vpp² 诊断：斜率 ${number(scaling.slope_per_vpp2)} Vpp⁻²，R²=${number(scaling.r_squared)}，${scaling.n_points} 条有效曲线。`
      : "S–Vpp² 诊断未形成有效线性结果；请查看逐幅度拟合与原始数据。"}</p>}
    {([ ["frequency_response.png", "RF 频率响应与布洛赫拟合"], ["s_vs_amplitude.png", "饱和度与幅度平方"] ] as const)
      .filter(([name]) => artifacts[`results/${name}`]).map(([name, title]) => <figure className="zaw-figure" key={name}>
        <figcaption>{title}</figcaption><img src={artifacts[`results/${name}`]} alt={title} loading="lazy" />
      </figure>)}
    {!Object.keys(artifacts).length && <p>本次运行暂无结果文件。</p>}
    <p className="xy-artifact-links">{Object.entries(artifacts).map(([name, url]) =>
      <a key={name} href={url} download>{name}</a>)}</p>
  </div>;
}

const renderResult = (summary: WorkflowRunSummary) => <FrequencyResult summary={summary} />;

export function MxYRFFrequencyResponsePage() {
  return <WorkflowExperimentPage experimentId="mx-y-rf-frequency-response"
    analysisParameterKeys={{ FIT_R_SQUARED_MIN: "FIT_R_SQUARED_MIN",
      FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX: "FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX",
      R_BAD_POINT_STD_THRESHOLD_V: "R_BAD_POINT_STD_THRESHOLD_V" }} renderResult={renderResult} />;
}
