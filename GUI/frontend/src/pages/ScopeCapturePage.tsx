import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { ChartNoAxesCombined, Eye, EyeOff, Play, RefreshCw, RotateCcw, Save, Trash2 } from "lucide-react";
import { api } from "../api";
import { JobView } from "../components/JobView";
import { useJobActivity } from "../components/JobActivity";
import { PageHead } from "../components/PageHead";
import { ScopePlot } from "../components/ScopePlot";
import { WorkflowPanel, type XYSession } from "../components/NoiseSpectrumXY/WorkflowPanel";
import type {
  ExperimentDefinition, ExperimentSchema, Job, JobSummary, ParameterLayout, ParameterValues,
  RunSummary, RunsResponse,
} from "../types/api";
import type { WorkflowRunSummary } from "./WorkflowExperimentPage";
import {
  defaultParameterLayout, historyParameterValues, preflightMessage, sameParameterLayout,
  validatedParameterLayout, type PreflightResult,
} from "./experimentHelpers";
import "./zawClosedLoop.css";
import "./scopeCapture.css";

const ID = "scope-capture";
const BASE = `/api/experiments/${ID}`;
const terminal = (job: Job | undefined) => Boolean(job && ["completed", "failed", "cancelled"].includes(job.status));
const statusLabels: Record<string, string> = {
  completed: "完成", failed: "失败", cancelled: "已取消", running: "运行中",
  not_started: "未分析", quality_failed: "质量未通过", stale: "需重算", unknown: "未记录",
};
const status = (value: string) => statusLabels[value] ?? value;
const palette = ["#147d74", "#cd6c2d", "#6261b5", "#bd4268", "#3879b0", "#737b28"];
const colorKey = "exp-agent:scope-capture:colors";
type Display = { channel: number; sample_count: number; actual_rate_sa_s: number; actual_duration_s: number;
  requested_rate_sa_s: number; requested_duration_s: number; time_s: number[]; voltage_v: number[]; display_decimated: boolean;
  vertical_scale_v_div?: number | null; vertical_offset_v?: number | null; vertical_range_min_v?: number | null;
  vertical_range_max_v?: number | null; overrange?: boolean | null; overrange_fraction?: number | null };
type Spectrum = { frequency_hz: number[]; psd_v2_hz: number[]; frequency_resolution_hz: number };
type RecordData = { display: Display; spectrum: Spectrum };

/** 最近一次运行结果：采集与分析状态、波形/PSD 结果图和产物下载。 */
function LatestResult({ value }: { value: WorkflowRunSummary }) {
  const artifacts = value.artifacts ?? {};
  return <div className="zaw-latest">
    <p><strong>{value.run_id}</strong> · {value.timestamp} · 采集：{status(value.completion_status)} · 分析：{status(value.analysis_status)}</p>
    {value.analysis_error && <p role="alert">{value.analysis_error}</p>}
    {artifacts["results/waveform_psd.png"]
      ? <figure className="zaw-figure"><figcaption>波形与 Welch PSD</figcaption>
          <img src={artifacts["results/waveform_psd.png"]} alt="results/waveform_psd.png" loading="lazy" /></figure>
      : <p className="zaw-empty">本次运行暂无结果图；完成后在历史页叠加比较波形。</p>}
    <p className="xy-artifact-links">{Object.entries(artifacts).map(([name, url]) =>
      <a key={name} href={url} download>{name}</a>)}</p>
  </div>;
}

export function ScopeCapturePage() {
  const [tab, setTab] = useState<"run" | "history">("run");
  const [definition, setDefinition] = useState<ExperimentDefinition>();
  const [fields, setFields] = useState<ExperimentSchema["fields"]>([]);
  const [values, setValues] = useState<ParameterValues>({});
  const [layout, setLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [savedLayout, setSavedLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [job, setJob] = useState<Job>();
  const [analysisJob, setAnalysisJob] = useState<Job>();
  const [session, setSession] = useState<XYSession>();
  const [starting, setStarting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preflight, setPreflight] = useState<PreflightResult>();
  const [workflowRefresh, setWorkflowRefresh] = useState(0);
  const [workflowRuns, setWorkflowRuns] = useState<{ run_id: string; completion_status: string }[]>([]);
  const [latest, setLatest] = useState<WorkflowRunSummary>();
  const { hardwareBusy } = useJobActivity();
  const jobActive = Boolean(job && !terminal(job));
  const analysisActive = Boolean(analysisJob && !terminal(analysisJob));
  const sessionActive = Boolean(session && session.status !== "closed");
  const parametersBusy = jobActive && (!sessionActive || session?.status !== "waiting" || Boolean(session?.pending_request));

  // 历史 Tab：通用运行记录 + 多曲线叠加比较（时域/频域、超量程诊断）。
  const [history, setHistory] = useState<RunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [hidden, setHidden] = useState<string[]>([]);
  const [records, setRecords] = useState<Record<string, RecordData>>({});
  const [colors, setColors] = useState<Record<string, string>>(() => {
    try {
      const value: unknown = JSON.parse(localStorage.getItem(colorKey) || "{}");
      if (!value || typeof value !== "object" || Array.isArray(value)) return {};
      return Object.fromEntries(Object.entries(value).filter(([, color]) => typeof color === "string" && /^#[0-9a-f]{6}$/i.test(color)));
    } catch { return {}; }
  });
  const displayMode = String(values.mode ?? "time");
  const [spectrumXAxisLog, setSpectrumXAxisLog] = useState(false);
  const [spectrumYAxisLog, setSpectrumYAxisLog] = useState(false);
  const [loading, setLoading] = useState(false);
  const [reset, setReset] = useState(0);
  const alive = useRef(true);

  useEffect(() => {
    let disposed = false;
    void Promise.all([
      api<ExperimentDefinition>(BASE), api<ExperimentSchema>(`${BASE}/schema`), api<JobSummary[]>("/api/jobs"),
    ]).then(async ([def, schema, jobs]) => {
      if (disposed) return;
      setDefinition(def); setFields(schema.fields);
      setValues(Object.fromEntries(schema.fields.map(field => [field.name, field.default])));
      const saved = schema.parameter_layout_saved ? validatedParameterLayout(schema.fields, schema.parameter_layout) : undefined;
      const initial = saved ?? defaultParameterLayout(schema.fields);
      setLayout(initial); setSavedLayout(initial);
      const matching = jobs.filter(item => item.kind === `experiment:${ID}`);
      const recovered = matching.find(item => ["queued", "running"].includes(item.status))
        ?? matching.find(item => Boolean(item.started_at));
      if (recovered) {
        const full = await api<Job>(`/api/jobs/${recovered.id}`);
        if (!disposed) setJob(full);
      }
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, []);

  useEffect(() => { try { localStorage.setItem(colorKey, JSON.stringify(colors)); } catch { /* 浏览器禁用存储时保留本次会话颜色。 */ } }, [colors]);

  useEffect(() => {
    if (terminal(analysisJob)) {
      setRecords({});
      setWorkflowRefresh(value => value + 1);
    }
  }, [analysisJob?.id, analysisJob?.status]);

  const refresh = useCallback(async (offset = 0) => {
    const result = await api<RunsResponse>("/api/runs?experiment_id=scope-capture&limit=50&offset=" + offset);
    if (!alive.current) return;
    setHistory(old => offset ? [...old, ...result.runs.filter(r => !old.some(o => o.id === r.id))] : result.runs);
    setTotal(result.total);
  }, []);

  useEffect(() => {
    alive.current = true;
    refresh().catch(reason => setError(String(reason)));
    return () => { alive.current = false; };
  }, [refresh, job?.status, analysisJob?.status]);

  useEffect(() => {
    let disposed = false;
    void api<{ runs: { run_id: string; completion_status: string }[]; total: number }>(`${BASE}/runs?limit=50&offset=0`)
      .then(data => { if (!disposed) setWorkflowRuns(data.runs); })
      .catch(() => { /* 工作流历史不可用时流程面板退化为空列表。 */ });
    void api<{ runs: { run_id: string }[] }>(`${BASE}/runs?limit=1&offset=0`).then(async data => {
      const value = data.runs[0] ? await api<WorkflowRunSummary>(`${BASE}/runs/${encodeURIComponent(data.runs[0].run_id)}/summary`) : undefined;
      if (!disposed) setLatest(value);
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [BASE, workflowRefresh, job?.status, analysisJob?.status]);

  useEffect(() => {
    let disposed = false;
    if (job?.kind?.startsWith("analysis:") && ["queued", "running"].includes(job.status)) return;
    const missing = selected.filter(id => !records[id] && history.find(h => h.id === id)?.artifacts.includes("display.json"));
    if (!missing.length) { setLoading(false); return; }
    setLoading(true);
    void Promise.all(missing.map(async id => {
      const prefix = "/api/runs/scope-capture/" + encodeURIComponent(id) + "/artifacts/";
      const [display, spectrum] = await Promise.all([api<Display>(prefix + "display.json"), api<Spectrum>(prefix + "psd.json")]);
      return [id, { display, spectrum }] as const;
    })).then(entries => { if (!disposed) setRecords(old => ({ ...old, ...Object.fromEntries(entries) })); })
      .catch(reason => { if (!disposed) setError("历史数据加载失败：" + String(reason)); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [selected, records, history, job?.kind, job?.status]);

  const check = async (start: boolean) => {
    setStarting(true); setError("");
    try {
      const result = await api<PreflightResult>(`${BASE}/preflight`, { method: "POST", body: JSON.stringify({ parameters: values }) });
      setPreflight(result);
      if (!result.ok) { setError(result.errors.join("；")); return; }
      if (start) {
        setJob(await api<Job>(`${BASE}/runs`, { method: "POST", body: JSON.stringify({ parameters: values }) }));
        setWorkflowRefresh(value => value + 1);
      } else {
        setNotice(preflightMessage(result, "预检通过"));
      }
    } catch (reason) { setError(String(reason)); } finally { setStarting(false); }
  };

  const save = async () => {
    setSaving(true); setError("");
    try {
      const result = await api<{ message: string; schema: ExperimentSchema }>(`${BASE}/defaults`, {
        method: "PUT", body: JSON.stringify({ parameters: values, parameter_layout: layout }),
      });
      const persisted = result.schema.parameter_layout_saved
        ? validatedParameterLayout(result.schema.fields, result.schema.parameter_layout) : undefined;
      if (!persisted || !sameParameterLayout(persisted, layout)) throw new Error("后端未确认参数布局已保存");
      setSavedLayout(persisted); setFields(result.schema.fields); setNotice(result.message);
    } catch (reason) { setError(String(reason)); } finally { setSaving(false); }
  };

  const switchMode = (mode: string) => {
    // mode 是稳定外部参数键，同时控制历史叠加的时域/频域显示。
    setReset(value => value + 1);
    setValues(current => ({ ...current, mode }));
  };

  const remove = async () => {
    const ids = [...selected];
    if (!ids.length || !window.confirm("永久删除以下 " + ids.length + " 条历史数据及其原始文件？\n" + ids.join("\n"))) return;
    setStarting(true); setError("");
    try {
      for (const id of ids) {
        await api("/api/runs/scope-capture/" + encodeURIComponent(id), { method: "DELETE" });
        setSelected(old => old.filter(x => x !== id)); setHidden(old => old.filter(x => x !== id));
        setRecords(old => { const next = { ...old }; delete next[id]; return next; });
        setColors(old => { const next = { ...old }; delete next[id]; return next; });
      }
    } catch (reason) { setError(String(reason)); }
    finally { await refresh().catch(reason => setError(String(reason))); setWorkflowRefresh(value => value + 1); setStarting(false); }
  };

  const reanalyze = async () => {
    if (selected.length !== 1) return;
    setStarting(true); setError("");
    try {
      const id = selected[0];
      setAnalysisJob(await api<Job>("/api/runs/" + encodeURIComponent(id) + "/analyze", {
        method: "POST", body: JSON.stringify({ experiment_id: ID }),
      }));
    } catch (reason) { setError(String(reason)); } finally { setStarting(false); }
  };

  const restore = async (id: string) => {
    setStarting(true); setError("");
    try {
      const summary = await api<WorkflowRunSummary>(`${BASE}/runs/${encodeURIComponent(id)}/summary`);
      setValues(current => ({ ...current, ...historyParameterValues(fields, summary.parameters as ParameterValues) }));
      setTab("run");
    } catch (reason) { setError(String(reason)); } finally { setStarting(false); }
  };

  const colorFor = (id: string) => colors[id] || palette[[...id].reduce((sum, c) => sum + c.charCodeAt(0), 0) % palette.length];
  const traces = useMemo(() => selected.filter(id => !hidden.includes(id) && records[id]).map(id => ({
    id, color: colorFor(id),
    x: displayMode === "time" ? records[id].display.time_s : records[id].spectrum.frequency_hz,
    y: displayMode === "time" ? records[id].display.voltage_v : records[id].spectrum.psd_v2_hz,
    ...(displayMode === "time" ? {
      rangeMin: records[id].display.vertical_range_min_v ?? undefined,
      rangeMax: records[id].display.vertical_range_max_v ?? undefined,
      overrange: records[id].display.overrange ?? undefined,
    } : {}),
  })), [selected, hidden, records, colors, displayMode]);
  const loadedRuns = selected.map(id => records[id]).filter(Boolean);
  const newest = loadedRuns[loadedRuns.length - 1];

  const tabKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const order = ["run", "history"] as const;
    const index = order.indexOf(tab);
    setTab(order[(index + (event.key === "ArrowRight" ? 1 : order.length - 1)) % order.length]);
    event.currentTarget.querySelector<HTMLButtonElement>(`#scope-tab-${tab === "run" ? "history" : "run"}`)?.focus();
  };

  return <>
    <PageHead eyebrow="MEASUREMENT · SCOPE" title={definition?.title ?? "示波器采集"}
      description={definition?.description ?? "读取参数与运行记录。"} />
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert success" role="status">{notice}</div>}
    <div className="zaw-tab-strip" role="tablist" aria-label="示波器采集视图" onKeyDown={tabKey}>
      {(["run", "history"] as const).map(item => <button key={item} id={`scope-tab-${item}`} role="tab"
        aria-selected={tab === item} aria-controls={`scope-panel-${item}`} tabIndex={tab === item ? 0 : -1}
        className={tab === item ? "active" : ""} onClick={() => setTab(item)}>
        {item === "run" ? "运行" : "历史"}</button>)}
    </div>
    <div id="scope-panel-run" role="tabpanel" aria-labelledby="scope-tab-run" hidden={tab !== "run"}>
      <WorkflowPanel enabled={fields.length > 0} fields={fields} layout={layout} setLayout={setLayout}
        setNotice={setNotice} parametersDisabled={parametersBusy || starting || saving} values={values} setValues={setValues}
        session={session} setSession={setSession} job={job} setJob={setJob}
        analysisJob={analysisJob} setAnalysisJob={setAnalysisJob} hardwareBusy={hardwareBusy}
        runs={workflowRuns} setError={setError} onRefresh={setWorkflowRefresh}
        experimentId={ID} finishStepId="A05" runStepId="A04">
        <div className="xy-workflow-controls">
          <div className="xy-flow-actions">
            <button disabled={saving || starting || parametersBusy || !fields.length} onClick={() => void save()}>
              {saving ? "保存中…" : "保存参数与布局"}</button>
            <button className="secondary" disabled={starting || parametersBusy || !fields.length} onClick={() => void check(false)}>仅预检</button>
            <button disabled={starting || jobActive || hardwareBusy || analysisActive || !fields.length} onClick={() => void check(true)}>
              {starting ? "处理中…" : "预检并运行实验"}</button>
          </div>
          <p>在流程节点编辑参数。此处保存下一次实验的全部参数和统一的基础／高级分类，不保存历史分析覆盖值。</p>
          {!sameParameterLayout(layout, savedLayout) && <p role="status">布局有未保存修改</p>}
          {preflight && <div className={`alert ${preflight.ok ? "success" : "error"}`}>{preflightMessage(preflight, "参数预检通过")}</div>}
          {hardwareBusy && !jobActive && <p>其他硬件任务正在运行。</p>}
          {definition?.wiring_notes.map(note => <p key={note}>{note}</p>)}
          <JobView job={job} onUpdate={setJob} />
        </div>
      </WorkflowPanel>
      <section className="zaw-panel zaw-panel-result">
        <div className="zaw-panel-head"><small>LATEST</small><h2>最近一次结果</h2></div>
        {latest ? <LatestResult value={latest} /> : <p className="zaw-empty">暂无运行记录。</p>}
      </section>
    </div>
    <div id="scope-panel-history" role="tabpanel" aria-labelledby="scope-tab-history" hidden={tab !== "history"}>
      <div className="scope-page">
        <div className="scope-workspace">
          <section className="scope-display">
            <div className="scope-toolbar">
              <div className="scope-modes" role="group" aria-label="显示模式">
                {fields.find(f => f.name === "mode")?.options?.map(option => <button key={option.value}
                  aria-pressed={displayMode === option.value} onClick={() => switchMode(String(option.value))}>{option.label}</button>)}
              </div>
              {displayMode === "frequency" && <div className="scope-axis-scales" role="group" aria-label="频谱坐标轴">
                <label><input type="checkbox" aria-label="频率轴 log" checked={spectrumXAxisLog} onChange={e => { setSpectrumXAxisLog(e.target.checked); setReset(n => n + 1); }} />频率轴 log</label>
                <label><input type="checkbox" aria-label="PSD 轴 log" checked={spectrumYAxisLog} onChange={e => { setSpectrumYAxisLog(e.target.checked); setReset(n => n + 1); }} />PSD 轴 log</label>
              </div>}
              <span>{loading ? "正在加载波形" : traces.length + " 条曲线"}</span>
              <button className="scope-icon" title="重置缩放" aria-label="重置缩放" onClick={() => setReset(n => n + 1)}><RotateCcw size={17} /></button>
            </div>
            <div className="scope-plot-wrap"><ScopePlot traces={traces} mode={displayMode} reset={reset}
              xScale={displayMode === "frequency" && spectrumXAxisLog ? "log" : "linear"}
              yScale={displayMode === "frequency" && spectrumYAxisLog ? "log" : "linear"} />
              {!traces.length && <div className="scope-empty">{loading ? "正在加载" : "暂无显示数据"}</div>}
            </div>
            <div className="scope-readout">
              <span>实际采样率 <b>{newest ? newest.display.actual_rate_sa_s.toPrecision(6) + " Sa/s" : "--"}</b></span>
              <span>记录长度 <b>{newest ? newest.display.sample_count.toLocaleString() + " 点" : "--"}</b></span>
              <span>实际时长 <b>{newest ? newest.display.actual_duration_s.toPrecision(6) + " s" : "--"}</b></span>
              {displayMode === "frequency" && <span>频率间隔 <b>{newest ? newest.spectrum.frequency_resolution_hz.toPrecision(5) + " Hz" : "--"}</b></span>}
              {displayMode === "time" && <>
                <span>垂直档位 <b>{newest?.display.vertical_scale_v_div != null ? newest.display.vertical_scale_v_div + " V/div" : "--"}</b></span>
                <span>垂直偏置 <b>{newest?.display.vertical_offset_v != null ? newest.display.vertical_offset_v + " V" : "--"}</b></span>
                <span>量程 <b>{newest?.display.vertical_range_min_v != null && newest?.display.vertical_range_max_v != null ? newest.display.vertical_range_min_v + " ~ " + newest.display.vertical_range_max_v + " V" : "--"}</b></span>
                <span>状态 <b style={newest?.display.overrange ? { color: "#c82828" } : undefined}>{newest?.display.overrange == null ? "未知" : newest.display.overrange ? "超量程" : "正常"}</b></span>
                {newest?.display.overrange_fraction != null && <span>超量程比例 <b>{(newest.display.overrange_fraction * 100).toPrecision(3)}%</b></span>}
              </>}
            </div>
          </section>
          <div className="scope-controls">
            <section className="scope-history"><div className="scope-section-head"><h2>历史 <small>{total}</small></h2><div>
              <button className="scope-icon" title="刷新历史" aria-label="刷新历史" disabled={starting} onClick={() => void refresh().catch(reason => setError(String(reason)))}><RefreshCw size={16} /></button>
              <button className="scope-icon" title="重新分析选定数据" aria-label="重新分析选定数据" disabled={selected.length !== 1 || starting || jobActive || analysisActive} onClick={() => void reanalyze()}><ChartNoAxesCombined size={16} /></button>
              <button className="scope-icon danger" title="删除选定历史数据" aria-label="删除选定历史数据" disabled={!selected.length || starting} onClick={() => void remove()}><Trash2 size={16} /></button></div></div>
              <div className="scope-history-list">{history.map(item => <div className="scope-history-row" key={item.id}>
                <input type="checkbox" aria-label={"选择 " + item.id} checked={selected.includes(item.id)} disabled={starting}
                  onChange={e => { setError(""); setSelected(old => e.target.checked ? [...old, item.id] : old.filter(id => id !== item.id)); }} />
                <div className="scope-history-name" title={item.id}>{item.id}<small>{item.artifacts.includes("display.json") ? "C" + (records[item.id]?.display.channel || "·") : "待分析"}</small></div>
                <input type="color" aria-label={"颜色 " + item.id} value={colorFor(item.id)} onChange={e => setColors(old => ({ ...old, [item.id]: e.target.value }))} />
                <button className="scope-icon" title={hidden.includes(item.id) ? "显示" : "隐藏"} aria-label={(hidden.includes(item.id) ? "显示 " : "隐藏 ") + item.id}
                  disabled={!selected.includes(item.id)} onClick={() => setHidden(old => old.includes(item.id) ? old.filter(id => id !== item.id) : [...old, item.id])}>
                  {hidden.includes(item.id) ? <EyeOff size={16} /> : <Eye size={16} />}</button>
              </div>)}</div>
              {!history.length && <p className="scope-muted">暂无历史数据</p>}
              {history.length < total && <button className="secondary compact" disabled={starting}
                onClick={() => void refresh(history.length).catch(reason => setError(String(reason)))}>加载更多</button>}
            </section>
            <section className="scope-settings">
              <h2>历史操作</h2>
              <button disabled={selected.length !== 1 || starting || jobActive || sessionActive} onClick={() => void restore(selected[0])}>
                <Play size={15} />填回参数到运行页</button>
              <p className="scope-muted">勾选历史后叠加比较波形与 PSD；重新分析仅支持单条记录。</p>
              {definition?.safety_notes.map(note => <p key={note} className="scope-muted">{note}</p>)}
            </section>
          </div>
        </div>
      </div>
    </div>
    <JobView job={analysisJob} onUpdate={setAnalysisJob} />
  </>;
}
