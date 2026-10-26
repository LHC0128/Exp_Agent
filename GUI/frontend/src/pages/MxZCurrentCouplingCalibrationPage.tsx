import { useEffect, useState } from "react";
import { api } from "../api";
import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";

type CalibrationResult = {
  success?: boolean | null;
  K_Z_Hz_per_A?: number | null;
  K_Z_Hz_per_Vsense?: number | null;
  f_0_at_zero_current_hz?: number | null;
  accepted_curve_count?: number | null;
  total_curve_count?: number | null;
  rejection_reasons?: string[] | null;
  linearity?: unknown;
  hysteresis?: unknown;
  repeatability?: unknown;
  operating_point?: unknown;
  curves?: unknown[] | null;
};
const labels: Record<string, string> = { completed: "完成", quality_failed: "质量未通过", failed: "失败",
  cancelled: "已取消", running: "运行中", acquired: "待安全封存", partial: "部分完成", stale: "需重算", not_started: "未分析" };
const status = (value: string) => labels[value] ?? value;
const number = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value) ? value.toPrecision(8) : "不可用";
const plots: Record<string, string> = { "current_coupling_calibration.png": "实际电流与共振中心标定",
  "frequency_response_fits.png": "各 Z 工作点的 R 频扫拟合" };

function Result({ value }: { value: WorkflowRunSummary }) {
  const artifacts = value.artifacts ?? {};
  const reportUrl = artifacts["results/analysis.json"];
  const [report, setReport] = useState<CalibrationResult>();
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    setReport(undefined); setError("");
    if (reportUrl) void api<CalibrationResult>(`/api/experiments/mx-z-current-coupling-calibration/runs/${encodeURIComponent(value.run_id)}/result`).then(result => {
      if (!disposed) setReport(result);
    }).catch(reason => { if (!disposed) setError(`标定诊断加载失败：${String(reason)}`); });
    return () => { disposed = true; };
  }, [reportUrl, value.run_id]);
  return <div className="zaw-latest">
    <p>{value.run_id} · {value.timestamp} · 采集：{status(value.completion_status)} · 分析：{status(value.analysis_status)}</p>
    {value.analysis_error && <div className="alert error">{value.analysis_error}</div>}
    {error && <div className="alert error">{error}</div>}
    {report && <>
      <p className={report.success ? "alert success" : "alert error"}>
        {report.success == null ? "标定质量不可用" : report.success ? "标定质量通过" : "标定质量未通过；诊断已保留，不能作为有效标定使用"}
      </p>
      <dl>
        <dt>Z 电流耦合系数 (Hz/A)</dt><dd>{number(report.K_Z_Hz_per_A)}</dd>
        <dt>采样电阻电压耦合系数 (Hz/Vsense)</dt><dd>{number(report.K_Z_Hz_per_Vsense)}</dd>
        <dt>零电流共振中心 (Hz)</dt><dd>{number(report.f_0_at_zero_current_hz)}</dd>
        <dt>有效频扫</dt><dd>{report.accepted_curve_count ?? "不可用"} / {report.total_curve_count ?? "不可用"}</dd>
      </dl>
      {report.rejection_reasons == null && <p>标定拒绝原因：不可用</p>}
      {(report.rejection_reasons ?? []).length > 0 && <ul aria-label="标定拒绝原因">
        {report.rejection_reasons?.map((reason, index) => <li key={index}>{reason}</li>)}
      </ul>}
      <details><summary>线性度、磁滞、重复性与逐曲线诊断</summary>
        <pre style={{ maxHeight: 320, overflow: "auto", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ linearity: report.linearity ?? "不可用", hysteresis: report.hysteresis ?? "不可用",
          repeatability: report.repeatability ?? "不可用", operating_point: report.operating_point ?? "不可用", curves: report.curves ?? "不可用" }, null, 2)}</pre>
      </details>
    </>}
    {!Object.keys(artifacts).length && <p>本次运行暂无分析结果。</p>}
    {Object.entries(artifacts).filter(([name]) => plots[name.split("/").at(-1)!]).map(([name, url]) =>
      <figure className="zaw-figure" key={name}><figcaption>{plots[name.split("/").at(-1)!]}</figcaption>
        <img src={url} alt={plots[name.split("/").at(-1)!]} loading="lazy" /></figure>)}
    <p className="xy-artifact-links">{Object.entries(artifacts).map(([name, url]) => <a key={name} href={url} download>{name}</a>)}</p>
  </div>;
}

export function MxZCurrentCouplingCalibrationPage() {
  return <WorkflowExperimentPage experimentId="mx-z-current-coupling-calibration"
    analysisParameterKeys={{ LINEAR_R_SQUARED_MIN: "LINEAR_R_SQUARED_MIN" }} renderResult={value => <Result value={value} />} />;
}
