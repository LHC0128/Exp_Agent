import { useEffect, useState } from "react";
import { api } from "../api";
import { runStatusLabel } from "../components/workflow/RunHistory";
import type { ParameterValues, SchemaField } from "../types/api";
import { WorkflowExperimentPage, type WorkflowRunSummary } from "./WorkflowExperimentPage";
const ID = "rf-control-scale-sensitivity";
const BASE = `/api/experiments/${ID}`;
type Metric = { control: string; scale: number; valid: boolean; control_scale_factor?: number; effective_control_scale?: number; slope_V_per_nT?: number; center_Hz?: number; dispersive_hwhm_Hz?: number; sensitivity_fT_per_sqrtHz?: number; band_exceeds_lorentzian_hwhm?: boolean; noise_records_total?: number; noise_records_rejected?: number; rejection_reason?: string };
const analysisKeys = { noise_nperseg: "NOISE_NPERSEG", noise_outlier_policy: "NOISE_OUTLIER_POLICY", band_min_hz: "SENSITIVITY_BAND_MIN_HZ", band_max_hz: "SENSITIVITY_BAND_MAX_HZ" };
const normalize = (values: ParameterValues): ParameterValues => {
  const factors = values.CONTROL_SCALE_FACTORS;
  const mapping = factors && typeof factors === "object" && !Array.isArray(factors) ? { ...factors } : {};
  for (const relative of Array.isArray(values.CONTROL_WAVEFORM_FILES) ? values.CONTROL_WAVEFORM_FILES : []) {
    const name = String(relative).split("/")[0];
    mapping[name] ??= 1;
  }
  const next: ParameterValues = { ...values, CONTROL_SCALE_FACTORS: mapping };
  for (const suffix of ["FREQ", "AMPLITUDE", "OFFSET", "DUTY", "PHASE"]) next[`Z_TRIGGER_${suffix}`] = values[`XY_TRIGGER_${suffix}`];
  return next;
};
const number = (value?: number | null) => typeof value === "number" && Number.isFinite(value) ? value.toPrecision(5) : "—";

const mapFields = (fields: SchemaField[], values: ParameterValues): SchemaField[] => fields.map(field => ({ ...field, read_only: field.read_only
  || ["Z_PHASE_SCAN_START_DEG", "Z_PHASE_SCAN_STOP_DEG", "Z_PHASE_SCAN_STEP_DEG"].includes(field.name) && values.Z_PHASE_SCAN_ENABLED === false
  || field.name === "RF_PHYSICAL_INPUT_SOURCE" && values.RF_INPUT_ROUTE === "internal"
  || ["LOOPBACK_INPUT_RANGE_V", "LOOPBACK_INPUT_DIFF"].includes(field.name) && !(values.RF_INPUT_ROUTE === "physical" && values.RF_PHYSICAL_INPUT_SOURCE === "voltage1") }));

function Result({ value }: { value: WorkflowRunSummary }) {
  const artifacts = value.artifacts ?? {};
  const [metrics, setMetrics] = useState<{ points?: Metric[]; sensitivity_assumption?: string; plot_labels?: Record<string, string> }>();
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    setMetrics(undefined); setError("");
    const url = artifacts["results/analysis.json"];
    if (url) void api<{ points: Metric[]; sensitivity_assumption: string }>(url).then(result => { if (!disposed) setMetrics(result); }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [value.run_id, artifacts["results/analysis.json"]]);
  return <div className="zaw-latest">
    <p><strong>{value.run_id}</strong> · {runStatusLabel(value.completion_status)} · 分析：{runStatusLabel(value.analysis_status)}</p>
    {value.parameters.Z_PHASE_SCAN_ENABLED === false && <p>RF 相位来源：全部控制 × Scale 共用手动相位 {String(value.parameters.Z_BURST_PHASE_INIT)}°；其余校相逐点独立执行。</p>}
    {value.analysis_error && <p className="alert error">{value.analysis_error}</p>}
    {error && <p className="alert error">{error}</p>}
    {metrics?.sensitivity_assumption && <p className="alert">{metrics.sensitivity_assumption}</p>}
    {metrics?.points && <div style={{ overflowX: "auto" }}><table><thead><tr><th>控制</th><th>扫描倍率</th><th>补偿倍率</th><th>实际总倍率</th><th>色散拟合斜率 V/nT</th><th>洛伦兹中心频率 Hz</th><th>色散 HWHM Hz</th><th>场等效噪声 fT/√Hz</th><th>剔除/总条数</th><th>状态</th></tr></thead><tbody>{metrics.points.map(row => <tr key={row.control + row.scale}><td>{row.control}</td><td>{row.scale}</td><td>{number(row.control_scale_factor ?? 1)}</td><td>{number(row.effective_control_scale ?? row.scale)}</td><td>{number(row.slope_V_per_nT)}</td><td>{number(row.center_Hz)}</td><td>{number(row.dispersive_hwhm_Hz)}</td><td>{number(row.sensitivity_fT_per_sqrtHz)}</td><td>{row.noise_records_total === undefined ? "—" : `${row.noise_records_rejected ?? 0}/${row.noise_records_total}`}</td><td>{row.valid ? row.band_exceeds_lorentzian_hwhm ? "有效（频带超过洛伦兹 HWHM）" : "有效" : row.rejection_reason ?? "无效"}</td></tr>)}</tbody></table></div>}
    {Object.entries(artifacts).filter(([name]) => name.endsWith(".png")).map(([name, url]) => <figure key={name} className="zaw-figure"><figcaption>{name}</figcaption><a href={url} download><img src={url} alt={name} loading="lazy" /></a></figure>)}
    <p className="xy-artifact-links">{Object.entries(artifacts).filter(([name]) => !name.endsWith(".png")).map(([name, url]) => <a key={name} href={url} download>{name}</a>)}</p>
  </div>;
}

export function RFControlScaleSensitivityPage() {
  return <WorkflowExperimentPage experimentId={ID}
    eyebrow="MEASUREMENT · RF SCALE"
    analysisParameterPrefix="" analysisParameterKeys={analysisKeys}
    normalizeParameters={normalize} mapFields={mapFields}
    analysisRunFilter={run => run.completion_status === "completed"}
    renderResult={value => <Result value={value} />}
    renderPreview={({ values }) => <div className="xy-workflow-preview">
      <p>{values.Z_PHASE_SCAN_ENABLED === false ? `跳过 RF 相位扫描：全部控制 × Scale 使用手动相位 ${String(values.Z_BURST_PHASE_INIT ?? "—")}°。` : "RF 相位扫描开启：每个控制 × Scale 独立扫描并选相。"}</p>
      <p aria-label="采集预览">所选控制：{String(values.CONTROL_WAVEFORM_FILES ?? "—")}；扫描倍率：{values.CONTROL_SCALE_START != null && values.CONTROL_SCALE_STOP != null && values.CONTROL_SCALE_POINTS != null ? `${values.CONTROL_SCALE_START} → ${values.CONTROL_SCALE_STOP}，${values.CONTROL_SCALE_POINTS} 点` : String(values.CONTROL_SCALES ?? "—")}；补偿倍率：{JSON.stringify(values.CONTROL_SCALE_FACTORS)}；噪声采集：{values.INCLUDE_NOISE === false ? "关闭" : "开启"}；所有点均测 R 扫频。</p>
    </div>}
    fillBack={async ({ summary }) => {
      const derived = await api<{ values: { parameters: ParameterValues } }>(BASE + "/derive", {
        method: "POST", body: JSON.stringify({ parameters: summary.parameters }) });
      return { values: derived.values.parameters };
    }} />;
}
