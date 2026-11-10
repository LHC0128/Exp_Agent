import type { ParameterValue, ParameterValues } from "../types/api";
import { runStatusLabel } from "../components/workflow/RunHistory";
import { historyParameterValues } from "./experimentHelpers";
import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";

const ID = "noise-spectrum-xy";
const OLD_WELCH_NOTICE = "旧 Welch 每段点数不再填回；新采集仅按表单中的频率格间距自动分段。历史重新分析保留旧规则。";

type Summary = WorkflowRunSummary & {
  artifacts: Record<string, string>;
  analysis_quality?: {
    psd?: { actual_rate_sa_s: number; nperseg: number; bin_width_hz: number; segments_per_point_range?: number[] | null };
    calibration?: { k: number; b: number; support_points: number; residual_std_hz: number };
    fit?: { valid_points: number; background_valid_points?: number; total_points: number; valid_frequency_extent_hz: number[] | null };
    warnings?: string[];
  };
};

const figureLabels: Record<string, string> = {
  "calibration.png": "PSD 脊线与正弦峰值标定",
  "noise_spectrum_2d.png": "二维噪声谱（未标定时纵轴为扫描电压）",
  "noise_spectrum_lines.png": "典型控制点噪声谱",
  "noise_spectra_extracted.png": "提取的可控与不可控噪声谱",
  "diagonal_analysis.png": "PSD 斜线特征诊断",
  "psd_column_diagnostics.png": "PSD 逐列拟合诊断",
  "fit_quality.png": "拟合残差、留出误差与线宽",
  "background_identifiability.png": "不可控背景的模型拟合与远端平台来源",
};
const downloadable = [
  "noise_spectra.npz",
  "noise_spectra.csv",
  "calibration.npz",
  "popt_fit.npz",
  "psd_matrix.npz",
  "analysis_summary.json",
  "fit_diagnostics.npz",
  "global_comparison.npz",
];
function Result({ value }: { value: Summary }) {
  const figures = Object.entries(figureLabels).filter(([name]) => value.artifacts[name]);
  const files = downloadable.filter((name) => value.artifacts[name]);
  return (
    <div className="zaw-latest">
      <p>{value.run_id} · {value.timestamp}</p>
      <p>采集：{runStatusLabel(value.completion_status)} · 分析：{runStatusLabel(value.analysis_status)}</p>
      {value.analysis_error && <div className="alert error">{value.analysis_error}</div>}
      {value.analysis_quality?.psd && <p>
        实际 Welch 每段点数：{value.analysis_quality.psd.nperseg}；
        频率格间距：{value.analysis_quality.psd.bin_width_hz.toFixed(3)} Hz；
        实际采样率：{value.analysis_quality.psd.actual_rate_sa_s.toFixed(3)} Sa/s；
        每点平均段数：{value.analysis_quality.psd.segments_per_point_range?.join("–") ?? "未记录"}。
      </p>}
      {value.analysis_quality?.calibration && <p>
        移动峰标定 K = {value.analysis_quality.calibration.k.toFixed(1)} Hz/V；
        支持 {value.analysis_quality.calibration.support_points} 个控制点；
        残差标准差 {value.analysis_quality.calibration.residual_std_hz.toFixed(1)} Hz。
      </p>}
      {value.analysis_quality?.fit && <p>
        通过质量验收 {value.analysis_quality.fit.valid_points} / {value.analysis_quality.fit.total_points} 个频率点；
        {value.analysis_quality.fit.background_valid_points !== undefined && <>不可控背景独立有效 {value.analysis_quality.fit.background_valid_points} 点；</>}
        无效点保留为空，不插值或外推。
      </p>}
      {value.analysis_quality?.warnings?.map((warning) => <p key={warning}>{warning}</p>)}
      {!figures.length && <p className="zaw-empty">本次运行暂无结果图。</p>}
      {figures.map(([name, label], index) => (
        <figure className="zaw-figure" key={name}>
          <figcaption>{label}</figcaption>
          <img src={value.artifacts[name]} alt={label} loading={index ? "lazy" : "eager"} />
        </figure>
      ))}
      {files.length > 0 && (
        <p className="xy-artifact-links">{files.map((name) => <a key={name} href={value.artifacts[name]} download>{name}</a>)}</p>
      )}
    </div>
  );
}

const derivedText = (value: ParameterValue | undefined) =>
  typeof value === "number" || typeof value === "string" ? String(value) : "—";
const derivedFixed = (value: ParameterValue | undefined, digits: number) =>
  typeof value === "number" ? value.toFixed(digits) : "—";

function WelchPreview({ derived, error }: { derived: ParameterValues; error: string }) {
  const message = error || (typeof derived.error === "string" ? derived.error : "");
  return (
    <div className="xy-workflow-preview" aria-label="Welch 分段预览" aria-live="polite">
      <h3>Welch 分段预览（只读）</h3>
      {message ? <p className="alert error">{message}</p> : <>
        <p>预计每段点数：{derivedText(derived.nperseg)}；
          每点平均段数：{derivedText(derived.segments_per_point)}（50% 重叠，非独立段数）。</p>
        <p>预计频率格间距：{derivedFixed(derived.bin_width_hz, 3)} Hz；
          扫描采集与等待约 {derivedFixed(derived.scan_seconds, 1)} 秒。</p>
      </>}
    </div>
  );
}

export function NoiseSpectrumXYPage() {
  return <WorkflowExperimentPage experimentId={ID} eyebrow="MEASUREMENT · XY SINE"
    renderResult={value => <Result value={value as Summary} />}
    renderPreview={({ derived, derivedError }) => <WelchPreview derived={derived} error={derivedError} />}
    fillBack={({ summary, fields }) => ({
      values: historyParameterValues(fields, summary.parameters),
      notice: Object.hasOwn(summary.parameters, "HF2_NPERSEG") ? OLD_WELCH_NOTICE : undefined,
    })} />;
}
