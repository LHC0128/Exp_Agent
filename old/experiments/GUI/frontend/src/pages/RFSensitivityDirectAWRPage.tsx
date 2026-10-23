import { historyParameterValues } from "./experimentHelpers";
import { preflightMessage, type PreflightResult } from "./experimentHelpers";
import { useEffect, useState, type KeyboardEvent } from "react";
import { api } from "../api";
import { PageHead } from "../components/PageHead";
import { recoverableJob, useExperimentTask, useRequestedJobId } from "../components/experimentNavigation";
import { useParameterGuard, useUnsavedChanges } from "../components/NavigationGuard";
import { JobView } from "../components/JobView";
import { useJobActivity } from "../components/JobActivity";
import { WorkflowPanel, type XYSession } from "../components/NoiseSpectrumXY/WorkflowPanel";
import { defaultParameterLayout, sameParameterLayout, validatedParameterLayout } from "./experimentHelpers";
import type { ExperimentDefinition, ExperimentSchema, Job, JobSummary, ParameterLayout, ParameterValues } from "../types/api";
import type { components } from "../types/openapi";
import "./zawClosedLoop.css";
import "./rfDirectAW.css";

const analysisParameterKeys = { noise_nperseg: "NOISE_NPERSEG", noise_outlier_policy: "NOISE_OUTLIER_POLICY",
  band_min_hz: "SENSITIVITY_BAND_MIN_HZ", band_max_hz: "SENSITIVITY_BAND_MAX_HZ" };
const terminal = (job: Job | undefined) => Boolean(job && ["completed", "failed", "cancelled"].includes(job.status));
type RunList = components["schemas"]["RFDirectAWRunsResponse"];
type RunItem = components["schemas"]["RFDirectAWRunItem"];
type Summary = components["schemas"]["RFDirectAWRunSummary"];
type ComparisonRun = components["schemas"]["RFDirectAWComparisonRun"];
type ComparisonResult = { runs: ComparisonRun[]; warnings: string[]; artifacts: Record<string, string> };
const invalidLegend = (legend = "") => /[\u3400-\u9fff\uf900-\ufaff\x00-\x1f\x7f]/.test(legend);
const comparisonSelection = (runs: ComparisonRun[]) => runs.map(({ run_id, legend }, index) => ({
  run_id, legend: legend?.trim() || (invalidLegend(run_id) ? `Run ${run_id.match(/^(\d{4}_\d{4}(?:\d{2})?)_/)?.[1] ?? index + 1}` : run_id),
}));

function commonTriggerParameters(values: ParameterValues): ParameterValues {
  const next = { ...values };
  for (const suffix of ["FREQ", "AMPLITUDE", "OFFSET", "DUTY", "PHASE"]) {
    next[`Z_TRIGGER_${suffix}`] = values[`XY_TRIGGER_${suffix}`];
  }
  return next;
}

function historyParameters(values: ParameterValues, multiControl: boolean): ParameterValues {
  const next = { ...values };
  if (multiControl && !Array.isArray(next.CONTROL_WAVEFORM_FILES)) {
    next.CONTROL_WAVEFORM_FILES = typeof next.ARB_WAVEFORM_FILE === "string" && next.ARB_WAVEFORM_FILE ? [next.ARB_WAVEFORM_FILE] : [];
  }
  if (!["RF_INPUT_ROUTE", "RF_AUX_OUTPUT_IDX", "RF_PHYSICAL_INPUT_SOURCE"].some(key => key in next)) {
    Object.assign(next, { RF_INPUT_ROUTE: "physical", RF_AUX_OUTPUT_IDX: 1, RF_PHYSICAL_INPUT_SOURCE: "voltage1" });
  }
  return next;
}

const statusLabels: Record<string, string> = {
  completed: "完成",
  failed: "失败",
  cancelled: "已取消",
  running: "运行中",
  not_started: "未分析",
  acquired: "已采集，待安全收尾",
  partial: "部分分析完成"
};
const status = (value: string) => statusLabels[value] ?? value;

type NoiseQuality = { n_records?: number; n_accepted?: number; n_rejected?: number; reason?: string; policy?: string };

function Result({ value }: { value: Summary }) {
  const component = value.response_component ?? "X";
  const controls = value.analysis?.controls as Record<string, { response_fit?: { slope_V_per_nT?: number }; sensitivity?: { flat_median_fT_per_sqrtHz?: number }; noise_rejection?: NoiseQuality }> | undefined;
  const quality = value.analysis?.noise_rejection as NoiseQuality | undefined;
  const caption = component === "R" ? "R 标定 / X 噪声与 RF 灵敏度" : "X 响应与 RF 灵敏度";
  const fit = value.analysis?.response_fit as { method?: string; success?: boolean; slope_V_per_nT?: number; failure_reason?: string } | undefined;
  const sensitivity = value.analysis?.sensitivity as { flat_median_fT_per_sqrtHz?: number | null; flat_fmin_Hz?: number | null; flat_fmax_Hz?: number | null } | undefined;
  const correction = value.analysis?.frequency_response_correction as { applied?: boolean; hwhm_Hz?: number | null } | undefined;
  const correctionText = correction?.applied && typeof correction.hwhm_Hz === "number"
    ? `已作洛伦兹校正，HWHM ${correction.hwhm_Hz.toFixed(0)} Hz；平坦段 ${sensitivity?.flat_fmin_Hz?.toFixed(0) ?? "—"}–${sensitivity?.flat_fmax_Hz?.toFixed(0) ?? "—"} Hz`
    : "未作洛伦兹校正";
  return <div className="zaw-latest">
    <p><strong>{value.run_id}</strong> · {value.timestamp}</p>
    <p>采集：{status(value.completion_status)} · 校相：{status(value.phase_calibration_status)} · 分析：{status(value.analysis_status)}</p>
    <p>RF 解调器索引：{String(value.parameters.DEMOD3_IDX ?? 3)}{value.signal_chain && ` · ${value.signal_chain}`}</p>
    {value.parameters.Z_PHASE_SCAN_ENABLED === false && <p>RF 相位来源：手动 {String(value.parameters.Z_BURST_PHASE_INIT)}°；未执行相位扫描。</p>}
    {value.temperature_control?.control_source === "external_software" && <p className="alert">外部温控：程序未设置或检测温度，请确认外部软件维持目标温度。</p>}
    {value.failure_reason && <div className="alert error">{value.failure_reason}</div>}
    {value.analysis_error && <div className="alert error">{value.analysis_error}</div>}
    {!value.can_analyze && <p>{value.analysis_block_reason}</p>}
    {component === "R" && <p>R 标定 / X 噪声 · 响应 {String(value.parameters.Z_RF_FREQ)} Hz · 校相/噪声 {String(value.parameters.Z_RF_NOISE_FREQ)} Hz</p>}
    {fit && !controls && <p>{component === "R" ? "R 标定底层中心斜率" : "X 斜率"}：{fit.slope_V_per_nT?.toPrecision(5)} V/nT · {fit.success ? component === "R" ? "绝对色散拟合" : "色散拟合" : "数值斜率估算回退"}{fit.failure_reason && `（${fit.failure_reason}）`}</p>}
    {controls && <table><thead><tr><th>控制版本</th><th>响应斜率（V/nT）</th><th>灵敏度中位数（fT/√Hz）</th><th>PSD 保留／总数</th></tr></thead><tbody>{Object.entries(controls).map(([version, data]) => <tr key={version}><td>{version}</td><td>{data.response_fit?.slope_V_per_nT?.toPrecision(5) ?? "—"}</td><td>{data.sensitivity?.flat_median_fT_per_sqrtHz?.toPrecision(5) ?? "—"}</td><td title={data.noise_rejection?.reason}>{data.noise_rejection?.n_accepted ?? "—"}／{data.noise_rejection?.n_records ?? "—"}</td></tr>)}</tbody></table>}
    {controls && <ul>{Object.entries(controls).filter(([, data]) => data.noise_rejection?.reason).map(([version, data]) => <li key={version}>PSD 提示（{version}）：{data.noise_rejection?.reason}</li>)}</ul>}
    {typeof quality?.n_records === "number" && <p>PSD 异常谱筛选：保留 {quality.n_accepted}／{quality.n_records} 条，剔除 {quality.n_rejected} 条。{quality.reason}原始数据、未剔除均值和逐记录清单均保留。</p>}
    {typeof sensitivity?.flat_median_fT_per_sqrtHz === "number" && <p>灵敏度中位数：{sensitivity.flat_median_fT_per_sqrtHz.toPrecision(5)} fT/√Hz（{correctionText}）</p>}
    {component === "R" && <p className="alert">换算假定响应与噪声频率的增益一致；增益一致性和等效线宽补偿待实测验证。</p>}
    {value.artifacts["full_analysis.png"] ? <figure className="zaw-figure"><figcaption>{caption}</figcaption><img src={value.artifacts["full_analysis.png"]} alt={caption} /></figure> : <p className="zaw-empty">本次运行暂无结果图。</p>}
    {value.artifacts["z_phase_response_comparison.png"] && <figure className="zaw-figure"><figcaption>各控制版本的 Z RF 相位响应</figcaption><img src={value.artifacts["z_phase_response_comparison.png"]} alt="各控制版本的 Z RF 相位响应" /></figure>}
    {controls && Object.keys(controls).map(version => {
      const image = value.artifacts[`controls/${version}/full_analysis.png`];
      const pdf = value.artifacts[`controls/${version}/full_analysis.pdf`];
      const label = `${version}：X/Y/R 响应与 RF 灵敏度`;
      return image ? <figure className="zaw-figure" key={version}>
        <figcaption>{label}{pdf && <> · <a href={pdf} download={`${version}_full_analysis.pdf`}>下载 {version} PDF</a></>}</figcaption>
        <a href={image} download={`${version}_full_analysis.png`}><img src={image} alt={label} loading="lazy" /></a>
      </figure> : null;
    })}
    <details><summary>全部结果图</summary>{["response_curve.png", "noise_psd.png", "sensitivity.png"].filter(name => value.artifacts[name]).map(name => <figure className="zaw-figure" key={name}><figcaption>{name}</figcaption><img src={value.artifacts[name]} alt={name} /></figure>)}</details>
    <p className="xy-artifact-links">{Object.entries(value.artifacts).filter(([name]) => !name.startsWith("controls/") && /\.(npz|json|yaml|pdf|csv)$/.test(name)).map(([name, url]) => <a key={name} href={url} download>{name}</a>)}</p>
    <details><summary>校相与安全收尾诊断</summary><pre>{JSON.stringify({ xy_phase_calibration: value.xy_phase_calibration, safety_shutdown: value.safety_shutdown }, null, 2)}</pre></details>
    <details><summary>实际采样率与锁相回读</summary><pre>{JSON.stringify({ actual_rates: value.actual_rates, stage_frequencies: value.stage_frequencies, response: value.hf2_response_snapshot, calibration: value.hf2_calibration_snapshot, noise: value.hf2_noise_snapshot }, null, 2)}</pre></details>
  </div>;
}

export function RFSensitivityDirectAWPage({ experimentId = "rf-sensitivity-aw" }: { experimentId?: "rf-sensitivity-aw" | "rf-sensitivity-aw-r" | "rf-sensitivity-aw-known-noise" }) {
  const ID = experimentId;
  const BASE = `/api/experiments/${ID}`;
  const isR = ID === "rf-sensitivity-aw-r";
  const isKnownNoise = ID === "rf-sensitivity-aw-known-noise";
  const canManageRuns = ID === "rf-sensitivity-aw";
  const title = isKnownNoise ? "已知噪声下 RF 灵敏度（DirectAW）" : isR ? "RF 灵敏度（DirectAW R）" : "RF 灵敏度（DirectAW）";
  const responseLabel = isR ? "R 标定 / X 噪声" : "X 响应";
  const summaryUrl = (runId: string) => `${BASE}/runs/${encodeURIComponent(runId)}/summary`;
  const [tab, setTab] = useState<"run" | "history">("run");
  const [definition, setDefinition] = useState<ExperimentDefinition>();
  const [fields, setFields] = useState<ExperimentSchema["fields"]>([]);
  const [values, setValues] = useState<ParameterValues>({});
  const [layout, setLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [savedLayout, setSavedLayout] = useState(layout);
  const [session, setSession] = useState<XYSession>();
  const [job, setJob] = useState<Job>();
  const [analysisJob, setAnalysisJob] = useState<Job>();
  const [comparisonJob, setComparisonJob] = useState<Job>();
  const [comparisonRuns, setComparisonRuns] = useState<ComparisonRun[]>([]);
  const [comparisonStarting, setComparisonStarting] = useState(false);
  const [historyView, setHistoryView] = useState<"single" | "comparison">("single");
  const [starting, setStarting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [latest, setLatest] = useState<Summary>();
  const [list, setList] = useState<RunList>({ runs: [], total: 0, limit: 50, offset: 0 });
  const [workflowRuns, setWorkflowRuns] = useState<RunItem[]>([]);
  const [starredOnly, setStarredOnly] = useState(false);
  const [managingRun, setManagingRun] = useState("");
  const [renamingId, setRenamingId] = useState("");
  const [renameName, setRenameName] = useState("");
  const [historyNotice, setHistoryNotice] = useState("");
  const [runDirectoryChange, setRunDirectoryChange] = useState<{ from: string; to: string | null }>();
  const [comparisonInvalidated, setComparisonInvalidated] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [selected, setSelected] = useState<Summary>();
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const { hardwareBusy } = useJobActivity();
  const jobActive = Boolean(job && !terminal(job));
  const analysisActive = Boolean(analysisJob && !terminal(analysisJob));
  const comparisonActive = Boolean(comparisonJob && !terminal(comparisonJob));
  const sessionBusy = Boolean(session && session.status !== "closed" && (session.status !== "waiting" || session.pending_request));
  const parametersBusy = jobActive && (!session || session.status === "closed") || sessionBusy;
  const routeFields = fields.map(field => ({ ...field, read_only: field.read_only
    || ["Z_PHASE_SCAN_START_DEG", "Z_PHASE_SCAN_STOP_DEG", "Z_PHASE_SCAN_STEP_DEG"].includes(field.name)
      && values.Z_PHASE_SCAN_ENABLED === false
    || field.name === "RF_PHYSICAL_INPUT_SOURCE" && values.RF_INPUT_ROUTE === "internal"
    || ["LOOPBACK_INPUT_RANGE_V", "LOOPBACK_INPUT_DIFF"].includes(field.name)
      && !(values.RF_INPUT_ROUTE === "physical" && values.RF_PHYSICAL_INPUT_SOURCE === "voltage1") }));
  const comparisonBusy = comparisonActive || comparisonStarting;
  const comparisonResult = comparisonJob?.status === "completed" ? comparisonJob.result as ComparisonResult | undefined : undefined;
  const comparisonDirty = Boolean(comparisonResult && (comparisonInvalidated || JSON.stringify(comparisonSelection(comparisonRuns)) !== JSON.stringify(comparisonSelection(comparisonResult.runs))));
  const directoryBusy = Boolean(managingRun || jobActive || analysisActive || comparisonBusy || starting || session && session.status !== "closed");
  const legendsInvalid = comparisonRuns.some(item => invalidLegend(item.legend));
  useParameterGuard(fields, values, layout, savedLayout, session && session.status !== "closed" ? session.parameters as ParameterValues : undefined);
  useUnsavedChanges(Boolean(renamingId && renameName));
  useExperimentTask({ ready: fields.length > 0, setTab, setJob, setAnalysisJob, setComparisonJob, setSelectedId, setHistoryView });
  const requestedJobId = useRequestedJobId();

  useEffect(() => {
    let disposed = false;
    Promise.all([
      api<ExperimentDefinition>(BASE),
      api<ExperimentSchema>(`${BASE}/schema`),
      api<JobSummary[]>("/api/jobs")
    ]).then(async ([def, schema, jobs]) => {
      if (disposed) return;
      setDefinition(def);
      setFields(schema.fields);
      setValues(commonTriggerParameters(Object.fromEntries(schema.fields.map(field => [field.name, field.default]))));
      const initial = validatedParameterLayout(schema.fields, schema.parameter_layout) ?? defaultParameterLayout(schema.fields);
      setLayout(initial);
      setSavedLayout(initial);
      const activeComparison = recoverableJob(jobs, ID, requestedJobId, "comparison");
      if (activeComparison) {
        const recovered = await api<Job>(`/api/jobs/${activeComparison.id}`);
        if (!disposed) {
          setComparisonJob(recovered);
          setHistoryView("comparison");
        }
      }
      const activeAnalysis = recoverableJob(jobs, ID, requestedJobId, "analysis");
      if (activeAnalysis) {
        const recovered = await api<Job>(`/api/jobs/${activeAnalysis.id}`);
        if (!disposed) setAnalysisJob(recovered);
      }
      const active = recoverableJob(jobs, ID, requestedJobId);
      if (active) {
        const recovered = await api<Job>(`/api/jobs/${active.id}`);
        if (!disposed) setJob(recovered);
      }
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [BASE, ID]);

  useEffect(() => {
    if (comparisonJob?.status !== "completed") return;
    const result = comparisonJob.result as ComparisonResult | undefined;
    if (result?.runs) setComparisonRuns(current => current.length ? current : result.runs.map(({ run_id, legend }) => ({ run_id, legend })));
  }, [comparisonJob?.status, comparisonJob?.result]);

  useEffect(() => {
    let disposed = false;
    api<RunList>(`${BASE}/runs?limit=50&offset=0`).then(async data => {
      if (!disposed) setWorkflowRuns(data.runs);
      const result = data.runs.length ? await api<Summary>(summaryUrl(data.runs[0].run_id)) : undefined;
      if (!disposed) {
        setLatest(result);
        setSelectedId(current => current || result?.run_id || "");
      }
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [job?.status, analysisJob?.status, refresh]);

  useEffect(() => {
    let disposed = false;
    api<RunList>(`${BASE}/runs?limit=50&offset=${offset}${starredOnly && !isR ? "&starred_only=true" : ""}`).then(data => {
      if (!disposed) {
        if (offset && offset >= data.total) {
          setOffset(Math.max(0, Math.ceil(data.total / 50) - 1) * 50);
          return;
        }
        setList(data);
        if (!starredOnly) setWorkflowRuns(data.runs);
      }
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [job?.status, analysisJob?.status, offset, refresh, starredOnly]);

  useEffect(() => {
    if (!selectedId) { setSelected(undefined); return; }
    let disposed = false;
    setSelected(undefined);
    api<Summary>(summaryUrl(selectedId)).then(data => {
      if (!disposed) setSelected(data);
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [selectedId, job?.status, analysisJob?.status, refresh]);

  useEffect(() => { setRenamingId(""); }, [selectedId]);

  const manageRun = async (runId: string, action: "star" | "rename" | "delete", payload?: object) => {
    if (managingRun || isR) return;
    if (action === "delete" && !window.confirm(`将永久删除 ${runId} 的参数、raw/ 原始数据和 results/ 分析结果。此操作不可恢复，是否删除？`)) return;
    setManagingRun(runId);
    setError("");
    setHistoryNotice("");
    try {
      const updated = action === "delete" ? undefined : await api<RunItem>(`${BASE}/runs/${encodeURIComponent(runId)}/${action}`, {
        method: action === "star" ? "PUT" : "POST", body: JSON.stringify(payload),
      });
      if (action === "delete") await api(`/api/runs/${ID}/${encodeURIComponent(runId)}`, { method: "DELETE" });
      setList(current => ({ ...current, runs: current.runs.flatMap(item => item.run_id !== runId ? [item]
        : updated && (!starredOnly || updated.starred) ? [updated] : []) }));
      if (action === "star") setSelected(current => current?.run_id === runId ? { ...current, ...updated } : current);
      if (action !== "star") {
        const nextId = updated?.run_id ?? null;
        setRunDirectoryChange({ from: runId, to: nextId });
        setSelectedId(current => current === runId ? nextId ?? "" : current);
        setSelected(current => current?.run_id === runId ? undefined : current);
        setWorkflowRuns(current => updated ? current.map(item => item.run_id === runId ? updated : item) : current.filter(item => item.run_id !== runId));
        setSession(current => current?.run_id === runId ? { ...current, run_id: nextId } : current);
        setComparisonRuns(current => updated ? current.map(item => item.run_id === runId ? { ...item, run_id: updated.run_id } : item) : current.filter(item => item.run_id !== runId));
        if (comparisonResult?.runs.some(item => item.run_id === runId)) setComparisonInvalidated(true);
        setRenamingId("");
      }
      setRefresh(current => current + 1);
      setHistoryNotice(action === "delete" ? "本次实验数据已永久删除。" : action === "rename" ? `已重命名为 ${updated!.run_id}` : updated!.starred ? "已星标本次实验。" : "已取消星标。");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setManagingRun("");
    }
  };

  const check = async (start: boolean) => {
    setStarting(true);
    setError("");
    setNotice("");
    try {
      const result = await api<PreflightResult>(`${BASE}/preflight`, {
        method: "POST",
        body: JSON.stringify({ parameters: commonTriggerParameters(values) })
      });
      if (!result.ok) {
        setError(result.errors.join("；"));
        return;
      }
      if (start) {
        setJob(await api<Job>(`${BASE}/runs`, {
          method: "POST",
          body: JSON.stringify({ parameters: commonTriggerParameters(values) })
        }));
      } else {
        setNotice(preflightMessage(result, "预检通过"));
      }
    } catch (reason) {
      setError(String(reason));
    } finally {
      setStarting(false);
    }
  };

  const save = async () => {
    setError("");
    setSaving(true);
    try {
      const result = await api<{ schema: ExperimentSchema }>(`${BASE}/defaults`, {
        method: "PUT",
        body: JSON.stringify({ parameters: commonTriggerParameters(values), parameter_layout: layout })
      });
      const persisted = validatedParameterLayout(result.schema.fields, result.schema.parameter_layout);
      if (!persisted || !sameParameterLayout(persisted, layout)) throw new Error("后端未确认参数布局已保存");
      setSavedLayout(persisted); setFields(result.schema.fields);
      setNotice("参数与布局已保存");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSaving(false);
    }
  };

  const reanalyze = async (runId: string) => {
    if (analysisActive || jobActive || comparisonBusy) return;
    setError("");
    try {
      setAnalysisJob(await api<Job>(`/api/runs/${encodeURIComponent(runId)}/analyze`, {
        method: "POST", body: JSON.stringify({ experiment_id: ID })
      }));
    } catch (reason) { setError(String(reason)); }
  };

  const generateComparison = async () => {
    if (comparisonBusy || analysisActive || comparisonRuns.length < 2 || legendsInvalid) return;
    setComparisonStarting(true);
    setError("");
    try {
      setComparisonJob(await api<Job>(`${BASE}/compare`, {
        method: "POST", body: JSON.stringify({ runs: comparisonSelection(comparisonRuns) })
      }));
      setComparisonInvalidated(false);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setComparisonStarting(false);
    }
  };

  const tabKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const order = ["run", "history"] as const;
    const index = order.indexOf(tab as typeof order[number]);
    const next = order[(index + (event.key === "ArrowRight" ? 1 : order.length - 1)) % order.length];
    setTab(next);
    event.currentTarget.querySelector<HTMLButtonElement>(`#rf-aw-tab-${next}`)?.focus();
  };

  return <>
    <PageHead eyebrow="MEASUREMENT · RF" title={definition?.title ?? title} description={definition?.description ?? "读取参数与运行记录。"} experiment={definition} />
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="zaw-tab-strip" role="tablist" aria-label={`${title}视图`} onKeyDown={tabKey}>
      {(["run", "history"] as const).map(item => <button key={item} id={`rf-aw-tab-${item}`} role="tab" aria-selected={tab === item} aria-controls={`rf-aw-panel-${item}`} tabIndex={tab === item ? 0 : -1} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>{item === "run" ? "运行" : "历史"}</button>)}
    </div>
    <div id="rf-aw-panel-run" role="tabpanel" aria-labelledby="rf-aw-tab-run" hidden={tab !== "run"}>
      <WorkflowPanel experimentId={ID} finishStepId="A16" runStepId="A15" analysisParameterPrefix="" analysisParameterKeys={analysisParameterKeys}
        normalizeParameters={commonTriggerParameters} enabled={fields.length > 0}
        fields={routeFields} layout={layout} setLayout={setLayout} setNotice={setNotice}
        parametersDisabled={parametersBusy || starting || saving || comparisonBusy}
        values={values} setValues={setValues} session={session} setSession={setSession}
        job={job} setJob={setJob} analysisJob={analysisJob} setAnalysisJob={setAnalysisJob}
        hardwareBusy={hardwareBusy} runs={workflowRuns.filter(item => item.completion_status === "completed")}
        runDirectoryChange={runDirectoryChange}
        setError={setError} onRefresh={setRefresh}>
        <div className="xy-workflow-controls">
          <div className="xy-flow-actions">
            <button disabled={saving || starting || parametersBusy || !fields.length} onClick={() => void save()}>{saving ? "保存中…" : "保存参数与布局"}</button>
            <button className="secondary" disabled={starting || parametersBusy || !fields.length} onClick={() => void check(false)}>仅预检</button>
            <button disabled={starting || jobActive || hardwareBusy || analysisActive || comparisonBusy || !fields.length} onClick={() => void check(true)}>{starting ? "正在启动…" : "预检并运行实验"}</button>
          </div>
          <p>在流程节点编辑参数。保存下一次实验参数与统一分类，历史目录的分析覆盖值独立保留。</p>
          {!sameParameterLayout(layout, savedLayout) && <p role="status">布局有未保存修改</p>}
          {notice && <p role="status">{notice}</p>}
          <div className="xy-workflow-preview" aria-label="RF 采集预览">
            <h3>RF 采集预览（只读）</h3>
            {!isR && <p>{values.Z_PHASE_SCAN_ENABLED === false
              ? `A11：跳过相位扫描，使用手动触发相位 ${String(values.Z_BURST_PHASE_INIT ?? "—")}°；分步模式仍需执行 A11 应用相位。`
              : "A11：扫描并拟合 Z RF 触发相位；可在节点参数中关闭扫描并手动指定相位。"}</p>}
            {isKnownNoise && <p>关闭扫描时，全部控制版本共用手动 RF 相位；仅改变 RF 正弦分量，不改变注入噪声相位。</p>}
            <p>响应范围 {String(values.RF_AMP_START ?? "—")} → {String(values.RF_AMP_STOP ?? "—")} Vpp，共 {String(values.RF_AMP_POINTS ?? "—")} 点，每点 {String(values.SCAN_POINT_DURATION_S ?? "—")} s。</p>
            <p>零 RF 噪声：{String(values.NOISE_N_AVG ?? "—")} 条 × {String(values.NOISE_DURATION ?? "—")} s；采样率以锁相实际回读为准。</p>
            {!isR && <p>各控制独立校相、采集和分析，共用当前 RF 参数，不自动改频。PSD 筛选：{values.NOISE_OUTLIER_POLICY === "mad_high" ? "保守 MAD 高值剔除（每控制至少 5 条）" : "保留全部记录"}；评分频带 {String(values.SENSITIVITY_BAND_MIN_HZ ?? "—")}–{String(values.SENSITIVITY_BAND_MAX_HZ ?? "—")} Hz。原始记录始终保留。</p>}
            {isKnownNoise && <p>注入噪声 ON：{String(values.NOISE_BAND_STARTS_HZ ?? "—")} → {String(values.NOISE_BAND_STOPS_HZ ?? "—")} Hz，实际噪声幅度自动对齐设计 PSD（V²/Hz）；控制版本：{String(values.CONTROL_WAVEFORM_FILES ?? "—")}。零 RF 保留噪声。</p>}
          </div>
          {hardwareBusy && !jobActive && <p>其他硬件任务正在运行。</p>}
          {definition?.wiring_notes.map(note => <p key={note}>{note}</p>)}
          <JobView job={job} onUpdate={setJob} />
        </div>
      </WorkflowPanel>
        <section className="zaw-panel zaw-panel-result">
          <div className="zaw-panel-head"><small>LATEST</small><h2>最近一次结果</h2></div>
          {latest ? <><Result value={latest} /><button className="zaw-secondary" onClick={() => { setSelectedId(latest.run_id); setHistoryView("single"); setTab("history"); }}>查看本次详情</button></> : <p className="zaw-empty">暂无运行记录。</p>}
        </section>
    </div>
    <div id="rf-aw-panel-history" role="tabpanel" aria-labelledby="rf-aw-tab-history" hidden={tab !== "history"}>
      {historyNotice && <p role="status">{historyNotice}</p>}
      <div className="zaw-history">
        <section className="zaw-run-list">
          <div className="zaw-run-list-head"><small>RUNS</small><h3>历史运行（{list.total}）</h3></div>
          <div className="rf-aw-history-tools">
            <button className="zaw-secondary" disabled={Boolean(managingRun)} onClick={() => setRefresh(value => value + 1)}>刷新记录</button>
            {canManageRuns && <label><input type="checkbox" checked={starredOnly} disabled={Boolean(managingRun)} onChange={event => { setStarredOnly(event.target.checked); setOffset(0); }} />仅显示星标</label>}
          </div>
          <ul>{list.runs.map(run => <li key={run.run_id}>
            <div className={`rf-aw-run-row${canManageRuns ? " with-star" : ""}`}>
              {!isKnownNoise && <input type="checkbox" aria-label={`选择 ${run.run_id} 进行对比`} title={run.comparison_block_reason || "加入对比"} disabled={!run.can_compare || comparisonBusy} checked={comparisonRuns.some(item => item.run_id === run.run_id)} onChange={event => {
                const checked = event.target.checked;
                setComparisonRuns(current => checked ? [...current, { run_id: run.run_id, legend: "" }] : current.filter(item => item.run_id !== run.run_id));
                setHistoryView("comparison");
              }} />}
              {canManageRuns && <button className={`rf-aw-star${run.starred ? " starred" : ""}`} aria-label={`${run.starred ? "取消星标" : "星标"} ${run.run_id}`} aria-pressed={Boolean(run.starred)} disabled={Boolean(managingRun)} onClick={() => void manageRun(run.run_id, "star", { starred: !run.starred })}>{run.starred ? "★" : "☆"}</button>}
              <button aria-label={run.run_id} className={`zaw-run-card ${selectedId === run.run_id ? "selected" : ""}`} onClick={() => { setSelectedId(run.run_id); setHistoryView("single"); }}><strong>{run.run_id}</strong><span>{run.timestamp}</span><span>采集：{status(run.completion_status)} · 校相：{status(run.phase_calibration_status)} · 分析：{status(run.analysis_status)}</span></button>
            </div>
            {!isKnownNoise && !run.can_compare && <p className="rf-aw-unavailable">不可对比：{run.comparison_block_reason}</p>}
          </li>)}</ul>
          {!list.runs.length && <p className="zaw-empty">{starredOnly ? "暂无星标运行记录。" : "暂无运行记录。"}</p>}
          <div className="zaw-control-buttons"><button className="zaw-secondary" disabled={!offset} onClick={() => setOffset(value => value - 50)}>上一页</button><button className="zaw-secondary" disabled={offset + 50 >= list.total} onClick={() => setOffset(value => value + 50)}>下一页</button></div>
        </section>
        <section className="zaw-run-details">
          <div className="zaw-control-buttons" role="group" aria-label="历史查看方式">
            <button className={historyView === "single" ? "zaw-primary" : "zaw-secondary"} aria-pressed={historyView === "single"} onClick={() => setHistoryView("single")}>单次详情</button>
{!isKnownNoise &&             <button className={historyView === "comparison" ? "zaw-primary" : "zaw-secondary"} aria-pressed={historyView === "comparison"} onClick={() => setHistoryView("comparison")}>对比分析（{comparisonRuns.length}）</button>}
          </div>
          <div hidden={historyView !== "single"}>{selected ? <>
            <div className="zaw-run-details-actions"><button className="zaw-secondary" disabled={jobActive || starting || saving} onClick={() => {
              setValues(current => commonTriggerParameters({ ...current, ...historyParameterValues(fields, historyParameters(selected.parameters as ParameterValues, !isR)) } as ParameterValues));
              if (!("RF_INPUT_ROUTE" in selected.parameters)) setNotice("旧参数按 HF2 物理接线 AuxOut2 → Signal Input2 填回；使用 MFLI 时请明确改选 Aux Input1 或 Aux Input2，否则预检会拒绝启动。");
              setTab("run");
            }}>填回参数</button><button className="zaw-secondary" disabled={jobActive || analysisActive || comparisonBusy || Boolean(managingRun) || !selected.can_analyze} onClick={() => void reanalyze(selected.run_id)}>重新分析</button></div>
            {canManageRuns && <div className="rf-aw-history-tools">
              <button className="zaw-secondary" disabled={directoryBusy} onClick={() => { setRenamingId(selected.run_id); setRenameName(selected.name || selected.run_id.replace(/^\d{4}_\d{4}(?:\d{2})?_/, "")); }}>重命名</button>
              <button className="danger" disabled={directoryBusy} onClick={() => void manageRun(selected.run_id, "delete")}>删除本次实验</button>
            </div>}
            {renamingId === selected.run_id && <form className="rf-aw-rename" onSubmit={event => { event.preventDefault(); void manageRun(selected.run_id, "rename", { name: renameName }); }}>
              <label>新名称<input autoFocus aria-label="新名称" maxLength={80} value={renameName} disabled={Boolean(managingRun)} onChange={event => setRenameName(event.target.value)} /></label>
              <p>只修改名称后缀，保留原时间前缀；无前缀的旧目录会补回原始运行时间。</p>
              <div className="rf-aw-history-tools"><button disabled={directoryBusy || !renameName.trim()} type="submit">保存名称</button><button className="zaw-secondary" type="button" disabled={Boolean(managingRun)} onClick={() => setRenamingId("")}>取消</button></div>
            </form>}
            <Result value={selected} />
            <details className="zaw-parameter-table"><summary>本次参数</summary><table><tbody>{Object.entries(selected.parameters).map(([key, value]) => <tr key={key}><th>{fields.find(field => field.name === key)?.label ?? key}</th><td>{String(value)}</td></tr>)}</tbody></table></details>
          </> : <p className="zaw-empty">{selectedId ? "正在读取运行详情…" : "请选择一条运行记录。"}</p>}</div>
          <div hidden={historyView !== "comparison"} className="rf-aw-comparison">
            <h3>对比分析</h3>
            <p>从左侧勾选至少两次运行，支持跨页选择。上图为{responseLabel}与拟合，下图为灵敏度主谱。</p>
            <p>每次运行填写一个英文图例，两面板使用同色；留空时使用运行 ID{canManageRuns && "，中文目录名使用英文时间标识"}。</p>
            {comparisonRuns.map(item => <div className="rf-aw-legend" key={item.run_id}>
              <label><span>{item.run_id}</span><input aria-label={`图例：${item.run_id}`} placeholder={item.run_id} value={item.legend ?? ""} disabled={comparisonBusy} aria-invalid={invalidLegend(item.legend)} onChange={event => {
                const legend = event.target.value;
                setComparisonRuns(current => current.map(value => value.run_id === item.run_id ? { ...value, legend } : value));
              }} /></label>
              <button className="zaw-secondary" aria-label={`移除 ${item.run_id}`} disabled={comparisonBusy} onClick={() => setComparisonRuns(current => current.filter(value => value.run_id !== item.run_id))}>移除</button>
            </div>)}
            {legendsInvalid && <p className="alert error" role="alert">图例请使用单行英文文字、数字或符号。</p>}
            <button className="zaw-primary" disabled={comparisonRuns.length < 2 || comparisonBusy || analysisActive || legendsInvalid} onClick={() => void generateComparison()}>{comparisonBusy ? "正在生成对比…" : "生成对比"}</button>
            {analysisActive && <p>重新分析完成后可生成对比。</p>}
            <JobView job={comparisonJob} onUpdate={setComparisonJob} />
            {comparisonDirty && <p className="alert" role="status">选择或图例已修改，请重新生成对比。</p>}
            {comparisonResult && !comparisonDirty && <>
              {comparisonResult.warnings.map((warning, index) => <p className="alert" key={index}>{warning}</p>)}
              <figure className="zaw-figure"><figcaption>{responseLabel}与 RF 灵敏度对比</figcaption><img src={comparisonResult.artifacts["full_analysis_comparison.png"]} alt={`${responseLabel}与 RF 灵敏度对比`} /></figure>
              <p className="rf-aw-downloads">{["png", "pdf"].map(extension => <a key={extension} href={comparisonResult.artifacts[`full_analysis_comparison.${extension}`]} download>下载 {extension.toUpperCase()}</a>)}</p>
            </>}
          </div>
        </section>
      </div>
    </div>
    <JobView job={analysisJob} onUpdate={setAnalysisJob} />
  </>;
}
