import { runStatusLabel } from "../components/workflow/RunHistory";
import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";

const ID = "bell-bloom-z-field-calibration";
const ANALYSIS_KEYS = { LINEAR_R_SQUARED_MIN: "LINEAR_R_SQUARED_MIN" };
type Summary = Omit<WorkflowRunSummary, "artifacts"> & {
  artifacts: Record<string, string>;
  failure_reason: string;
  safety_shutdown: { errors?: string[] };
  analysis: null | {
    success: boolean;
    K_Z_Hz_per_V: number | null;
    f_0V_Hz: number | null;
    linear_fit: { uncertainties: (number | null)[]; r_squared: number | null };
    rejection_reasons: string[];
  };
};
const number = (value: number | null) => value === null ? "—" : value.toLocaleString("zh-CN", { maximumSignificantDigits: 7 });

function Result({ value }: { value: Summary }) {
  const analysis = value.analysis;
  return <div className="zaw-latest">
    <p>{value.run_id} · {value.timestamp}</p>
    <p>采集：{runStatusLabel(value.completion_status)} · 分析：{runStatusLabel(value.analysis_status)}</p>
    {value.failure_reason && <div className="alert error">{value.failure_reason}</div>}
    {value.analysis_error && <div className="alert error">{value.analysis_error}</div>}
    {value.safety_shutdown.errors?.length ? <div className="alert error">安全恢复：{value.safety_shutdown.errors.join("；")}</div> : null}
    {!analysis ? <p className="zaw-empty">本次运行暂无分析结果。</p> : <>
      <div className="zaw-metrics-grid">
        <div className="zaw-metric"><small>标定质量</small><strong>{analysis.success ? "通过" : "未通过"}</strong></div>
        <div className="zaw-metric"><small>Z 标定系数（Hz/V）</small><strong>{number(analysis.K_Z_Hz_per_V)} ± {number(analysis.linear_fit.uncertainties[0])}</strong></div>
        <div className="zaw-metric"><small>零偏截距（Hz）</small><strong>{number(analysis.f_0V_Hz)}</strong></div>
        <div className="zaw-metric"><small>线性 R²</small><strong>{number(analysis.linear_fit.r_squared)}</strong></div>
      </div>
      {analysis.rejection_reasons.length > 0 && <div className="alert error">{analysis.rejection_reasons.join("；")}</div>}
      {value.artifacts["z_frequency_calibration.png"] && <figure className="zaw-figure"><figcaption>Z 电压与共振中心</figcaption><img src={value.artifacts["z_frequency_calibration.png"]} alt="Z 电压频率标定曲线" /></figure>}
      <details><summary>查看各频扫拟合与残差</summary>{["frequency_response_fits.png", "z_frequency_residuals.png"].filter(name => value.artifacts[name]).map(name => <figure className="zaw-figure" key={name}><img src={value.artifacts[name]} alt={name === "frequency_response_fits.png" ? "各 Z 电压的 R 频扫拟合" : "线性拟合残差"} loading="lazy" /></figure>)}</details>
      <p className="xy-artifact-links">{["analysis.yaml", "analysis.json", "calibration_results.npz"].filter(name => value.artifacts[name]).map(name => <a key={name} href={value.artifacts[name]} download>{name}</a>)}</p>
    </>}
  </div>;
}

export function BellBloomZCalibrationPage() {
  return <WorkflowExperimentPage experimentId={ID} analysisParameterKeys={ANALYSIS_KEYS}
    eyebrow="CALIBRATION · BELL BLOOM" renderResult={value => <Result value={value as Summary} />} />;
}
