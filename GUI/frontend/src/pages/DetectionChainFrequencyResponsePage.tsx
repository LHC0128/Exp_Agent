import { useEffect, useState, type KeyboardEvent } from "react";
import { api } from "../api";
import { PageHead } from "../components/PageHead";
import { JobView } from "../components/JobView";
import { useJobActivity } from "../components/JobActivity";
import { ParameterForm } from "../components/MxYOptimalControlFrequency/ParameterForm";
import { defaultParameterLayout, sameParameterLayout, validatedParameterLayout } from "./experimentHelpers";
import type { ExperimentDefinition, ExperimentSchema, Job, JobSummary, ParameterLayout, ParameterValues } from "../types/api";
import "./zawClosedLoop.css";

const ID = "detection-chain-frequency-response";
const BASE = `/api/experiments/${ID}`;
const terminal = (job: Job | undefined) => Boolean(job && ["completed", "failed", "cancelled"].includes(job.status));
type Run = { run_id: string; timestamp: string; run_tag: string; completion_status: string };
type RunList = { runs: Run[]; total: number };
type Summary = Run & {
  parameters: ParameterValues;
  analysis_status: string;
  failure_reason: string;
  analysis_error: string;
  temperature_control?: { control_source?: string; actual_temperature_c?: number | null } | null;
  artifacts: Record<string, string>;
};

const summaryUrl = (runId: string) => `${BASE}/runs/${encodeURIComponent(runId)}/summary`;
const statusLabels: Record<string, string> = { 
  completed: "完成", 
  failed: "失败", 
  cancelled: "已取消", 
  running: "运行中", 
  not_started: "未分析" 
};
const status = (value: string) => statusLabels[value] ?? value;

function Result({ value }: { value: Summary }) {
  return <div className="zaw-latest">
    <p><strong>{value.run_id}</strong> · {value.timestamp}</p>
    <p>采集：{status(value.completion_status)} · 分析：{status(value.analysis_status)}</p>
    {value.temperature_control?.control_source === "external_software" &&
      <p className="alert">外部温控：程序未设置或检测温度，请确认外部软件维持目标温度。</p>
    }
    {value.failure_reason && <div className="alert error">{value.failure_reason}</div>}
    {value.analysis_error && <div className="alert error">{value.analysis_error}</div>}
    {value.artifacts["frequency_response.png"] && 
      <figure className="zaw-figure">
        <figcaption>探测链路频率响应</figcaption>
        <img src={value.artifacts["frequency_response.png"]} alt="频率响应曲线" />
      </figure>
    }
    {!value.artifacts["frequency_response.png"] && <p className="zaw-empty">本次运行暂无结果图。</p>}
    <p>
      {["analysis_results.npz"].filter(name => value.artifacts[name]).map(name => 
        <a key={name} href={value.artifacts[name]} download style={{ marginRight: 12 }}>{name}</a>
      )}
    </p>
  </div>;
}

export function DetectionChainFrequencyResponsePage() {
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
    Promise.all([
      api<ExperimentDefinition>(BASE), 
      api<ExperimentSchema>(`${BASE}/schema`), 
      api<JobSummary[]>("/api/jobs")
    ]).then(async ([def, schema, jobs]) => {
      if (disposed) return;
      setDefinition(def);
      setFields(schema.fields);
      setValues(Object.fromEntries(schema.fields.map(field => [field.name, field.default])));
      const initial = validatedParameterLayout(schema.fields, schema.parameter_layout) ?? defaultParameterLayout(schema.fields);
      setLayout(initial);
      setSavedLayout(initial);
      const active = jobs.find(item => item.kind === `experiment:${ID}` && ["queued", "running"].includes(item.status))
        ?? jobs.find(item => item.kind === `experiment:${ID}` && item.started_at);
      if (active) {
        const recovered = await api<Job>(`/api/jobs/${active.id}`);
        if (!disposed) setJob(recovered);
      }
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    let disposed = false;
    api<RunList>(`${BASE}/runs?limit=1&offset=0`).then(async data => {
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
    api<RunList>(`${BASE}/runs?limit=50&offset=${offset}`).then(data => {
      if (!disposed) setList(data);
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [job?.status, offset, refresh]);

  useEffect(() => {
    if (!selectedId) return;
    let disposed = false;
    setSelected(undefined);
    api<Summary>(summaryUrl(selectedId)).then(data => {
      if (!disposed) setSelected(data);
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [selectedId, job?.status, analysisJob?.status, refresh]);

  const check = async (start: boolean) => {
    setStarting(true);
    setError("");
    try {
      const result = await api<{ ok: boolean; errors: string[] }>(`${BASE}/preflight`, {
        method: "POST",
        body: JSON.stringify({ parameters: values })
      });
      if (!result.ok) {
        setError(result.errors.join("；"));
        return;
      }
      if (start) {
        setJob(await api<Job>(`${BASE}/runs`, {
          method: "POST",
          body: JSON.stringify({ parameters: values })
        }));
      } else {
        setNotice("预检通过");
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
        body: JSON.stringify({ parameters: values, parameter_layout: layout })
      });
      const persisted = validatedParameterLayout(result.schema.fields, result.schema.parameter_layout);
      if (!persisted || !sameParameterLayout(persisted, layout)) throw new Error("后端未确认参数布局已保存");
      setSavedLayout(persisted);
      setNotice("参数与布局已保存");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSaving(false);
    }
  };

  const reanalyze = async (runId: string) => {
    setError("");
    try {
      setAnalysisJob(await api<Job>(`/api/runs/${encodeURIComponent(runId)}/analyze`, {
        method: "POST", body: JSON.stringify({ experiment_id: ID })
      }));
    } catch (reason) { setError(String(reason)); }
  };

  const tabKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const next = tab === "run" ? "history" : "run";
    setTab(next);
    event.currentTarget.querySelector<HTMLButtonElement>(`#dc-tab-${next}`)?.focus();
  };

  return <>
    <PageHead eyebrow="CALIBRATION · FREQUENCY" title={definition?.title ?? "探测链路频率响应标定"} description={definition?.description ?? "读取参数与运行记录。"} />
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="zaw-tab-strip" role="tablist" aria-label="探测链路频率响应标定视图" onKeyDown={tabKey}>
      {(["run", "history"] as const).map(item => <button key={item} id={`dc-tab-${item}`} role="tab" aria-selected={tab === item} aria-controls={`dc-panel-${item}`} tabIndex={tab === item ? 0 : -1} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>{item === "run" ? "运行" : "历史"}</button>)}
    </div>
    <div id="dc-panel-run" role="tabpanel" aria-labelledby="dc-tab-run" hidden={tab !== "run"}>
      <div className="zaw-dashboard">
        <section className="zaw-panel zaw-panel-params">
          <div className="zaw-panel-head"><small>PARAMETERS</small><h2>实验参数</h2></div>
          <ParameterForm fields={fields} values={values} layout={layout} onChange={(field, value) => setValues(old => ({ ...old, [field.name]: value }))} onLayoutChange={setLayout} onMoveNotice={setNotice} disabled={jobActive || starting} />
          {!sameParameterLayout(layout, savedLayout) && <p className="zaw-layout-dirty">布局有未保存修改</p>}
          <button className="zaw-secondary" disabled={saving || jobActive || !fields.length} onClick={() => void save()}>{saving ? "保存中…" : "保存参数与布局"}</button>
          {notice && <p role="status">{notice}</p>}
        </section>
        <section className="zaw-panel zaw-panel-control">
          <div className="zaw-panel-head"><small>CONTROL</small><h2>控制与状态</h2></div>
          <div className="zaw-control-buttons">
            <button className="zaw-secondary" disabled={starting || jobActive || !fields.length} onClick={() => void check(false)}>仅预检</button>
            <button className="zaw-primary" disabled={starting || jobActive || hardwareBusy || !fields.length} onClick={() => void check(true)}>{jobActive ? "实验运行中" : starting ? "正在启动…" : "预检并运行实验"}</button>
          </div>
          {hardwareBusy && !jobActive && <p>其他硬件任务正在运行。</p>}
          {definition?.wiring_notes.map(note => <p key={note}>{note}</p>)}
          <JobView job={job} onUpdate={setJob} />
        </section>
        <section className="zaw-panel zaw-panel-result">
          <div className="zaw-panel-head"><small>LATEST</small><h2>最近一次结果</h2></div>
          {latest ? <><Result value={latest} /><button className="zaw-secondary" onClick={() => { setSelectedId(latest.run_id); setTab("history"); }}>查看本次详情</button></> : <p className="zaw-empty">暂无运行记录。</p>}
        </section>
      </div>
    </div>
    <div id="dc-panel-history" role="tabpanel" aria-labelledby="dc-tab-history" hidden={tab !== "history"}>
      <div className="zaw-history">
        <section className="zaw-run-list">
          <div className="zaw-run-list-head"><small>RUNS</small><h3>历史运行（{list.total}）</h3></div>
          <button className="zaw-secondary" onClick={() => setRefresh(value => value + 1)}>刷新记录</button>
          <ul>{list.runs.map(run => <li key={run.run_id}><button className={`zaw-run-card ${selectedId === run.run_id ? "selected" : ""}`} onClick={() => setSelectedId(run.run_id)}><strong>{run.run_id}</strong><span>{run.timestamp}</span><span>{status(run.completion_status)}</span></button></li>)}</ul>
          {!list.runs.length && <p className="zaw-empty">暂无运行记录。</p>}
          <div className="zaw-control-buttons"><button className="zaw-secondary" disabled={!offset} onClick={() => setOffset(value => value - 50)}>上一页</button><button className="zaw-secondary" disabled={offset + 50 >= list.total} onClick={() => setOffset(value => value + 50)}>下一页</button></div>
        </section>
        <section className="zaw-run-details">
          {selected ? <>
            <div className="zaw-run-details-actions"><button className="zaw-secondary" disabled={jobActive || starting} onClick={() => { setValues(current => ({ ...current, ...Object.fromEntries(Object.entries(selected.parameters).filter(([key]) => fields.some(field => field.name === key))) })); setTab("run"); }}>填回参数</button><button className="zaw-secondary" disabled={jobActive || analysisActive || selected.completion_status === "running"} onClick={() => void reanalyze(selected.run_id)}>重新分析</button></div>
            <Result value={selected} />
            <details className="zaw-parameter-table"><summary>本次参数</summary><table><tbody>{Object.entries(selected.parameters).map(([key, value]) => <tr key={key}><th>{fields.find(field => field.name === key)?.label ?? key}</th><td>{String(value)}</td></tr>)}</tbody></table></details>
          </> : <p className="zaw-empty">{selectedId ? "正在读取运行详情…" : "请选择一条运行记录。"}</p>}
        </section>
      </div>
    </div>
    <JobView job={analysisJob} onUpdate={setAnalysisJob} />
  </>;
}
