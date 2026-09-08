import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChartNoAxesCombined, Eye, EyeOff, Play, RefreshCw, RotateCcw, Save, Trash2 } from "lucide-react";
import { api } from "../api";
import { JobView } from "../components/JobView";
import { useJobActivity } from "../components/JobActivity";
import { ScopePlot } from "../components/ScopePlot";
import type { ExperimentSchema, Job, JobSummary, ParameterValues, RunSummary, RunsResponse } from "../types/api";
import "./scopeCapture.css";

const base = "/api/experiments/scope-capture";
const palette = ["#147d74", "#cd6c2d", "#6261b5", "#bd4268", "#3879b0", "#737b28"];
const colorKey = "exp-agent:scope-capture:colors";
type Display = { channel: number; sample_count: number; actual_rate_sa_s: number; actual_duration_s: number;
  requested_rate_sa_s: number; requested_duration_s: number; time_s: number[]; voltage_v: number[]; display_decimated: boolean;
  vertical_scale_v_div?: number | null; vertical_offset_v?: number | null; vertical_range_min_v?: number | null; vertical_range_max_v?: number | null; overrange?: boolean | null; overrange_fraction?: number | null };
type Spectrum = { frequency_hz: number[]; psd_v2_hz: number[]; frequency_resolution_hz: number };
type RecordData = { display: Display; spectrum: Spectrum };

export function ScopeCapturePage() {
  const [schema, setSchema] = useState<ExperimentSchema>();
  const [values, setValues] = useState<ParameterValues>({});
  const [mode, setMode] = useState("time");
  const [spectrumXAxisLog, setSpectrumXAxisLog] = useState(false);
  const [spectrumYAxisLog, setSpectrumYAxisLog] = useState(false);
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
  const [job, setJob] = useState<Job>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [reset, setReset] = useState(0);
  const alive = useRef(true);
  const loadedJob = useRef("");
  const { hardwareBusy, activeJobs } = useJobActivity();
  const active = Boolean(job && ["queued", "running"].includes(job.status)) || activeJobs.some(j => j.kind === "experiment:scope-capture");
  const refresh = useCallback(async (offset = 0) => {
    const result = await api<RunsResponse>("/api/runs?experiment_id=scope-capture&limit=50&offset=" + offset);
    if (!alive.current) return;
    setHistory(old => offset ? [...old, ...result.runs.filter(r => !old.some(o => o.id === r.id))] : result.runs);
    setTotal(result.total);
  }, []);
  useEffect(() => {
    alive.current = true;
    api<ExperimentSchema>(base + "/schema").then(s => {
      if (!alive.current) return;
      setSchema(s); setValues(Object.fromEntries(s.fields.map(f => [f.name, f.default])));
      setMode(String(s.fields.find(f => f.name === "mode")?.default || "time"));
    }).catch(e => setError(String(e)));
    refresh().catch(e => setError(String(e)));
    api<JobSummary[]>("/api/jobs").then(async jobs => {
      const match = jobs.find(j => j.kind === "experiment:scope-capture" && ["queued", "running"].includes(j.status));
      if (match) { const full = await api<Job>("/api/jobs/" + match.id); if (alive.current) setJob(full); }
    }).catch(e => setError(String(e)));
    return () => { alive.current = false; };
  }, [refresh]);
  useEffect(() => { try { localStorage.setItem(colorKey, JSON.stringify(colors)); } catch { /* 浏览器禁用存储时保留本次会话颜色。 */ } }, [colors]);
  useEffect(() => {
    let disposed = false;
    if (job?.kind.startsWith("analysis:") && ["queued", "running"].includes(job.status)) return;
    const missing = selected.filter(id => !records[id] && history.find(h => h.id === id)?.artifacts.includes("display.json"));
    if (!missing.length) { setLoading(false); return; }
    setLoading(true);
    Promise.all(missing.map(async id => {
      const prefix = "/api/runs/scope-capture/" + encodeURIComponent(id) + "/artifacts/";
      const [display, spectrum] = await Promise.all([api<Display>(prefix + "display.json"), api<Spectrum>(prefix + "psd.json")]);
      return [id, { display, spectrum }] as const;
    })).then(entries => { if (!disposed) setRecords(old => ({ ...old, ...Object.fromEntries(entries) })); })
      .catch(e => { if (!disposed) setError("历史数据加载失败：" + String(e)); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [selected, records, history, job?.kind, job?.status]);
  useEffect(() => {
    if (!job || !["completed", "failed", "cancelled"].includes(job.status) || loadedJob.current === job.id) return;
    loadedJob.current = job.id;
    refresh().catch(e => setError(String(e)));
    const result = job.result as { run_dir?: string } | undefined;
    const id = result?.run_dir?.split(/[\\/]/).pop();
    if (job.status === "completed" && id) {
      setRecords(old => { const next = { ...old }; delete next[id]; return next; });
      setSelected(old => [...new Set([...old, id])]);
    }
  }, [job, refresh]);
  const colorFor = (id: string) => colors[id] || palette[[...id].reduce((sum, c) => sum + c.charCodeAt(0), 0) % palette.length];
  const traces = useMemo(() => selected.filter(id => !hidden.includes(id) && records[id]).map(id => ({
    id, color: colorFor(id), x: mode === "time" ? records[id].display.time_s : records[id].spectrum.frequency_hz,
    y: mode === "time" ? records[id].display.voltage_v : records[id].spectrum.psd_v2_hz,
    ...(mode === "time" ? {
      rangeMin: records[id].display.vertical_range_min_v ?? undefined,
      rangeMax: records[id].display.vertical_range_max_v ?? undefined,
      overrange: records[id].display.overrange ?? undefined,
    } : {}),
  })), [selected, hidden, records, colors, mode]);
  const run = async () => {
    setBusy(true); setError(""); setNotice("");
    try {
      const parameters = { ...values, mode };
      const preflight = await api<{ ok: boolean; errors: string[] }>(base + "/preflight", { method: "POST", body: JSON.stringify({ parameters }) });
      if (!preflight.ok) throw new Error(preflight.errors.join("；"));
      setJob(await api<Job>(base + "/runs", { method: "POST", body: JSON.stringify({ parameters }) }));
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const remove = async () => {
    const ids = [...selected];
    if (!ids.length || !window.confirm("永久删除以下 " + ids.length + " 条历史数据及其原始文件？\n" + ids.join("\n"))) return;
    setBusy(true); setError("");
    try {
      for (const id of ids) {
        await api("/api/runs/scope-capture/" + encodeURIComponent(id), { method: "DELETE" });
        setSelected(old => old.filter(x => x !== id)); setHidden(old => old.filter(x => x !== id));
        setRecords(old => { const next = { ...old }; delete next[id]; return next; });
        setColors(old => { const next = { ...old }; delete next[id]; return next; });
      }
    } catch (e) { setError(String(e)); }
    finally { await refresh().catch(e => setError(String(e))); setBusy(false); }
  };
  const save = async () => {
    setBusy(true); setError("");
    try { await api(base + "/defaults", { method: "PUT", body: JSON.stringify({ parameters: { ...values, mode } }) }); setNotice("默认参数已保存"); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const reanalyze = async () => {
    if (selected.length !== 1) return;
    setBusy(true); setError("");
    try {
      const id = selected[0];
      setJob(await api<Job>("/api/runs/" + encodeURIComponent(id) + "/analyze", {
        method: "POST", body: JSON.stringify({ experiment_id: "scope-capture" }),
      }));
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const loaded = selected.map(id => records[id]).filter(Boolean);
  const latest = loaded[loaded.length - 1];
  return <div className="scope-page">
    <header className="scope-heading"><h1>示波器采集</h1><span>SDS · 单通道</span></header>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert success" role="status">{notice}</div>}
    <div className="scope-workspace">
      <section className="scope-display">
        <div className="scope-toolbar">
          <div className="scope-modes" role="group" aria-label="显示模式">
            {schema?.fields.find(f => f.name === "mode")?.options?.map(option => <button key={option.value} aria-pressed={mode === option.value} onClick={() => { setMode(String(option.value)); setReset(n => n + 1); }}>{option.label}</button>)}
          </div>
          {mode === "frequency" && <div className="scope-axis-scales" role="group" aria-label="频谱坐标轴">
            <label><input type="checkbox" aria-label="频率轴 log" checked={spectrumXAxisLog} onChange={e => { setSpectrumXAxisLog(e.target.checked); setReset(n => n + 1); }} />频率轴 log</label>
            <label><input type="checkbox" aria-label="PSD 轴 log" checked={spectrumYAxisLog} onChange={e => { setSpectrumYAxisLog(e.target.checked); setReset(n => n + 1); }} />PSD 轴 log</label>
          </div>}
          <span>{loading ? "正在加载波形" : traces.length + " 条曲线"}</span>
          <button className="scope-icon" title="重置缩放" aria-label="重置缩放" onClick={() => setReset(n => n + 1)}><RotateCcw size={17} /></button>
        </div>
        <div className="scope-plot-wrap"><ScopePlot traces={traces} mode={mode} reset={reset}
          xScale={mode === "frequency" && spectrumXAxisLog ? "log" : "linear"}
          yScale={mode === "frequency" && spectrumYAxisLog ? "log" : "linear"} />
          {!traces.length && <div className="scope-empty">{loading ? "正在加载" : "暂无显示数据"}</div>}
        </div>
        <div className="scope-readout">
          <span>实际采样率 <b>{latest ? latest.display.actual_rate_sa_s.toPrecision(6) + " Sa/s" : "--"}</b></span>
          <span>记录长度 <b>{latest ? latest.display.sample_count.toLocaleString() + " 点" : "--"}</b></span>
          <span>实际时长 <b>{latest ? latest.display.actual_duration_s.toPrecision(6) + " s" : "--"}</b></span>
          {mode === "frequency" && <span>频率间隔 <b>{latest ? latest.spectrum.frequency_resolution_hz.toPrecision(5) + " Hz" : "--"}</b></span>}
          {mode === "time" && <>
            <span>垂直档位 <b>{latest?.display.vertical_scale_v_div != null ? latest.display.vertical_scale_v_div + " V/div" : "--"}</b></span>
            <span>垂直偏置 <b>{latest?.display.vertical_offset_v != null ? latest.display.vertical_offset_v + " V" : "--"}</b></span>
            <span>量程 <b>{latest?.display.vertical_range_min_v != null && latest?.display.vertical_range_max_v != null ? latest.display.vertical_range_min_v + " ~ " + latest.display.vertical_range_max_v + " V" : "--"}</b></span>
            <span>状态 <b style={latest?.display.overrange ? { color: "#c82828" } : undefined}>{latest?.display.overrange == null ? "未知" : latest.display.overrange ? "超量程" : "正常"}</b></span>
            {latest?.display.overrange_fraction != null && <span>超量程比例 <b>{(latest.display.overrange_fraction * 100).toPrecision(3)}%</b></span>}
          </>}
        </div>
        <JobView job={job} onUpdate={setJob} />
      </section>
      <div className="scope-controls">
        <section className="scope-settings"><div className="scope-section-head"><h2>采集设置</h2>
          <button className="scope-icon" title="保存默认参数" aria-label="保存默认参数" disabled={!schema || busy} onClick={save}><Save size={17} /></button></div>
          {schema?.fields.filter(f => f.name !== "mode").map(f => <label key={f.name}>{f.label}{f.unit && " (" + f.unit + ")"}
            {f.options ? <select disabled={active || busy} value={String(values[f.name] ?? "")} onChange={e => setValues(old => ({ ...old, [f.name]: f.options?.find(o => String(o.value) === e.target.value)?.value ?? e.target.value }))}>
              {f.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
              : f.type === "boolean"
                ? <input disabled={active || busy} type="checkbox" checked={Boolean(values[f.name])} onChange={e => setValues(old => ({ ...old, [f.name]: e.target.checked }))} />
                : <input disabled={active || busy} type={f.type === "string" ? "text" : "number"} value={String(values[f.name] ?? "")} min={f.minimum} max={f.maximum} step="any" onChange={e => setValues(old => ({ ...old, [f.name]: f.type === "string" || e.target.value === "" ? e.target.value : Number(e.target.value) }))} />}
          </label>)}
          <button className="scope-start" disabled={!schema || busy || active || hardwareBusy} onClick={run}><Play size={17} />{active ? "采集中" : busy ? "处理中" : "开始采集"}</button>
          {hardwareBusy && !active && <p className="scope-muted">其他硬件任务正在运行</p>}
          {mode === "frequency" && <p className="scope-muted">Hann · 去直流 · V²/Hz</p>}
        </section>
        <section className="scope-history"><div className="scope-section-head"><h2>历史 <small>{total}</small></h2><div>
          <button className="scope-icon" title="刷新历史" aria-label="刷新历史" disabled={busy} onClick={() => refresh().catch(e => setError(String(e)))}><RefreshCw size={16} /></button>
          <button className="scope-icon" title="重新分析选定数据" aria-label="重新分析选定数据" disabled={selected.length !== 1 || busy || active} onClick={reanalyze}><ChartNoAxesCombined size={16} /></button>
          <button className="scope-icon danger" title="删除选定历史数据" aria-label="删除选定历史数据" disabled={!selected.length || busy || active} onClick={remove}><Trash2 size={16} /></button></div></div>
          <div className="scope-history-list">{history.map(item => <div className="scope-history-row" key={item.id}>
            <input type="checkbox" aria-label={"选择 " + item.id} checked={selected.includes(item.id)} disabled={busy} onChange={e => { setError(""); setSelected(old => e.target.checked ? [...old, item.id] : old.filter(id => id !== item.id)); }} />
            <div className="scope-history-name" title={item.id}>{item.id}<small>{item.artifacts.includes("display.json") ? "C" + (records[item.id]?.display.channel || "·") : "待分析"}</small></div>
            <input type="color" aria-label={"颜色 " + item.id} value={colorFor(item.id)} onChange={e => setColors(old => ({ ...old, [item.id]: e.target.value }))} />
            <button className="scope-icon" title={hidden.includes(item.id) ? "显示" : "隐藏"} aria-label={(hidden.includes(item.id) ? "显示 " : "隐藏 ") + item.id} disabled={!selected.includes(item.id)} onClick={() => setHidden(old => old.includes(item.id) ? old.filter(id => id !== item.id) : [...old, item.id])}>{hidden.includes(item.id) ? <EyeOff size={16} /> : <Eye size={16} />}</button>
          </div>)}</div>
          {!history.length && <p className="scope-muted">暂无历史数据</p>}
          {history.length < total && <button className="secondary compact" disabled={busy} onClick={() => refresh(history.length).catch(e => setError(String(e)))}>加载更多</button>}
        </section>
      </div>
    </div>
  </div>;
}
