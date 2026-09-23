import { useEffect, useState, type KeyboardEvent } from "react";
import { api } from "../api";
import { PageHead } from "../components/PageHead";
import { JobView } from "../components/JobView";
import { useJobActivity } from "../components/JobActivity";
import { ParameterForm } from "../components/MxYOptimalControlFrequency/ParameterForm";
import { defaultParameterLayout, sameParameterLayout, validatedParameterLayout } from "./experimentHelpers";
import type { ExperimentDefinition, ExperimentSchema, Job, JobSummary, ParameterLayout, ParameterValues } from "../types/api";
import "./zawClosedLoop.css";

const ID = "bell-bloom-z-field-calibration";
const BASE = `/api/experiments/${ID}`;
const terminal = (job: Job | undefined) => Boolean(job && ["completed", "failed", "cancelled"].includes(job.status));
type Run = { run_id: string; timestamp: string; run_tag: string; completion_status: string };
type RunList = { runs: Run[]; total: number };
type Summary = Run & {
  parameters: ParameterValues;
  analysis_status: string;
  failure_reason: string;
  analysis_error: string;
  safety_shutdown: { errors?: string[] };
  artifacts: Record<string, string>;
  analysis: null | {
    success: boolean;
    K_Z_Hz_per_V: number | null;
    f_0V_Hz: number | null;
    linear_fit: { uncertainties: (number | null)[]; r_squared: number | null };
    rejection_reasons: string[];
  };
};
const summaryUrl = (runId: string) => `${BASE}/runs/${encodeURIComponent(runId)}/summary`;
const number = (value: number | null) => value === null ? "—" : value.toLocaleString("zh-CN", { maximumSignificantDigits: 7 });
const statusLabels: Record<string, string> = { completed: "完成", failed: "失败", cancelled: "已取消", running: "运行中", not_started: "未分析" };
const status = (value: string) => statusLabels[value] ?? value;

function Result({ value }: { value: Summary }) {
  const analysis = value.analysis;
  return <div className="zaw-latest">
    <p>{value.run_id} · {value.timestamp}</p>
    <p>采集：{status(value.completion_status)} · 分析：{status(value.analysis_status)}</p>
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
      <p>{["analysis.yaml", "analysis.json", "calibration_results.npz"].filter(name => value.artifacts[name]).map(name => <a key={name} href={value.artifacts[name]} download style={{ marginRight: 12 }}>{name}</a>)}</p>
    </>}
  </div>;
}

export function BellBloomZCalibrationPage() {
  const [tab, setTab] = useState<"run" | "history">("run");
  const [definition, setDefinition] = useState<ExperimentDefinition>();
  const [fields, setFields] = useState<ExperimentSchema["fields"]>([]);
  const [values, setValues] = useState<ParameterValues>({});
  const [layout, setLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [savedLayout, setSavedLayout] = useState(layout);
  const [job, setJob] = useState<Job>();
  const [analysisJob, setAnalysisJob] = useState<Job>();
  const [starting, setStarting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preflight, setPreflight] = useState<{ ok: boolean; errors: string[] }>();
  const [latest, setLatest] = useState<Summary>();
  const [list, setList] = useState<RunList>({ runs: [], total: 0 });
  const [selectedId, setSelectedId] = useState("");
  const [selected, setSelected] = useState<Summary>();
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const { hardwareBusy } = useJobActivity();
  const jobActive = Boolean(job && !terminal(job));
  const analysisActive = Boolean(analysisJob && !terminal(analysisJob));

  useEffect(() => {
    let disposed = false;
    Promise.all([api<ExperimentDefinition>(BASE), api<ExperimentSchema>(`${BASE}/schema`), api<JobSummary[]>("/api/jobs")]).then(async ([def, schema, jobs]) => {
      if (disposed) return;
      setDefinition(def); setFields(schema.fields);
      setValues(Object.fromEntries(schema.fields.map(field => [field.name, field.default])));
      const initial = validatedParameterLayout(schema.fields, schema.parameter_layout) ?? defaultParameterLayout(schema.fields);
      setLayout(initial); setSavedLayout(initial);
      const active = jobs.find(item => item.kind === `experiment:${ID}` && ["queued", "running"].includes(item.status));
      if (active) { const recovered = await api<Job>(`/api/jobs/${active.id}`); if (!disposed) setJob(recovered); }
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    let disposed = false;
    api<RunList>(`${BASE}/runs?limit=1&offset=0`).then(async data => {
      const result = data.runs.length ? await api<Summary>(summaryUrl(data.runs[0].run_id)) : undefined;
      if (!disposed) { setLatest(result); setSelectedId(current => current || result?.run_id || ""); }
    }).catch(reason => { if (!disposed) { setLatest(undefined); setError(String(reason)); } });
    return () => { disposed = true; };
  }, [refresh, job?.id, job?.status, analysisJob?.status]);

  useEffect(() => {
    let disposed = false;
    api<RunList>(`${BASE}/runs?limit=50&offset=${offset}`).then(data => { if (!disposed) setList(data); }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [offset, refresh, job?.status]);

  useEffect(() => {
    if (!selectedId) return;
    let disposed = false;
    setSelected(undefined);
    api<Summary>(summaryUrl(selectedId)).then(data => { if (!disposed) setSelected(data); }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [selectedId, refresh, job?.status, analysisJob?.status]);

  const check = async (start: boolean) => {
    setStarting(true); setError("");
    try {
      const result = await api<{ ok: boolean; errors: string[] }>(`${BASE}/preflight`, { method: "POST", body: JSON.stringify({ parameters: values }) });
      setPreflight(result);
      if (start && result.ok) setJob(await api<Job>(`${BASE}/runs`, { method: "POST", body: JSON.stringify({ parameters: values }) }));
    } catch (reason) { setError(String(reason)); } finally { setStarting(false); }
  };
  const save = async () => {
    setSaving(true); setError("");
    try {
      const result = await api<{ schema: ExperimentSchema }>(`${BASE}/defaults`, { method: "PUT", body: JSON.stringify({ parameters: values, parameter_layout: layout }) });
      const persisted = validatedParameterLayout(result.schema.fields, result.schema.parameter_layout);
      if (!persisted || !sameParameterLayout(persisted, layout)) throw new Error("后端未确认参数布局已保存");
      setSavedLayout(persisted); setNotice("参数与布局已保存");
    } catch (reason) { setError(String(reason)); } finally { setSaving(false); }
  };
  const reanalyze = async (runId: string) => {
    setStarting(true); setError("");
    try { setAnalysisJob(await api<Job>(`/api/runs/${encodeURIComponent(runId)}/analyze`, { method: "POST", body: JSON.stringify({ experiment_id: ID }) })); }
    catch (reason) { setError(String(reason)); } finally { setStarting(false); }
  };
  const tabKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const next = tab === "run" ? "history" : "run";
    setTab(next); event.currentTarget.querySelector<HTMLButtonElement>(`#bb-z-tab-${next}`)?.focus();
  };

  return <>
    <PageHead eyebrow="CALIBRATION · BELL BLOOM" title={definition?.title ?? "Bell Bloom Z 磁场频率标定"} description={definition?.description ?? "读取参数与运行记录。"} />
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="zaw-tab-strip" role="tablist" aria-label="Bell Bloom Z 标定视图" onKeyDown={tabKey}>
      {(["run", "history"] as const).map(item => <button key={item} id={`bb-z-tab-${item}`} role="tab" aria-selected={tab === item} aria-controls={`bb-z-panel-${item}`} tabIndex={tab === item ? 0 : -1} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>{item === "run" ? "运行" : "历史"}</button>)}
    </div>
    <div id="bb-z-panel-run" role="tabpanel" aria-labelledby="bb-z-tab-run" hidden={tab !== "run"}>
      <div className="zaw-dashboard">
        <section className="zaw-panel zaw-panel-params"><h2>实验参数</h2>
          <ParameterForm fields={fields} values={values} layout={layout} onChange={(field, value) => setValues(old => ({ ...old, [field.name]: value }))} onLayoutChange={setLayout} onMoveNotice={setNotice} disabled={jobActive || starting} />
          {!sameParameterLayout(layout, savedLayout) && <p>布局有未保存修改</p>}
          <button disabled={saving || jobActive || !fields.length} onClick={() => void save()}>保存参数与布局</button>
          {notice && <p role="status">{notice}</p>}
        </section>
        <section className="zaw-panel zaw-panel-control"><h2>控制与状态</h2>
          <div className="zaw-control-buttons"><button disabled={starting || jobActive || !fields.length} onClick={() => void check(false)}>仅预检</button><button disabled={starting || jobActive || hardwareBusy || analysisActive || !fields.length} onClick={() => void check(true)}>{starting ? "处理中…" : "预检并运行实验"}</button></div>
          {preflight && <div className={`alert ${preflight.ok ? "success" : "error"}`}>{preflight.ok ? "参数预检通过" : preflight.errors.join("；")}</div>}
          {hardwareBusy && !jobActive && <p>其他硬件任务正在运行。</p>}
          {definition?.wiring_notes.map(note => <p key={note}>{note}</p>)}
          <JobView job={job} onUpdate={setJob} />
        </section>
        <section className="zaw-panel zaw-panel-result"><h2>最近一次结果</h2>
          {jobActive && <p>实验正在运行；下方展示已保存的运行记录及其时间。</p>}
          {latest ? <><Result value={latest} /><button onClick={() => { setSelectedId(latest.run_id); setTab("history"); }}>查看本次详情</button></> : <p>暂无运行记录。</p>}
        </section>
      </div>
    </div>
    <div id="bb-z-panel-history" role="tabpanel" aria-labelledby="bb-z-tab-history" hidden={tab !== "history"}>
      <div className="zaw-history"><section className="zaw-run-list"><h2>历史运行（{list.total}）</h2><button onClick={() => setRefresh(value => value + 1)}>刷新记录</button>
        <ul>{list.runs.map(run => <li key={run.run_id}><button className={`zaw-run-card ${selectedId === run.run_id ? "selected" : ""}`} onClick={() => setSelectedId(run.run_id)}><strong>{run.run_id}</strong><span>{run.timestamp}</span><span>{status(run.completion_status)}</span></button></li>)}</ul>
        <button disabled={offset === 0} onClick={() => setOffset(value => value - 50)}>上一页</button><button disabled={offset + 50 >= list.total} onClick={() => setOffset(value => value + 50)}>下一页</button>
      </section><section className="zaw-run-details">{selected ? <>
        <div className="zaw-run-details-actions"><button disabled={jobActive || starting} onClick={() => { setValues(selected.parameters); setTab("run"); }}>填回参数</button><button disabled={starting || jobActive || analysisActive || selected.completion_status === "running"} onClick={() => void reanalyze(selected.run_id)}>重新分析</button></div>
        <Result value={selected} /><details><summary>本次参数</summary><table><tbody>{Object.entries(selected.parameters).map(([key, value]) => <tr key={key}><th>{fields.find(field => field.name === key)?.label ?? key}</th><td>{String(value)}</td></tr>)}</tbody></table></details>
      </> : <p>{selectedId ? "正在读取运行详情…" : "暂无运行记录。"}</p>}</section></div>
    </div>
    <JobView job={analysisJob} onUpdate={setAnalysisJob} />
  </>;
}
