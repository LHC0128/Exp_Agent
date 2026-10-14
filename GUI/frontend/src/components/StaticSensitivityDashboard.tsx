import { preflightMessage, type PreflightResult } from "../pages/experimentHelpers";
import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { api } from "../api";
import { useJobActivity } from "./JobActivity";
import { JobView } from "./JobView";
import { WorkflowPanel, type XYSession } from "./NoiseSpectrumXY/WorkflowPanel";
import type { ExperimentDefinition, ExperimentSchema, Job, JobSummary, ParameterLayout, ParameterValues, SchemaField } from "../types/api";
import { sameParameterLayout, validatedParameterLayout } from "../pages/experimentHelpers";

type StaticRunSummary = { run_id: string; timestamp: string; run_tag: string; completion_status: string; analysis: Record<string, unknown>; artifacts: Record<string, string> };
type Props = { experimentId: string; definition: ExperimentDefinition; fields: SchemaField[]; layout: ParameterLayout; layoutDirty: boolean; onLayoutChange: Dispatch<SetStateAction<ParameterLayout>>; onLayoutSaved: (next: ParameterLayout) => void; values: ParameterValues; setValues: Dispatch<SetStateAction<ParameterValues>>; job: Job | undefined; setJob: Dispatch<SetStateAction<Job | undefined>>; prefill: ParameterValues | null; clearPrefill: () => void };
const ANALYSIS_KEYS = { z_v_to_nt: "z_v_to_nt" };

export function StaticSensitivityDashboard({ experimentId, definition, fields, layout, layoutDirty, onLayoutChange, onLayoutSaved, values, setValues, job, setJob, prefill, clearPrefill }: Props) {
  const { hardwareBusy, activeJobs } = useJobActivity();
  const [session, setSession] = useState<XYSession>();
  const [analysisJob, setAnalysisJob] = useState<Job>();
  const [runs, setRuns] = useState<StaticRunSummary[]>([]);
  const [refresh, setRefresh] = useState(0);
  const [notice, setNotice] = useState("");
  const [starting, setStarting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [preflight, setPreflight] = useState<PreflightResult>();
  const [status, setStatus] = useState<{ ok: boolean; message: string }>();
  const [error, setError] = useState("");
  const live = Boolean(session && session.status !== "closed");
  const jobActive = Boolean(job && ["queued", "running"].includes(job.status));
  const fullActive = jobActive && !live;
  const busySession = live && (session?.status !== "waiting" || Boolean(session?.pending_request));
  const blocked = hardwareBusy || live || activeJobs.some((item) => item.kind === `experiment:${experimentId}` && ["queued", "running"].includes(item.status));
  useEffect(() => {
    if (!prefill) return;
    setValues((current) => ({ ...current, ...prefill }));
    clearPrefill();
  }, [prefill]);
  useEffect(() => {
    let disposed = false;
    void api<{ runs: StaticRunSummary[] }>(`/api/experiments/${experimentId}/runs?limit=50&offset=0`)
      .then((result) => { if (!disposed) setRuns(result.runs); }).catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [experimentId, refresh, job?.status, analysisJob?.status]);
  useEffect(() => {
    let disposed = false;
    void api<JobSummary[]>("/api/jobs").then(async (items) => {
      const match = items.find((item) => item.kind.startsWith(`analysis:${experimentId}:`) && ["queued", "running"].includes(item.status));
      if (!match || disposed) return;
      const detail = await api<Job>(`/api/jobs/${match.id}`);
      if (!disposed) setAnalysisJob(detail);
    }).catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [experimentId]);
  const check = async () => {
    setError("");
    try { setPreflight(await api<PreflightResult>(`/api/experiments/${experimentId}/preflight`, { method: "POST", body: JSON.stringify({ parameters: values }) })); }
    catch (reason) { setError(String(reason)); }
  };
  const run = async () => {
    setError(""); setPreflight(undefined); setStarting(true);
    try {
      const result = await api<PreflightResult>(`/api/experiments/${experimentId}/preflight`, { method: "POST", body: JSON.stringify({ parameters: values }) });
      setPreflight(result);
      if (result.ok) setJob(await api<Job>(`/api/experiments/${experimentId}/runs`, { method: "POST", body: JSON.stringify({ parameters: values }) }));
    } catch (reason) { setError(String(reason)); }
    finally { setStarting(false); }
  };
  const save = async () => {
    setSaving(true); setStatus(undefined);
    try {
      const result = await api<{ message: string; schema: ExperimentSchema }>(`/api/experiments/${experimentId}/defaults`, { method: "PUT", body: JSON.stringify({ parameters: values, parameter_layout: layout }) });
      const persisted = result.schema.parameter_layout_saved ? validatedParameterLayout(result.schema.fields, result.schema.parameter_layout) : undefined;
      if (!persisted || !sameParameterLayout(persisted, layout)) throw new Error("后端未确认参数布局已保存");
      onLayoutSaved(persisted); setStatus({ ok: true, message: result.message });
    } catch (reason) { setStatus({ ok: false, message: String(reason) }); }
    finally { setSaving(false); }
  };
  return <>
    <WorkflowPanel experimentId={experimentId} finishStepId="A13" runStepId="A12" analysisParameterKeys={ANALYSIS_KEYS}
      enabled={fields.length > 0} fields={fields} layout={layout} setLayout={onLayoutChange} setNotice={setNotice}
      parametersDisabled={starting || fullActive} values={values} setValues={setValues} session={session} setSession={setSession}
      job={job} setJob={setJob} analysisJob={analysisJob} setAnalysisJob={setAnalysisJob}
      hardwareBusy={hardwareBusy || fullActive || starting} runs={runs} setError={setError} onRefresh={setRefresh}>
      <div className="xy-workflow-controls">
        <div className="zaw-control-buttons">
          <button className="zaw-secondary" disabled={saving || fullActive || starting || busySession} onClick={() => void save()}>{saving ? "保存中…" : "保存参数与布局"}</button>
          <button className="zaw-secondary" disabled={starting || jobActive || blocked} onClick={() => void check()}>仅预检</button>
          <button className="zaw-primary" disabled={starting || jobActive || blocked} onClick={() => void run()}>{jobActive ? "实验运行中" : starting ? "正在启动…" : "预检并运行实验"}</button>
        </div>
        {layoutDirty && <p className="zaw-layout-dirty" role="status">布局有未保存修改</p>}
        {notice && <p role="status">{notice}</p>}
        {status && <div className={`alert ${status.ok ? "success" : "error"}`}>{status.message}</div>}
        {preflight && <div className={`alert ${preflight.ok ? "success" : "error"}`}>{preflightMessage(preflight, "参数预检通过，可以启动实验。")}</div>}
        {blocked && !jobActive && <div className="alert">其他硬件任务正在运行，等待其完成后才能启动本实验。</div>}
        {error && <div className="alert error">{error}</div>}
        <p>执行模式：{definition.execution_mode} · 所需设备：{definition.required_devices.join(" · ")}</p>
        <JobView job={job} onUpdate={setJob} />
        <JobView job={analysisJob} onUpdate={setAnalysisJob} />
      </div>
    </WorkflowPanel>
    <section className="zaw-panel zaw-panel-result"><div className="zaw-panel-head"><small>LATEST</small><h2>最近一次结果</h2></div>
      <LatestPreview job={job} busy={jobActive} runId={session?.steps.A12?.status === "completed" ? session.run_id : undefined} refresh={refresh} />
    </section>
  </>;
}

function LatestPreview({ job, busy, runId: sessionRunId, refresh }: { job: Job | undefined; busy: boolean; runId?: string | null; refresh: number }) { const [summary, setSummary] = useState<StaticRunSummary>(); useEffect(() => { const result = job?.result as { run_dir?: unknown } | undefined; const runDir = typeof result?.run_dir === "string" ? result.run_dir : ""; const runId = sessionRunId ?? runDir.split(/[\\/]/).pop() ?? ""; const load = (id: string) => api<StaticRunSummary>(`/api/experiments/static-sensitivity/runs/${encodeURIComponent(id)}/summary`).then(setSummary); if (runId) { load(runId).catch(() => undefined); return; } api<{ runs: Array<{ run_id: string }> }>("/api/experiments/static-sensitivity/runs?limit=1&offset=0").then((data) => { const latest = data.runs[0]; if (latest) return load(latest.run_id); return undefined; }).catch(() => undefined); }, [job?.id, job?.status, sessionRunId, refresh]); if (!summary) return <p className="zaw-empty">{busy ? "正在采集，运行结束后会展示结果。" : "尚无运行结果，启动一次实验后这里会展示灵敏度分析。"}</p>; const analysis = summary.analysis; return <div className="zaw-latest"><div className="zaw-metrics-grid"><div className="zaw-metric"><small>完成状态</small><span className="zaw-metric-value">{summary.completion_status}</span></div><div className="zaw-metric"><small>拟合状态</small><span className="zaw-metric-value">{analysis.fit_valid === undefined ? "未分析" : analysis.fit_valid ? "有效" : "无效"}</span></div><div className="zaw-metric"><small>最佳 GS200 量程</small><span className="zaw-metric-value">{String(analysis.best_range_label ?? "—")}</span></div><div className="zaw-metric"><small>平坦段灵敏度</small><span className="zaw-metric-value">{formatNumber(analysis.sensitivity_flat_fT_per_sqrt_Hz)} fT/√Hz</span></div></div><p className="zaw-latest-meta">{summary.timestamp} · {summary.run_tag}</p>{typeof analysis.error === "string" && <p className="alert error">{analysis.error}</p>}{Array.isArray(analysis.fit_rejection_reasons) && analysis.fit_rejection_reasons.length > 0 && <p className="alert error">{analysis.fit_rejection_reasons.join("；")}</p>}{analysis.success !== false && summary.artifacts["full_analysiswithoutpump.png"] && <figure className="zaw-figure"><figcaption>色散、噪声与灵敏度结果</figcaption><img src={summary.artifacts["full_analysiswithoutpump.png"]} alt="静磁场灵敏度分析结果" loading="lazy" /></figure>}</div>; }
const formatNumber = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toString() : "—";
