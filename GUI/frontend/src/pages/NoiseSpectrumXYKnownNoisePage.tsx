import { historyParameterValues } from "./experimentHelpers";
import { ScopePlot } from "../components/ScopePlot";
import { runStatusLabel } from "../components/workflow/RunHistory";
import {
  WorkflowExperimentPage, type RenderPreviewContext, type WorkflowRunSummary,
} from "./WorkflowExperimentPage";
import type { ParameterValues } from "../types/api";

const KNOWN_NOISE_ID = "noise-spectrum-xy-known-noise";
const PROBE_AM_ID = "noise-spectrum-xy-uncontrolled-probe-am";
const ANALYSIS_KEYS = { ratio_pass_low: "RATIO_PASS_LOW", ratio_pass_high: "RATIO_PASS_HIGH" };

type RunSummary = Omit<WorkflowRunSummary, "artifacts"> & {
  artifacts: Record<string, string>;
  analysis_quality?: {
    dispersion?: Record<string, unknown>;
    status?: string;
    psd?: { actual_rate_sa_s: number; nperseg: number; bin_width_hz: number; paired_points?: number };
    calibration?: { k: number; b: number; support_points: number; residual_std_hz?: number; source?: string };
    comparison?: {
      valid_points: number; total_points: number; median_ratio: number | null;
      criteria_pass: boolean; negative_differential_points?: number;
      pass_low?: number; pass_high?: number;
      signed_differences_retained?: boolean;
      in_band_valid_points?: number; in_band_total_points?: number;
      in_band_background?: { median_ratio: number | null; resolved_points: number; total_points: number };
      out_of_band_background?: { median_ratio: number | null; resolved_points: number; total_points: number };
    };
    truth_chain?: { K_Z_Hz_per_V: number; dispersion_slope_v_per_hz: number };
    warnings?: string[];
    fit_quality?: {
      valid_background_bins: number; total_bins: number;
      negative_delta_bins: number; positive_delta_bins: number;
      valid_controlled_bins?: number;
      negative_controlled_delta_bins?: number;
      positive_controlled_delta_bins?: number;
      background_method_on?: Record<string, number>;
      background_method_off?: Record<string, number>;
    };
  };
};
type WaveformPreview = {
  error?: string;
  nperseg?: number;
  bin_width_hz?: number;
  segments_per_point?: number;
  scan_seconds?: number;
  sample_rate_sa_s?: number;
  line_spacing_hz?: number;
  nyquist_hz?: number;
  design_peak_v?: number;
  aligned_amplitude_vpp?: number;
  aligned_amplitude_safe?: boolean;
  realized_psd_scale?: number;
  z_aligned_amplitude_vpp?: number;
  z_aligned_amplitude_safe?: boolean;
  z_realized_psd_scale?: number;
  actual_waveform_peak_v?: number;
  waveform_rms_v?: number;
  equivalent_noise_hz_per_rt_hz?: number;
  z_calibration_k_hz_per_v?: number;
  waveform_time_s?: number[];
  waveform_voltage_v?: number[];
  spectrum_frequency_hz?: number[];
  target_psd_v2_per_hz?: number[];
  calculated_psd_v2_per_hz?: number[];
};

const figureLabels: Record<string, string> = {
  "known_noise_preview.png": "任意波、目标谱与波形计算谱",
  "differential_psd.png": "差分 PSD 热图与脊线",
  "noise_spectrum_2d.png": "平均 PSD 二维谱",
  "noise_spectra_extracted.png": "差分响应系数",
  "lorentzian_line_shapes.png": "若干频率列的洛伦兹线形与拟合",
  "controlled_uncontrolled_comparison.png": "受控响应与不可控背景的注入前后对比",
  "signed_spectral_difference.png": "受控响应与不可控背景的基线和有符号差谱（双 Y 轴）",
  "measured_vs_truth.png": "测得谱与真值谱对比",
  "dispersion_scan.png": "色散线形与线形拟合",
};
const downloadable = [
  "known_noise_preview.npz",
  "noise_spectra.npz",
  "noise_spectra.csv",
  "calibration.npz",
  "popt_fit.npz",
  "separated_spectra.npz",
  "dispersion_source.npz",
  "dispersion_source.json",
  "psd_matrices.npz",
  "analysis_summary.json",
  "fit_diagnostics.npz",
];
const format = (value: number | null | undefined, digits = 3) =>
  value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);

function Result({ value, probeAm }: { value: RunSummary; probeAm: boolean }) {
  const figures = Object.entries(figureLabels).filter(([name]) => value.artifacts[name]);
  const files = downloadable.filter((name) => value.artifacts[name]);
  const comparison = value.analysis_quality?.comparison;
  return (
    <div className="zaw-latest">
      <p>{value.run_id} · {value.timestamp}</p>
      <p>采集：{runStatusLabel(value.completion_status)} · 分析：{runStatusLabel(value.analysis_status)}</p>
      {probeAm && <p>Z 已知可控噪声：{value.parameters.INJECT_CONTROLLED_NOISE ? "持续注入" : "未注入"}</p>}
      {value.analysis_quality?.dispersion && <details><summary>标定来源与条件差异</summary><pre>{JSON.stringify(value.analysis_quality.dispersion, null, 2)}</pre></details>}
      {value.analysis_error && <div className="alert error">{value.analysis_error}</div>}
      {comparison && <>
        <p>
          判据{comparison.criteria_pass ? "通过" : "未通过"}：带内中位比值 {format(comparison.median_ratio)}；
          有效 {comparison.valid_points} / {comparison.total_points} 个频率点
          （非正差分 {comparison.negative_differential_points ?? 0} 个
          {comparison.signed_differences_retained ? "已保留" : "已剔除"}）。
        </p>
        {comparison.in_band_total_points !== undefined && <p>
          注入带内有效 {comparison.in_band_valid_points} / {comparison.in_band_total_points} 点；
          覆盖不足 50% 时判据不通过。
        </p>}
        {(["in_band_background", "out_of_band_background"] as const).map((key) => {
          const background = comparison[key];
          return background && <p key={key}>
            {key === "in_band_background" ? "带内" : "带外"}背景 ON/OFF 中位比值：{format(background.median_ratio)}；
            可辨识 {background.resolved_points} / {background.total_points} 点。
          </p>;
        })}
        {comparison.pass_low !== undefined && comparison.pass_high !== undefined && (
          <p>阈值区间：[{format(comparison.pass_low)}, {format(comparison.pass_high)}]。</p>
        )}
      </>}
      {probeAm && value.analysis_quality?.fit_quality && <p>
        ΔN_S1 有效频点 {value.analysis_quality.fit_quality.valid_background_bins} / {value.analysis_quality.fit_quality.total_bins}；
        负差 {value.analysis_quality.fit_quality.negative_delta_bins} 点保留，正差 {value.analysis_quality.fit_quality.positive_delta_bins} 点。
        {value.analysis_quality.fit_quality.valid_controlled_bins !== undefined && <>受控响应 ΔS_beta 有效频点 {value.analysis_quality.fit_quality.valid_controlled_bins}；
          负差 {value.analysis_quality.fit_quality.negative_controlled_delta_bins} 点，正差 {value.analysis_quality.fit_quality.positive_controlled_delta_bins} 点。</>}
        N_S1 为锁相测量端 PSD，S_beta 为拟合受控响应系数；均未标定为 Probe 光功率谱。
        {value.analysis_quality.fit_quality.background_method_on && <>
          不可控背景独立验收；远端平台补充 ON {value.analysis_quality.fit_quality.background_method_on.off_resonance_plateau ?? 0} 点、
          OFF {value.analysis_quality.fit_quality.background_method_off?.off_resonance_plateau ?? 0} 点，可控响应只展示各态通过的部分。
        </>}
      </p>}
      {value.analysis_quality?.truth_chain && <p>
        真值链 K_Z = {format(value.analysis_quality.truth_chain.K_Z_Hz_per_V, 1)} Hz/V；
        色散斜率 = {format(value.analysis_quality.truth_chain.dispersion_slope_v_per_hz, 6)} V/Hz。
      </p>}
      {value.analysis_quality?.calibration && (value.analysis_quality.calibration.source === "configured_K_B"
        ? <p>暂定控制轴 K = {format(value.analysis_quality.calibration.k, 1)} Hz/V，B = {format(value.analysis_quality.calibration.b, 1)} Hz；峰位来自运行参数，仅供暂时分析。</p>
        : <p>
          移动峰标定 K = {format(value.analysis_quality.calibration.k, 1)} Hz/V；
          支持 {value.analysis_quality.calibration.support_points} 个控制点。
        </p>)}
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

export function NoiseSpectrumXYKnownNoisePage() {
  return <NoiseSpectrumXYInjectionPage experimentId={KNOWN_NOISE_ID} probeAm={false} />;
}

export function NoiseSpectrumXYProbeAMPage() {
  return <NoiseSpectrumXYInjectionPage experimentId={PROBE_AM_ID} probeAm />;
}

function WaveformPreview({ ctx, probeAm }: { ctx: RenderPreviewContext; probeAm: boolean }) {
  const preview = ctx.derived as unknown as WaveformPreview;
  const values = ctx.values;
  return (
    <div aria-label="噪声波形与分段预览" aria-live="polite">
      <h3>噪声波形与分段预览（只读）</h3>
      {ctx.derivedError
        // 模板以“派生预览失败”前缀上报，本页沿用原错误文案。
        ? <p className="alert error">{ctx.derivedError.replace("派生预览失败", "无法计算波形与分段预览")}</p>
        : <>
          <p>波形：采样率 {format(preview.sample_rate_sa_s, 0)} Sa/s；谱线间隔 {format(preview.line_spacing_hz, 1)} Hz；
            Nyquist {format((preview.nyquist_hz ?? Number.NaN) / 1000, 1)} kHz。</p>
          <p>设计峰值 {format(preview.design_peak_v)} V；实际注入谱 = 设计谱 × {format(preview.realized_psd_scale)}；
            波形 RMS {format(preview.waveform_rms_v)} V。</p>
          <p>
            达到设计谱密度需设注入幅度 {format(preview.aligned_amplitude_vpp)} Vpp。
            <button
              className="secondary"
              disabled={ctx.busy || preview.aligned_amplitude_vpp === undefined
                || (probeAm && !preview.aligned_amplitude_safe)
                || Math.abs((Number(values.NOISE_AMPLITUDE_VPP) || 0)
                  - (preview.aligned_amplitude_vpp ?? Number.NaN)) < 1e-6}
              onClick={() => ctx.setValues((current) => ({
                ...current,
                NOISE_AMPLITUDE_VPP: preview.aligned_amplitude_vpp ?? current.NOISE_AMPLITUDE_VPP,
              }))}
            >{probeAm ? "对齐 Probe AM 幅度" : "对齐注入幅度"}</button>
            {probeAm && preview.aligned_amplitude_vpp !== undefined && !preview.aligned_amplitude_safe
              && <span role="status"> 对齐幅度超出 Probe AOM AM 安全限值，请手动选择安全幅度。</span>}
          </p>
          {!probeAm && <p>等效频率噪声（带内中位）{format(preview.equivalent_noise_hz_per_rt_hz)} Hz/√Hz
            （手动 K_Z = {format(preview.z_calibration_k_hz_per_v, 1)} Hz/V）。</p>
          }
          {probeAm && <p>AM 输入峰值 ±{format((Number(values.NOISE_AMPLITUDE_VPP) || 0) / 2)} V；设计谱为参考值，分析同时保存实际任意波 Welch PSD。</p>}
          {probeAm && <p>Z 可控噪声{values.INJECT_CONTROLLED_NOISE ? "将持续注入" : "本次不注入"}；
            开启时电压谱 = 设计谱 × {format(preview.z_realized_psd_scale)}；
            达到设计谱密度需设幅度 {format(preview.z_aligned_amplitude_vpp)} Vpp。
            <button
              className="secondary"
              disabled={ctx.busy || preview.z_aligned_amplitude_vpp === undefined
                || !preview.z_aligned_amplitude_safe
                || Math.abs((Number(values.Z_NOISE_AMPLITUDE_VPP) || 0)
                  - (preview.z_aligned_amplitude_vpp ?? Number.NaN)) < 1e-6}
              onClick={() => ctx.setValues((current) => ({
                ...current,
                Z_NOISE_AMPLITUDE_VPP: preview.z_aligned_amplitude_vpp ?? current.Z_NOISE_AMPLITUDE_VPP,
              }))}
            >对齐 Z 噪声幅度</button>
            {preview.z_aligned_amplitude_vpp !== undefined && !preview.z_aligned_amplitude_safe
              && <span role="status"> 对齐幅度超出 Z 通道安全限值，请手动选择安全幅度。</span>}
          </p>}
          <p>Welch：每段点数 {preview.nperseg ?? "—"}；每点平均段数 {preview.segments_per_point ?? "—"}；
            格间距 {format(preview.bin_width_hz)} Hz。</p>
          <p>扫描采集与等待约 {format(preview.scan_seconds, 1)} 秒（每点两段，相邻点交替 OFF→ON / ON→OFF，两态均等待稳定）。</p>
          {preview.waveform_time_s?.length && preview.waveform_voltage_v?.length ? <>
            <h4>任意波时域</h4>
            <div className="known-noise-chart">
              <ScopePlot
                mode="time"
                reset={0}
                traces={[{ id: "Injected arbitrary waveform", color: "#147d74", x: preview.waveform_time_s, y: preview.waveform_voltage_v }]}
              />
            </div>
          </> : null}
          {preview.spectrum_frequency_hz?.length && preview.target_psd_v2_per_hz?.length && preview.calculated_psd_v2_per_hz?.length ? <>
            <h4>{probeAm ? "设计谱参考与波形计算谱" : "目标谱与波形计算谱"}</h4>
            <div className="known-noise-chart">
              <ScopePlot
                mode="frequency"
                xScale="log"
                yScale="log"
                reset={0}
                traces={[
                  { id: "Target PSD", color: "#d97706", x: preview.spectrum_frequency_hz, y: preview.target_psd_v2_per_hz },
                  { id: "Welch PSD from waveform", color: "#2563eb", x: preview.spectrum_frequency_hz, y: preview.calculated_psd_v2_per_hz },
                ]}
              />
            </div>
          </> : null}
        </>}
      <p>预览基于请求采样率与设计谱；实际值以分析结果为准。三个谱段数组必须等长，逗号分隔。</p>
    </div>
  );
}

function NoiseSpectrumXYInjectionPage({ experimentId, probeAm }: { experimentId: string; probeAm: boolean }) {
  return <WorkflowExperimentPage
    experimentId={experimentId}
    analysisParameterKeys={probeAm ? undefined : ANALYSIS_KEYS}
    eyebrow={probeAm ? "MEASUREMENT · PROBE AM NOISE" : "VERIFICATION · KNOWN INJECTION"}
    startLabel={probeAm ? undefined : "完整运行"}
    fillBack={({ summary, fields }) => {
      const compatible: ParameterValues = historyParameterValues(fields, summary.parameters);
      if (probeAm && !Object.hasOwn(summary.parameters, "INJECT_CONTROLLED_NOISE")) {
        compatible.INJECT_CONTROLLED_NOISE = false;
      }
      if (!probeAm && !Object.hasOwn(summary.parameters, "Z_CALIBRATION_K_HZ_PER_V")) {
        compatible.Z_CALIBRATION_K_HZ_PER_V = 0;
      }
      return { values: compatible };
    }}
    renderResult={value => <Result value={value as RunSummary} probeAm={probeAm} />}
    renderPreview={ctx => (
      <details className="xy-workflow-preview">
        <summary>{probeAm ? "AM 噪声波形、可选 Z 注入与 Welch 预览" : "波形、注入真值与 Welch 预览"}</summary>
        <WaveformPreview ctx={ctx} probeAm={probeAm} />
      </details>
    )}
  />;
}
