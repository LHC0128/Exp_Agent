import { useEffect, useState, type KeyboardEvent } from "react";

import { api } from "../api";
import { useJobActivity } from "../components/JobActivity";
import { JobView } from "../components/JobView";
import { ScopePlot } from "../components/ScopePlot";
import { ParameterForm } from "../components/MxYOptimalControlFrequency/ParameterForm";
import { PageHead } from "../components/PageHead";
import type {
  ExperimentDefinition,
  ExperimentSchema,
  Job,
  JobSummary,
  ParameterLayout,
  ParameterValues,
} from "../types/api";
import {
  defaultParameterLayout,
  sameParameterLayout,
  validatedParameterLayout,
} from "./experimentHelpers";
import "./zawClosedLoop.css";

const KNOWN_NOISE_ID = "noise-spectrum-xy-known-noise";
const PROBE_AM_ID = "noise-spectrum-xy-uncontrolled-probe-am";
const PAGE_SIZE = 50;
const terminal = (job: Job | undefined) => Boolean(
  job && ["completed", "failed", "cancelled"].includes(job.status),
);

type RunItem = {
  run_id: string;
  timestamp: string;
  run_tag: string;
  completion_status: string;
};
type RunList = { runs: RunItem[]; total: number };
type RunSummary = RunItem & {
  analysis_status: string;
  analysis_error: string;
  parameters: ParameterValues;
  artifacts: Record<string, string>;
  analysis_quality?: {
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

const statusLabels: Record<string, string> = {
  completed: "完成",
  failed: "失败",
  cancelled: "已取消",
  running: "运行中",
  not_started: "未分析",
  quality_failed: "质量验收未通过",
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
  "psd_matrices.npz",
  "analysis_summary.json",
  "fit_diagnostics.npz",
];
const summaryUrl = (base: string, runId: string) => `${base}/runs/${encodeURIComponent(runId)}/summary`;
const status = (value: string) => statusLabels[value] ?? value;
const format = (value: number | null | undefined, digits = 3) =>
  value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);

function Result({ value, probeAm }: { value: RunSummary; probeAm: boolean }) {
  const figures = Object.entries(figureLabels).filter(([name]) => value.artifacts[name]);
  const files = downloadable.filter((name) => value.artifacts[name]);
  const comparison = value.analysis_quality?.comparison;
  return (
    <div className="zaw-latest">
      <p>{value.run_id} · {value.timestamp}</p>
      <p>采集：{status(value.completion_status)} · 分析：{status(value.analysis_status)}</p>
      {probeAm && <p>Z 已知可控噪声：{value.parameters.INJECT_CONTROLLED_NOISE ? "持续注入" : "未注入"}</p>}
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
        <p>{files.map((name) => <a key={name} href={value.artifacts[name]} download style={{ marginRight: 12 }}>{name}</a>)}</p>
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

function NoiseSpectrumXYInjectionPage({ experimentId: ID, probeAm }: { experimentId: string; probeAm: boolean }) {
  const BASE = `/api/experiments/${ID}`;
  const prefix = probeAm ? "probe-am-noise" : "known-noise";
  const [tab, setTab] = useState<"run" | "history" | "architecture">("run");
  const [definition, setDefinition] = useState<ExperimentDefinition>();
  const [fields, setFields] = useState<ExperimentSchema["fields"]>([]);
  const [values, setValues] = useState<ParameterValues>({});
  const [layout, setLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [savedLayout, setSavedLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [job, setJob] = useState<Job>();
  const [analysisJob, setAnalysisJob] = useState<Job>();
  const [starting, setStarting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preflight, setPreflight] = useState<{ ok: boolean; errors: string[] }>();
  const [preview, setPreview] = useState<WaveformPreview>();
  const [latest, setLatest] = useState<RunSummary>();
  const [list, setList] = useState<RunList>({ runs: [], total: 0 });
  const [selectedId, setSelectedId] = useState("");
  const [selected, setSelected] = useState<RunSummary>();
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const { hardwareBusy } = useJobActivity();
  const jobActive = Boolean(job && !terminal(job));
  const analysisActive = Boolean(analysisJob && !terminal(analysisJob));

  useEffect(() => {
    if (!fields.length) return;
    let disposed = false;
    setPreview(undefined);
    const timer = window.setTimeout(() => {
      void api<{ values: WaveformPreview }>(`${BASE}/derive`, {
        method: "POST", body: JSON.stringify({ parameters: values }),
      }).then((result) => {
        if (!disposed) setPreview(result.values);
      }).catch((reason) => {
        if (!disposed) setPreview({ error: `无法计算波形与分段预览：${String(reason)}` });
      });
    }, 200);
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [fields, values]);

  useEffect(() => {
    let disposed = false;
    void Promise.all([
      api<ExperimentDefinition>(BASE),
      api<ExperimentSchema>(`${BASE}/schema`),
      api<JobSummary[]>("/api/jobs"),
    ]).then(async ([def, schema, jobs]) => {
      if (disposed) return;
      setDefinition(def);
      setFields(schema.fields);
      setValues(Object.fromEntries(schema.fields.map((field) => [field.name, field.default])));
      const persisted = schema.parameter_layout_saved
        ? validatedParameterLayout(schema.fields, schema.parameter_layout)
        : undefined;
      const initial = persisted ?? defaultParameterLayout(schema.fields);
      setLayout(initial);
      setSavedLayout(initial);
      const matching = jobs.filter((item) => item.kind === `experiment:${ID}`);
      const recoverable = matching.some((item) => ["queued", "running"].includes(item.status))
        ? matching.filter((item) => ["queued", "running"].includes(item.status))
        : matching.filter((item) => item.started_at);
      if (recoverable.length) {
        const recovered = await api<Job>(`/api/jobs/${recoverable[0].id}`);
        if (!disposed) setJob(recovered);
      }
    }).catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    let disposed = false;
    void api<RunList>(`${BASE}/runs?limit=${PAGE_SIZE}&offset=${offset}`).then((data) => {
      if (disposed) return;
      setList(data);
      setSelectedId((current) => current || data.runs[0]?.run_id || "");
    }).catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [offset, refresh, job?.status, analysisJob?.status]);

  useEffect(() => {
    let disposed = false;
    void api<RunList>(`${BASE}/runs?limit=1&offset=0`).then(async (data) => {
      const latestRun = data.runs[0];
      const summary = latestRun ? await api<RunSummary>(summaryUrl(BASE, latestRun.run_id)) : undefined;
      if (!disposed) setLatest(summary);
    }).catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [refresh, job?.status, analysisJob?.status]);

  useEffect(() => {
    if (!selectedId) return;
    let disposed = false;
    setSelected(undefined);
    void api<RunSummary>(summaryUrl(BASE, selectedId))
      .then((data) => { if (!disposed) setSelected(data); })
      .catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [selectedId, refresh, job?.status, analysisJob?.status]);

  const check = async (start: boolean) => {
    setStarting(true);
    setError("");
    try {
      const result = await api<{ ok: boolean; errors: string[] }>(`${BASE}/preflight`, {
        method: "POST",
        body: JSON.stringify({ parameters: values }),
      });
      setPreflight(result);
      if (start && result.ok) {
        setJob(await api<Job>(`${BASE}/runs`, {
          method: "POST",
          body: JSON.stringify({ parameters: values }),
        }));
      }
    } catch (reason) {
      setError(String(reason));
    } finally {
      setStarting(false);
    }
  };

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const result = await api<{ message: string; schema: ExperimentSchema }>(`${BASE}/defaults`, {
        method: "PUT",
        body: JSON.stringify({ parameters: values, parameter_layout: layout }),
      });
      const persisted = result.schema.parameter_layout_saved
        ? validatedParameterLayout(result.schema.fields, result.schema.parameter_layout)
        : undefined;
      if (!persisted || !sameParameterLayout(persisted, layout)) {
        throw new Error("后端未确认参数布局已保存");
      }
      setSavedLayout(persisted);
      setNotice(result.message);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSaving(false);
    }
  };

  const reanalyze = async (runId: string) => {
    setStarting(true);
    setError("");
    try {
      setAnalysisJob(await api<Job>(`/api/runs/${encodeURIComponent(runId)}/analyze`, {
        method: "POST",
        body: JSON.stringify({ experiment_id: ID }),
      }));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setStarting(false);
    }
  };

  const fillBack = (parameters: ParameterValues) => {
    const compatible = Object.fromEntries(
      fields
        .filter((field) => Object.hasOwn(parameters, field.name))
        .map((field) => [field.name, parameters[field.name]]),
    );
    if (probeAm && !Object.hasOwn(parameters, "INJECT_CONTROLLED_NOISE")) {
      compatible.INJECT_CONTROLLED_NOISE = false;
    }
    setValues((current) => ({ ...current, ...compatible }));
    setTab("run");
  };

  const tabKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const order = ["run", "history", "architecture"] as const;
    const index = order.indexOf(tab as typeof order[number]);
    const next = order[(index + (event.key === "ArrowRight" ? 1 : order.length - 1)) % order.length];
    setTab(next);
    event.currentTarget.querySelector<HTMLButtonElement>(`#${prefix}-tab-${next}`)?.focus();
  };

  return (
    <>
      <PageHead
        eyebrow={probeAm ? "MEASUREMENT · PROBE AM NOISE" : "VERIFICATION · KNOWN INJECTION"}
        title={definition?.title ?? (probeAm ? "XY 控制测量已知不可控 Probe AM 噪声谱" : "XY 控制测量已知可控噪声谱")}
        description={definition?.description ?? "读取实验定义与运行记录。"}
      />
      {error && <div className="alert error" role="alert">{error}</div>}
      <div className="zaw-tab-strip" role="tablist" aria-label={probeAm ? "XY Probe AM 噪声实验视图" : "XY 已知噪声注入实验视图"} onKeyDown={tabKey}>
        {(["run", "history", "architecture"] as const).map((item) => (
          <button
            key={item}
            id={`${prefix}-tab-${item}`}
            role="tab"
            aria-selected={tab === item}
            aria-controls={`${prefix}-panel-${item}`}
            tabIndex={tab === item ? 0 : -1}
            className={tab === item ? "active" : ""}
            onClick={() => setTab(item)}
          >
            {item === "run" ? "运行" : item === "history" ? "历史" : "架构"}
          </button>
        ))}
      </div>

      <div id={`${prefix}-panel-run`} role="tabpanel" aria-labelledby={`${prefix}-tab-run`} hidden={tab !== "run"}>
        <div className={probeAm ? "noise-injection-dashboard" : "zaw-dashboard"}>
          <section className="zaw-panel zaw-panel-params">
            <div className="zaw-panel-head"><small>PARAMETERS</small><h2>实验参数</h2></div>
            {probeAm && <button
              type="button"
              className="secondary"
              aria-pressed={Boolean(values.INJECT_CONTROLLED_NOISE)}
              disabled={jobActive || starting || !fields.length}
              onClick={() => setValues((current) => ({ ...current, INJECT_CONTROLLED_NOISE: !current.INJECT_CONTROLLED_NOISE }))}
            >Z 可控噪声：{values.INJECT_CONTROLLED_NOISE ? "注入" : "不注入"}</button>}
            <ParameterForm
              fields={probeAm ? fields.filter((field) => field.name !== "INJECT_CONTROLLED_NOISE") : fields}
              values={values}
              layout={layout}
              onChange={(field, value) => setValues((current) => ({ ...current, [field.name]: value }))}
              onLayoutChange={setLayout}
              onMoveNotice={setNotice}
              disabled={jobActive || starting}
            />
            <div aria-label="噪声波形与分段预览" aria-live="polite">
              <h3>噪声波形与分段预览（只读）</h3>
              {preview?.error ? <p className="alert error">{preview.error}</p> : <>
                <p>波形：采样率 {format(preview?.sample_rate_sa_s, 0)} Sa/s；谱线间隔 {format(preview?.line_spacing_hz, 1)} Hz；
                  Nyquist {format(preview?.nyquist_hz / 1000, 1)} kHz。</p>
                <p>设计峰值 {format(preview?.design_peak_v)} V；实际注入谱 = 设计谱 × {format(preview?.realized_psd_scale)}；
                  波形 RMS {format(preview?.waveform_rms_v)} V。</p>
                <p>
                  达到设计谱密度需设注入幅度 {format(preview?.aligned_amplitude_vpp)} Vpp。
                  <button
                    className="secondary"
                    disabled={jobActive || starting || preview?.aligned_amplitude_vpp === undefined
                      || (probeAm && !preview.aligned_amplitude_safe)
                      || Math.abs((Number(values.NOISE_AMPLITUDE_VPP) || 0)
                        - (preview.aligned_amplitude_vpp ?? Number.NaN)) < 1e-6}
                    onClick={() => setValues((current) => ({
                      ...current,
                      NOISE_AMPLITUDE_VPP: preview.aligned_amplitude_vpp ?? current.NOISE_AMPLITUDE_VPP,
                    }))}
                  >{probeAm ? "对齐 Probe AM 幅度" : "对齐注入幅度"}</button>
                  {probeAm && preview?.aligned_amplitude_vpp !== undefined && !preview.aligned_amplitude_safe
                    && <span role="status"> 对齐幅度超出 Probe AOM AM 安全限值，请手动选择安全幅度。</span>}
                </p>
                {!probeAm && <p>等效频率噪声（带内中位）{format(preview?.equivalent_noise_hz_per_rt_hz)} Hz/√Hz
                  （K_Z = {format(preview?.z_calibration_k_hz_per_v, 1)} Hz/V）。</p>
                }
                {probeAm && <p>AM 输入峰值 ±{format((Number(values.NOISE_AMPLITUDE_VPP) || 0) / 2)} V；设计谱为参考值，分析同时保存实际任意波 Welch PSD。</p>}
                {probeAm && <p>Z 可控噪声{values.INJECT_CONTROLLED_NOISE ? "将持续注入" : "本次不注入"}；
                  开启时电压谱 = 设计谱 × {format(preview?.z_realized_psd_scale)}；
                  达到设计谱密度需设幅度 {format(preview?.z_aligned_amplitude_vpp)} Vpp。
                  <button
                    className="secondary"
                    disabled={jobActive || starting || preview?.z_aligned_amplitude_vpp === undefined
                      || !preview.z_aligned_amplitude_safe
                      || Math.abs((Number(values.Z_NOISE_AMPLITUDE_VPP) || 0)
                        - (preview.z_aligned_amplitude_vpp ?? Number.NaN)) < 1e-6}
                    onClick={() => setValues((current) => ({
                      ...current,
                      Z_NOISE_AMPLITUDE_VPP: preview.z_aligned_amplitude_vpp ?? current.Z_NOISE_AMPLITUDE_VPP,
                    }))}
                  >对齐 Z 噪声幅度</button>
                  {preview?.z_aligned_amplitude_vpp !== undefined && !preview.z_aligned_amplitude_safe
                    && <span role="status"> 对齐幅度超出 Z 通道安全限值，请手动选择安全幅度。</span>}
                </p>}
                <p>Welch：每段点数 {preview?.nperseg ?? "—"}；每点平均段数 {preview?.segments_per_point ?? "—"}；
                  格间距 {format(preview?.bin_width_hz)} Hz。</p>
                <p>扫描采集与等待约 {format(preview?.scan_seconds, 1)} 秒（每点两段，相邻点交替 OFF→ON / ON→OFF，两态均等待稳定）。</p>
                {preview?.waveform_time_s?.length && preview.waveform_voltage_v?.length ? <>
                  <h4>任意波时域</h4>
                  <div className="known-noise-chart">
                    <ScopePlot
                      mode="time"
                      reset={0}
                      traces={[{ id: "Injected arbitrary waveform", color: "#147d74", x: preview.waveform_time_s, y: preview.waveform_voltage_v }]}
                    />
                  </div>
                </> : null}
                {preview?.spectrum_frequency_hz?.length && preview.target_psd_v2_per_hz?.length && preview.calculated_psd_v2_per_hz?.length ? <>
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
            {!sameParameterLayout(layout, savedLayout) && <p role="status">布局有未保存修改</p>}
            <button disabled={saving || jobActive || !fields.length} onClick={() => void save()}>
              {saving ? "保存中…" : "保存参数与布局"}
            </button>
            {notice && <p role="status">{notice}</p>}
          </section>

          <div className="noise-injection-side">
          <section className="zaw-panel zaw-panel-control">
            <div className="zaw-panel-head"><small>CONTROL</small><h2>控制与状态</h2></div>
            <div className="zaw-control-buttons">
              <button disabled={starting || jobActive || !fields.length} onClick={() => void check(false)}>仅预检</button>
              <button disabled={starting || jobActive || hardwareBusy || analysisActive || !fields.length} onClick={() => void check(true)}>
                {starting ? "处理中…" : "预检并运行实验"}
              </button>
            </div>
            {preflight && <div className={`alert ${preflight.ok ? "success" : "error"}`}>{preflight.ok ? "参数预检通过" : preflight.errors.join("；")}</div>}
            {hardwareBusy && !jobActive && <p>其他硬件任务正在运行。</p>}
            {definition?.wiring_notes.map((note) => <p key={note}>{note}</p>)}
            <JobView job={job} onUpdate={setJob} />
          </section>

          <section className="zaw-panel zaw-panel-result">
            <div className="zaw-panel-head"><small>LATEST</small><h2>最近一次结果</h2></div>
            {latest ? <><Result value={latest} probeAm={probeAm} /><button onClick={() => { setSelectedId(latest.run_id); setTab("history"); }}>查看本次详情</button></> : <p>暂无运行记录。</p>}
          </section>
          </div>
        </div>
      </div>

      <div id={`${prefix}-panel-history`} role="tabpanel" aria-labelledby={`${prefix}-tab-history`} hidden={tab !== "history"}>
        <div className="zaw-history">
          <section className="zaw-run-list">
            <h2>历史运行（{list.total}）</h2>
            <button onClick={() => setRefresh((value) => value + 1)}>刷新记录</button>
            <ul>{list.runs.map((run) => (
              <li key={run.run_id}><button className={`zaw-run-card ${selectedId === run.run_id ? "selected" : ""}`} onClick={() => setSelectedId(run.run_id)}><strong>{run.run_id}</strong><span>{run.timestamp}</span><span>{status(run.completion_status)}</span></button></li>
            ))}</ul>
            <button disabled={offset === 0} onClick={() => setOffset((value) => value - PAGE_SIZE)}>上一页</button>
            <button disabled={offset + PAGE_SIZE >= list.total} onClick={() => setOffset((value) => value + PAGE_SIZE)}>下一页</button>
          </section>
          <section className="zaw-run-details">
            {selected ? <>
              <div className="zaw-run-details-actions">
                <button disabled={jobActive || starting} onClick={() => fillBack(selected.parameters)}>填回参数</button>
                <button disabled={starting || jobActive || analysisActive || selected.completion_status === "running"} onClick={() => void reanalyze(selected.run_id)}>重新分析</button>
              </div>
              <Result value={selected} probeAm={probeAm} />
              <details><summary>本次参数</summary><table><tbody>{Object.entries(selected.parameters).map(([key, value]) => <tr key={key}><th>{fields.find((field) => field.name === key)?.label ?? key}</th><td>{String(value)}</td></tr>)}</tbody></table></details>
            </> : <p>{selectedId ? "正在读取运行详情…" : "暂无运行记录。"}</p>}
          </section>
        </div>
      </div>

      {tab === "architecture" && (
        <div id={`${prefix}-panel-architecture`} role="tabpanel" aria-labelledby={`${prefix}-tab-architecture`}>
          <ArchitectureView probeAm={probeAm} />
        </div>
      )}
      <JobView job={analysisJob} onUpdate={setAnalysisJob} />
    </>
  );
}

// “架构”Tab：内嵌本实验的 Archify 采集流程架构图，总览与三张子图按需加载。
const ARCHITECTURE_DIAGRAMS = [
  { id: "overview", file: "", tabLabel: "总览", title: "实验采集架构图" },
  { id: "acquisition", file: ".acquisition", tabLabel: "采集流程", title: "实验采集执行流程图" },
  { id: "timing", file: ".timing", tabLabel: "单点时序", title: "实验单控制点时序图" },
  { id: "dataflow", file: ".dataflow", tabLabel: "数据分析", title: "实验离线分析与差谱图" },
] as const;

const architectureBase = (probeAm: boolean) =>
  probeAm
    ? "/architecture/noise-spectrum-xy-uncontrolled-probe-am"
    : "/architecture/noise-spectrum-xy-known-noise";

const architectureTitles = (probeAm: boolean) =>
  probeAm
    ? {
        overview: "XY 已知不可控 Probe AM 噪声谱实验架构图",
        acquisition: "XY 已知不可控 Probe AM 噪声谱实验采集执行流程图",
        timing: "XY 已知不可控 Probe AM 噪声谱实验单控制点时序图",
        dataflow: "XY 已知不可控 Probe AM 噪声谱实验离线分析与差谱图",
        intro:
          "总览：外部 AM 周期噪声经 Probe AOM 调制 Probe 光，X/Y 正弦控制场逐点调幅重触发，" +
          "同一温控窗口内交替采集 AM OFF/ON 两态（可选持续注入 Z 可控噪声）；子图分别展开采集执行流程、" +
          "单控制点多仪器时序与离线两态差分分析的数据流判据。",
      }
    : {
        overview: "XY 已知可控噪声谱实验采集架构图",
        acquisition: "XY 已知可控噪声谱实验采集执行流程图",
        timing: "XY 已知可控噪声谱实验单控制点时序图",
        dataflow: "XY 已知可控噪声谱实验离线分析与真值链图",
        intro:
          "总览：在 Z 小线圈注入分段平顶谱已知伪噪声，X/Y 正弦控制场逐点调幅重触发，" +
          "同一温控窗口内交替采集注入 OFF/ON 两态；子图分别展开采集执行流程、单控制点" +
          "多仪器时序与离线差分分析的真值链判据。",
      };

function ArchitectureView({ probeAm }: { probeAm: boolean }) {
  const [diagram, setDiagram] = useState<(typeof ARCHITECTURE_DIAGRAMS)[number]>(ARCHITECTURE_DIAGRAMS[0]);
  const base = architectureBase(probeAm);
  const titles = architectureTitles(probeAm);
  const heading = titles[diagram.id];
  return (
    <section className="mx-arch">
      <div className="mx-arch-head">
        <div>
          <small>EXPERIMENT ARCHITECTURE</small>
          <h2>采集流程架构</h2>
          <p>{titles.intro}</p>
        </div>
        <a className="architecture-open" href={`${base}${diagram.file}.html`} target="_blank" rel="noreferrer">
          在新页面打开
        </a>
      </div>
      <div className="mx-arch-diagrams" role="tablist" aria-label="架构图选择">
        {ARCHITECTURE_DIAGRAMS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={diagram.id === item.id}
            className={diagram.id === item.id ? "active" : ""}
            onClick={() => setDiagram(item)}
          >
            {item.tabLabel}
          </button>
        ))}
      </div>
      <div className="architecture-frame">
        <iframe
          key={diagram.id}
          src={`${base}${diagram.file}.html`}
          title={heading}
          loading="lazy"
          sandbox="allow-scripts allow-same-origin allow-downloads allow-popups"
          allow="clipboard-write"
        />
      </div>
    </section>
  );
}
