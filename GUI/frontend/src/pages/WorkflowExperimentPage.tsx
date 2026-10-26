import { useEffect, useState, type KeyboardEvent, type ReactNode } from "react";

import { api } from "../api";
import { useJobActivity } from "../components/JobActivity";
import { JobView } from "../components/JobView";
import { WorkflowPanel, type XYSession } from "../components/workflow/WorkflowPanel";
import { PageHead } from "../components/PageHead";
import { recoverableJob, useExperimentTask, useRequestedJobId } from "../components/experimentNavigation";
import { useParameterGuard } from "../components/NavigationGuard";
import type {
  ExperimentDefinition, ExperimentSchema, Job, JobSummary, ParameterLayout, ParameterValues,
} from "../types/api";
import type { components } from "../types/openapi";
import {
  defaultParameterLayout, historyParameterValues, preflightMessage, sameParameterLayout,
  validatedParameterLayout, type PreflightResult,
} from "./experimentHelpers";
import "./zawClosedLoop.css";

export type WorkflowRunItem = components["schemas"]["WorkflowRunItem"];
export type WorkflowRunSummary = Omit<components["schemas"]["WorkflowRunSummary"], "parameters"> & {
  parameters: ParameterValues;
};
type RunList = components["schemas"]["WorkflowRuns"];
type Props = {
  experimentId: string;
  analysisParameterPrefix?: string; analysisParameterKeys?: Record<string, string>;
  normalizeParameters?: (values: ParameterValues) => ParameterValues;
  renderResult?: (summary: WorkflowRunSummary) => ReactNode;
  hasAnalysis?: boolean;
};
const identity = (values: ParameterValues) => values;
const active = (job?: Job) => Boolean(job && ["queued", "running"].includes(job.status));
const PAGE_SIZE = 50;
const labels: Record<string, string> = {
  completed: "完成", failed: "失败", cancelled: "已取消", running: "运行中",
  not_started: "未分析", quality_failed: "质量未通过", unknown: "未记录",
};
const status = (value: string) => labels[value] ?? value;

function DefaultResult({ summary, hasAnalysis }: { summary: WorkflowRunSummary; hasAnalysis: boolean }) {
  const artifacts = summary.artifacts ?? {};
  return <div className="zaw-latest">
    <p>{summary.run_id} · {summary.timestamp} · 采集：{status(summary.completion_status)}{hasAnalysis && <> · 分析：{status(summary.analysis_status)}</>}</p>
    {summary.analysis_error && <p role="alert">{summary.analysis_error}</p>}
    {!Object.keys(artifacts).length && <p>本次运行暂无结果文件。</p>}
    {Object.entries(artifacts).filter(([name]) => /\.(png|jpe?g|svg|webp)$/i.test(name)).map(([name, url]) =>
      <figure className="zaw-figure" key={name}><figcaption>{name}</figcaption><img src={url} alt={name} loading="lazy" /></figure>)}
    <p className="xy-artifact-links">{Object.entries(artifacts).map(([name, url]) =>
      <a key={name} href={url} download>{name}</a>)}</p>
  </div>;
}

export function WorkflowExperimentPage({ experimentId,
  analysisParameterPrefix, analysisParameterKeys, normalizeParameters = identity,
  renderResult, hasAnalysis = true }: Props) {
  const BASE = `/api/experiments/${experimentId}`;
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
  const [preview, setPreview] = useState<ParameterValues>({});
  const [list, setList] = useState<RunList>({ runs: [], total: 0 });
  const [latest, setLatest] = useState<WorkflowRunSummary>();
  const [selectedId, setSelectedId] = useState("");
  const [selected, setSelected] = useState<WorkflowRunSummary>();
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const { hardwareBusy } = useJobActivity();
  const jobActive = active(job);
  const analysisActive = active(analysisJob);
  const live = Boolean(session && session.status !== "closed");
  const parametersBusy = jobActive && (!live || session?.status !== "waiting" || Boolean(session?.pending_request));
  useParameterGuard(fields, values, layout, savedLayout, live ? session?.parameters as ParameterValues : undefined);
  useExperimentTask({ ready: fields.length > 0, setTab, setJob, setAnalysisJob, setSelectedId });
  const requestedJobId = useRequestedJobId();
  const summaryUrl = (id: string) => `${BASE}/runs/${encodeURIComponent(id)}/summary`;
  const result = (summary: WorkflowRunSummary) => renderResult
    ? renderResult(summary) : <DefaultResult summary={summary} hasAnalysis={hasAnalysis} />;

  useEffect(() => {
    let disposed = false;
    void Promise.all([
      api<ExperimentDefinition>(BASE), api<ExperimentSchema>(`${BASE}/schema`), api<JobSummary[]>("/api/jobs"),
    ]).then(async ([def, schema, jobs]) => {
      if (disposed) return;
      setDefinition(def); setFields(schema.fields);
      setValues(normalizeParameters(Object.fromEntries(schema.fields.map(field => [field.name, field.default]))));
      const saved = schema.parameter_layout_saved ? validatedParameterLayout(schema.fields, schema.parameter_layout) : undefined;
      const initial = saved ?? defaultParameterLayout(schema.fields);
      setLayout(initial); setSavedLayout(initial);
      const recovered = recoverableJob(jobs, experimentId, requestedJobId);
      if (recovered) {
        const full = await api<Job>(`/api/jobs/${recovered.id}`);
        if (!disposed) setJob(full);
      }
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [BASE, experimentId, normalizeParameters]);

  useEffect(() => {
    if (!fields.length) return;
    let disposed = false;
    const timer = window.setTimeout(() => {
      void api<{ values: ParameterValues }>(`${BASE}/derive`, {
        method: "POST", body: JSON.stringify({ parameters: values }),
      }).then(data => {
        if (disposed) return;
        setPreview(data.values ?? {});
        setValues(current => {
          const readOnly = fields.filter(field => field.read_only && field.name in (data.values ?? {}));
          if (!readOnly.some(field => JSON.stringify(current[field.name]) !== JSON.stringify(data.values[field.name]))) return current;
          return { ...current, ...Object.fromEntries(readOnly.map(field => [field.name, data.values[field.name]])) };
        });
      }).catch(reason => { if (!disposed) setError(`派生预览失败：${String(reason)}`); });
    }, 200);
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [BASE, fields, values]);

  useEffect(() => {
    let disposed = false;
    void api<RunList>(`${BASE}/runs?limit=${PAGE_SIZE}&offset=${offset}`).then(data => {
      if (disposed) return;
      setList(data); setSelectedId(current => current || data.runs[0]?.run_id || "");
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [BASE, offset, refresh, job?.status, analysisJob?.status]);

  useEffect(() => {
    let disposed = false;
    void api<RunList>(`${BASE}/runs?limit=1&offset=0`).then(async data => {
      const value = data.runs[0] ? await api<WorkflowRunSummary>(summaryUrl(data.runs[0].run_id)) : undefined;
      if (!disposed) setLatest(value);
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [BASE, refresh, job?.status, analysisJob?.status]);

  useEffect(() => {
    if (!selectedId) { setSelected(undefined); return; }
    let disposed = false;
    setSelected(undefined);
    void api<WorkflowRunSummary>(summaryUrl(selectedId)).then(value => {
      if (!disposed) setSelected(value);
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [BASE, selectedId, refresh, job?.status, analysisJob?.status]);

  const check = async (start: boolean) => {
    setStarting(true); setError("");
    try {
      const checked = await api<PreflightResult>(`${BASE}/preflight`, {
        method: "POST", body: JSON.stringify({ parameters: values }),
      });
      setPreflight(checked);
      if (start && checked.ok) setJob(await api<Job>(`${BASE}/runs`, {
        method: "POST", body: JSON.stringify({ parameters: values }),
      }));
    } catch (reason) { setError(String(reason)); }
    finally { setStarting(false); }
  };
  const save = async () => {
    setSaving(true); setError("");
    try {
      const saved = await api<{ message: string; schema: ExperimentSchema }>(`${BASE}/defaults`, {
        method: "PUT", body: JSON.stringify({ parameters: values, parameter_layout: layout }),
      });
      const confirmed = saved.schema.parameter_layout_saved
        ? validatedParameterLayout(saved.schema.fields, saved.schema.parameter_layout) : undefined;
      if (!confirmed || !sameParameterLayout(confirmed, layout)) throw new Error("后端未确认参数布局已保存");
      setSavedLayout(confirmed); setFields(saved.schema.fields); setNotice(saved.message);
    } catch (reason) { setError(String(reason)); }
    finally { setSaving(false); }
  };
  const reanalyze = async (id: string) => {
    setStarting(true); setError("");
    try {
      setAnalysisJob(await api<Job>(`/api/runs/${encodeURIComponent(id)}/analyze`, {
        method: "POST", body: JSON.stringify({ experiment_id: experimentId }),
      }));
    } catch (reason) { setError(String(reason)); }
    finally { setStarting(false); }
  };
  const tabKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const next = tab === "run" ? "history" : "run";
    setTab(next); event.currentTarget.querySelector<HTMLButtonElement>(`[data-tab="${next}"]`)?.focus();
  };

  return <>
    <PageHead eyebrow="EXPERIMENT" title={definition?.title ?? "正在载入实验"} description={definition?.description ?? "读取实验定义与运行记录。"} experiment={definition} />
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="zaw-tab-strip" role="tablist" aria-label="实验视图" onKeyDown={tabKey}>
      {(["run", "history"] as const).map(item => <button key={item} id={`${experimentId}-tab-${item}`}
        role="tab" data-tab={item} aria-selected={tab === item} aria-controls={`${experimentId}-panel-${item}`}
        tabIndex={tab === item ? 0 : -1} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>
        {item === "run" ? "运行" : "历史"}
      </button>)}
    </div>
    <div id={`${experimentId}-panel-run`} role="tabpanel" aria-labelledby={`${experimentId}-tab-run`} hidden={tab !== "run"}>
      <WorkflowPanel experimentId={experimentId}
        analysisParameterPrefix={analysisParameterPrefix} analysisParameterKeys={analysisParameterKeys}
        normalizeParameters={normalizeParameters} enabled={fields.length > 0} fields={fields}
        layout={layout} setLayout={setLayout} setNotice={setNotice} parametersDisabled={parametersBusy || starting || saving}
        values={values} setValues={setValues} session={session} setSession={setSession} job={job} setJob={setJob}
        analysisJob={analysisJob} setAnalysisJob={setAnalysisJob} hardwareBusy={hardwareBusy}
        runs={list.runs} setError={setError} onRefresh={setRefresh}>
        <div className="xy-workflow-controls">
          <div className="xy-flow-actions">
            <button disabled={saving || starting || parametersBusy || !fields.length} onClick={() => void save()}>{saving ? "保存中…" : "保存参数与布局"}</button>
            <button className="secondary" disabled={starting || parametersBusy || !fields.length} onClick={() => void check(false)}>仅预检</button>
            <button disabled={starting || jobActive || hardwareBusy || analysisActive || !fields.length} onClick={() => void check(true)}>{starting ? "处理中…" : "预检并运行实验"}</button>
          </div>
          <p>在流程节点编辑参数。保存默认值仅保存下一次实验参数和统一分类，不保存历史分析覆盖值。</p>
          {!sameParameterLayout(layout, savedLayout) && <p role="status">布局有未保存修改</p>}
          {notice && <p role="status">{notice}</p>}
          {preflight && <div className={`alert ${preflight.ok ? "success" : "error"}`}>{preflightMessage(preflight, "参数预检通过")}</div>}
          {Object.keys(preview).length > 0 && <details className="xy-workflow-preview"><summary>派生预览（只读）</summary>
            <dl>{Object.entries(preview).map(([name, value]) => <div key={name}><dt>{fields.find(field => field.name === name)?.label ?? name}</dt><dd>{String(value)}</dd></div>)}</dl>
          </details>}
          {definition?.wiring_notes.map(note => <p key={note}>{note}</p>)}
          <JobView job={job} onUpdate={setJob} />
        </div>
      </WorkflowPanel>
      <section className="zaw-panel zaw-panel-result"><h2>最近一次结果</h2>
        {latest ? result(latest) : <p>暂无运行记录。</p>}
      </section>
    </div>
    <div id={`${experimentId}-panel-history`} role="tabpanel" aria-labelledby={`${experimentId}-tab-history`} hidden={tab !== "history"}>
      <div className="zaw-history">
        <section className="zaw-run-list"><h2>历史运行（{list.total}）</h2>
          <button onClick={() => setRefresh(current => current + 1)}>刷新记录</button>
          <ul>{list.runs.map(run => <li key={run.run_id}><button className={`zaw-run-card ${selectedId === run.run_id ? "selected" : ""}`}
            onClick={() => setSelectedId(run.run_id)}><strong>{run.run_id}</strong><span>{run.timestamp}</span><span>{status(run.completion_status)}</span></button></li>)}</ul>
          <button disabled={offset === 0} onClick={() => setOffset(current => current - PAGE_SIZE)}>上一页</button>
          <button disabled={offset + PAGE_SIZE >= list.total} onClick={() => setOffset(current => current + PAGE_SIZE)}>下一页</button>
        </section>
        <section className="zaw-run-details">{selected ? <>
          <div className="zaw-run-details-actions">
            <button disabled={jobActive || starting || live} onClick={() => {
              setValues(current => normalizeParameters({ ...current, ...historyParameterValues(fields, selected.parameters) })); setTab("run");
            }}>填回参数</button>
            {hasAnalysis && <button disabled={starting || jobActive || analysisActive || selected.completion_status === "running"}
              onClick={() => void reanalyze(selected.run_id)}>重新分析</button>}
          </div>
          {result(selected)}
          <details><summary>本次参数</summary><table><tbody>{Object.entries(selected.parameters).map(([name, value]) =>
            <tr key={name}><th>{fields.find(field => field.name === name)?.label ?? name}</th><td>{String(value)}</td></tr>)}</tbody></table></details>
        </> : <p>{selectedId ? "正在读取运行详情…" : "暂无运行记录。"}</p>}</section>
      </div>
    </div>
    <JobView job={analysisJob} onUpdate={setAnalysisJob} />
  </>;
}
