import { useEffect, useState, type KeyboardEvent } from "react";
import { api } from "../api";
import { PageHead } from "../components/PageHead";
import { recoverableJob, useExperimentTask, useRequestedJobId } from "../components/experimentNavigation";
import { useParameterGuard } from "../components/NavigationGuard";
import { JobView } from "../components/JobView";
import { useJobActivity } from "../components/JobActivity";
import { WorkflowPanel, type XYSession } from "../components/NoiseSpectrumXY/WorkflowPanel";
import { defaultParameterLayout, sameParameterLayout, validatedParameterLayout, type PreflightResult } from "./experimentHelpers";
import type { ExperimentDefinition, ExperimentSchema, Job, JobSummary, ParameterLayout, ParameterValues } from "../types/api";
import type { components } from "../types/openapi";
import "./zawClosedLoop.css";
import "./rfDirectAW.css";

const ID = "rf-control-scale-sensitivity";
const BASE = `/api/experiments/${ID}`;
type Runs = components["schemas"]["WorkflowRuns"];
type Summary = components["schemas"]["WorkflowRunSummary"];
type Metric = { control: string; scale: number; valid: boolean; control_scale_factor?: number; effective_control_scale?: number; slope_V_per_nT?: number; center_Hz?: number; dispersive_hwhm_Hz?: number; sensitivity_fT_per_sqrtHz?: number; band_exceeds_lorentzian_hwhm?: boolean; noise_records_total?: number; noise_records_rejected?: number; rejection_reason?: string };
const active = (job?: Job) => Boolean(job && ["queued", "running"].includes(job.status));
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

function Result({ value }: { value: Summary }) {
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
    <p><strong>{value.run_id}</strong> · {value.completion_status} · 分析：{value.analysis_status}</p>
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
  const [tab, setTab] = useState<"run" | "history">("run");
  const [definition, setDefinition] = useState<ExperimentDefinition>();
  const [fields, setFields] = useState<ExperimentSchema["fields"]>([]);
  const [values, setValues] = useState<ParameterValues>({});
  const [layout, setLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [savedLayout, setSavedLayout] = useState(layout);
  const [session, setSession] = useState<XYSession>();
  const [job, setJob] = useState<Job>();
  const [analysisJob, setAnalysisJob] = useState<Job>();
  const [runs, setRuns] = useState<Runs>({ runs: [], total: 0 });
  const [workflowRuns, setWorkflowRuns] = useState<Runs["runs"]>([]);
  const [selected, setSelected] = useState<Summary>();
  const [latest, setLatest] = useState<Summary>();
  const [selectedId, setSelectedId] = useState("");
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const { hardwareBusy } = useJobActivity();
  const live = Boolean(session && session.status !== "closed");
  useParameterGuard(fields, values, layout, savedLayout, live ? session?.parameters as ParameterValues : undefined);
  useExperimentTask({ ready: fields.length > 0, setTab, setJob, setAnalysisJob, setSelectedId });
  const requestedJobId = useRequestedJobId();
  const busy = pending || active(analysisJob) || (live ? session?.status !== "waiting" || Boolean(session.pending_request) : active(job));
  const routeFields = fields.map(field => ({ ...field, read_only: field.read_only
    || ["Z_PHASE_SCAN_START_DEG", "Z_PHASE_SCAN_STOP_DEG", "Z_PHASE_SCAN_STEP_DEG"].includes(field.name) && values.Z_PHASE_SCAN_ENABLED === false
    || field.name === "RF_PHYSICAL_INPUT_SOURCE" && values.RF_INPUT_ROUTE === "internal"
    || ["LOOPBACK_INPUT_RANGE_V", "LOOPBACK_INPUT_DIFF"].includes(field.name) && !(values.RF_INPUT_ROUTE === "physical" && values.RF_PHYSICAL_INPUT_SOURCE === "voltage1") }));

  useEffect(() => {
    let disposed = false;
    void Promise.all([api<ExperimentDefinition>(BASE), api<ExperimentSchema>(BASE + "/schema"), api<JobSummary[]>("/api/jobs")]).then(async ([def, schema, jobs]) => {
      if (disposed) return;
      setDefinition(def); setFields(schema.fields);
      setValues(normalize(Object.fromEntries(schema.fields.map(field => [field.name, field.default]))));
      const initial = validatedParameterLayout(schema.fields, schema.parameter_layout) ?? defaultParameterLayout(schema.fields);
      setLayout(initial); setSavedLayout(initial);
      const own = recoverableJob(jobs, ID, requestedJobId);
      if (own) { const detail = await api<Job>(`/api/jobs/${own.id}`); if (!disposed) setJob(detail); }
      const analysis = recoverableJob(jobs, ID, requestedJobId, "analysis");
      if (analysis) { const detail = await api<Job>(`/api/jobs/${analysis.id}`); if (!disposed) setAnalysisJob(detail); }
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    let disposed = false;
    void Promise.all([api<Runs>(`${BASE}/runs?limit=50&offset=${offset}`), api<Runs>(BASE + "/runs?limit=500&offset=0")]).then(async ([page, all]) => {
      if (disposed) return;
      setRuns(page); setWorkflowRuns(all.runs);
      const last = all.runs[0];
      if (last) {
        const summary = await api<Summary>(`${BASE}/runs/${encodeURIComponent(last.run_id)}/summary`);
        if (!disposed) { setLatest(summary); setSelectedId(old => old || last.run_id); }
      }
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [offset, refresh, job?.status, analysisJob?.status]);

  useEffect(() => {
    let disposed = false;
    setSelected(undefined);
    if (selectedId) void api<Summary>(`${BASE}/runs/${encodeURIComponent(selectedId)}/summary`).then(result => { if (!disposed) setSelected(result); }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [selectedId, refresh, analysisJob?.status]);

  const save = async () => {
    setPending(true); setError("");
    try {
      const result = await api<{ schema: ExperimentSchema }>(BASE + "/defaults", { method: "PUT", body: JSON.stringify({ parameters: normalize(values), parameter_layout: layout }) });
      const confirmed = validatedParameterLayout(result.schema.fields, result.schema.parameter_layout);
      if (!confirmed || !sameParameterLayout(confirmed, layout)) throw new Error("后端未确认参数布局保存");
      setSavedLayout(confirmed); setFields(result.schema.fields); setNotice("参数与布局已保存，仅影响下一次实验");
    } catch (reason) { setError(String(reason)); } finally { setPending(false); }
  };
  const check = async (run: boolean) => {
    setPending(true); setError("");
    try {
      const result = await api<PreflightResult>(BASE + "/preflight", { method: "POST", body: JSON.stringify({ parameters: normalize(values) }) });
      if (!result.ok) { setError(result.errors.join("；")); return; }
      if (run) setJob(await api<Job>(BASE + "/runs", { method: "POST", body: JSON.stringify({ parameters: normalize(values) }) }));
      else setNotice("预检通过，未连接仪器");
    } catch (reason) { setError(String(reason)); } finally { setPending(false); }
  };
  const fillParameters = async () => {
    if (!selected) return;
    setPending(true); setError("");
    try {
      const result = await api<{ values: { parameters: ParameterValues } }>(BASE + "/derive", {
        method: "POST", body: JSON.stringify({ parameters: selected.parameters }) });
      setValues(normalize(result.values.parameters)); setTab("run");
    } catch (reason) { setError(String(reason)); } finally { setPending(false); }
  };
  const reanalyze = async () => {
    if (!selected) return;
    setError("");
    try { setAnalysisJob(await api<Job>(`/api/runs/${encodeURIComponent(selected.run_id)}/analyze`, { method: "POST", body: JSON.stringify({ experiment_id: ID }) })); }
    catch (reason) { setError(String(reason)); }
  };
  const tabKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? "run" : event.key === "End" ? "history" : tab === "run" ? "history" : "run";
    setTab(next); document.getElementById(`scale-tab-${next}`)?.focus();
  };

  return <>
    <PageHead eyebrow="MEASUREMENT · RF SCALE" title={definition?.title ?? "多控制 Scale RF 响应与灵敏度"} description={definition?.description ?? "读取控制、参数与运行记录。"} experiment={definition} />
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="zaw-tab-strip" role="tablist" aria-label="Scale 实验视图" onKeyDown={tabKey}>{(["run", "history"] as const).map(name => <button key={name} id={`scale-tab-${name}`} role="tab" aria-selected={tab === name} tabIndex={tab === name ? 0 : -1} aria-controls={`scale-panel-${name}`} onClick={() => setTab(name)}>{name === "run" ? "运行" : "历史"}</button>)}</div>
    <div id="scale-panel-run" role="tabpanel" aria-labelledby="scale-tab-run" hidden={tab !== "run"}>
      <WorkflowPanel experimentId={ID} finishStepId="A16" runStepId="A15" analysisParameterPrefix="" analysisParameterKeys={analysisKeys}
        normalizeParameters={normalize} enabled={fields.length > 0} fields={routeFields} layout={layout} setLayout={setLayout}
        setNotice={setNotice} parametersDisabled={busy} values={values} setValues={setValues} session={session} setSession={setSession}
        job={job} setJob={setJob} analysisJob={analysisJob} setAnalysisJob={setAnalysisJob} hardwareBusy={hardwareBusy}
        runs={workflowRuns.filter(item => item.completion_status === "completed")} setError={setError} onRefresh={setRefresh}>
        <div className="xy-workflow-controls">
          <div className="xy-flow-actions">
            <button disabled={busy || !fields.length} onClick={() => void save()}>保存参数与布局</button>
            <button disabled={busy || !fields.length} onClick={() => void check(false)}>仅预检</button>
            <button disabled={busy || live || hardwareBusy || !fields.length} onClick={() => void check(true)}>预检并运行实验</button>
          </div>
          <p>在 A13 节点编辑扫描开关和手动 RF 相位。每个控制 × Scale 独立校相；关闭 RF 相位扫描时共用本实验手动相位，其余校相仍逐点执行。不注入人为噪声，不自动追峰；分步等待仍占用硬件锁。</p>
          <p>{values.Z_PHASE_SCAN_ENABLED === false ? `跳过 RF 相位扫描：全部控制 × Scale 使用手动相位 ${String(values.Z_BURST_PHASE_INIT ?? "—")}°。` : "RF 相位扫描开启：每个控制 × Scale 独立扫描并选相。"}</p>
          <p aria-label="采集预览">所选控制：{String(values.CONTROL_WAVEFORM_FILES ?? "—")}；扫描倍率：{values.CONTROL_SCALE_START != null && values.CONTROL_SCALE_STOP != null && values.CONTROL_SCALE_POINTS != null ? `${values.CONTROL_SCALE_START} → ${values.CONTROL_SCALE_STOP}，${values.CONTROL_SCALE_POINTS} 点` : String(values.CONTROL_SCALES ?? "—")}；补偿倍率：{JSON.stringify(values.CONTROL_SCALE_FACTORS)}；噪声采集：{values.INCLUDE_NOISE === false ? "关闭" : "开启"}；所有点均测 R 扫频。</p>
          <p>在 A13 设置扫描倍率起点、终点和点数，以及各控制补偿倍率（默认 1）。实际包络乘两种倍率，图横轴仍用扫描倍率；未测扫描倍率 1 时不做归一化。电压安全和校相质量门槛不变。</p>
          <p>保存默认值不改历史分析草稿；应用会话草稿后需显式重跑。{!sameParameterLayout(layout, savedLayout) && "布局有未保存修改。"}</p>
          {notice && <p role="status">{notice}</p>}
          {definition?.wiring_notes.map(note => <p key={note}>{note}</p>)}
          <JobView job={job} onUpdate={setJob} />
        </div>
      </WorkflowPanel>
      <section className="zaw-panel zaw-panel-result"><h2>最近一次结果</h2>{latest ? <Result value={latest} /> : <p>暂无运行记录。</p>}</section>
    </div>
    <div id="scale-panel-history" role="tabpanel" aria-labelledby="scale-tab-history" hidden={tab !== "history"}>
      <div className="zaw-history"><section className="zaw-run-list"><h3>历史运行（{runs.total}）</h3>
        <button onClick={() => setRefresh(value => value + 1)}>刷新记录</button>
        <ul>{runs.runs.map(run => <li key={run.run_id}><button className="zaw-run-card" onClick={() => setSelectedId(run.run_id)}><strong>{run.run_id}</strong><span>{run.timestamp} · {run.completion_status}</span></button></li>)}</ul>
        <button disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - 50))}>上一页</button>
        <button disabled={offset + 50 >= runs.total} onClick={() => setOffset(value => value + 50)}>下一页</button>
      </section><section className="zaw-run-details">{selected ? <>
        <button disabled={busy || live} onClick={() => void fillParameters()}>填回参数</button>
        <button disabled={busy || live || selected.completion_status !== "completed"} onClick={() => void reanalyze()}>重新分析</button>
        <Result value={selected} /><details><summary>运行参数快照</summary><pre>{JSON.stringify(selected.parameters, null, 2)}</pre></details>
      </> : <p>请选择历史运行。</p>}</section></div>
    </div>
    <JobView job={analysisJob} onUpdate={setAnalysisJob} />
  </>;
}
