import { useEffect, useState, type ReactNode } from "react";
import { ExperimentViewTabs, type ExperimentView } from "../components/ExperimentViewTabs";

import { api } from "../api";
import { useJobActivity } from "../components/JobActivity";
import { JobView } from "../components/JobView";
import { WorkflowPanel, type XYSession } from "../components/workflow/WorkflowPanel";
import { LatestResultPanel, RunControls, useRunControls } from "../components/workflow/RunControls";
import { RunHistoryView, runStatusLabel, useRunHistory } from "../components/workflow/RunHistory";
import { PageHead } from "../components/PageHead";
import { recoverableJob, useExperimentTask, useRequestedJobId } from "../components/experimentNavigation";
import { useParameterGuard } from "../components/NavigationGuard";
import type {
  ExperimentDefinition, ExperimentSchema, Job, JobSummary, ParameterLayout, ParameterValues,
} from "../types/api";
import type { components } from "../types/openapi";
import {
  defaultParameterLayout, historyParameterValues, sameParameterLayout, validatedParameterLayout,
} from "./experimentHelpers";
import "./zawClosedLoop.css";

export type WorkflowRunItem = components["schemas"]["WorkflowRunItem"];
export type WorkflowRunSummary = Omit<components["schemas"]["WorkflowRunSummary"], "parameters"> & {
  parameters: ParameterValues;
};
type Props = {
  experimentId: string;
  analysisParameterPrefix?: string; analysisParameterKeys?: Record<string, string>;
  normalizeParameters?: (values: ParameterValues) => ParameterValues;
  renderResult?: (summary: WorkflowRunSummary) => ReactNode;
  hasAnalysis?: boolean;
};
const identity = (values: ParameterValues) => values;
const active = (job?: Job) => Boolean(job && ["queued", "running"].includes(job.status));

function DefaultResult({ summary, hasAnalysis }: { summary: WorkflowRunSummary; hasAnalysis: boolean }) {
  const artifacts = summary.artifacts ?? {};
  return <div className="zaw-latest">
    <p>{summary.run_id} · {summary.timestamp} · 采集：{runStatusLabel(summary.completion_status)}{hasAnalysis && <> · 分析：{runStatusLabel(summary.analysis_status)}</>}</p>
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
  const [tab, setTab] = useState<ExperimentView>("run");
  const [definition, setDefinition] = useState<ExperimentDefinition>();
  const [fields, setFields] = useState<ExperimentSchema["fields"]>([]);
  const [values, setValues] = useState<ParameterValues>({});
  const [layout, setLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [savedLayout, setSavedLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [job, setJob] = useState<Job>();
  const [analysisJob, setAnalysisJob] = useState<Job>();
  const [session, setSession] = useState<XYSession>();
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<ParameterValues>({});
  const { hardwareBusy } = useJobActivity();
  const controls = useRunControls({ experimentId, values, layout, setError, setJob,
    onSaved: (schema, confirmed) => { setSavedLayout(confirmed); setFields(schema.fields); } });
  const { starting, saving, setStarting, setNotice } = controls;
  const jobActive = active(job);
  const analysisActive = active(analysisJob);
  const live = Boolean(session && session.status !== "closed");
  const parametersBusy = jobActive && (!live || session?.status !== "waiting" || Boolean(session?.pending_request));
  const history = useRunHistory<WorkflowRunItem, WorkflowRunSummary>({ experimentId,
    reloadKeys: [job?.status, analysisJob?.status], setError });
  useParameterGuard(fields, values, layout, savedLayout, live ? session?.parameters as ParameterValues : undefined);
  useExperimentTask({ ready: fields.length > 0, setTab, setJob, setAnalysisJob, setSelectedId: history.setSelectedId });
  const requestedJobId = useRequestedJobId();
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

  const reanalyze = async (id: string) => {
    setStarting(true); setError("");
    try {
      setAnalysisJob(await api<Job>(`/api/runs/${encodeURIComponent(id)}/analyze`, {
        method: "POST", body: JSON.stringify({ experiment_id: experimentId }),
      }));
    } catch (reason) { setError(String(reason)); }
    finally { setStarting(false); }
  };

  return <>
    <PageHead eyebrow="EXPERIMENT" title={definition?.title ?? "正在载入实验"} description={definition?.description ?? "读取实验定义与运行记录。"} experiment={definition} />
    {error && <div className="alert error" role="alert">{error}</div>}
    <ExperimentViewTabs experimentId={experimentId} title={definition?.title} value={tab} onChange={setTab} />
    <div id={`${experimentId}-panel-run`} role="tabpanel" aria-labelledby={`${experimentId}-tab-run`} hidden={tab !== "run"}>
      <WorkflowPanel experimentId={experimentId}
        analysisParameterPrefix={analysisParameterPrefix} analysisParameterKeys={analysisParameterKeys}
        normalizeParameters={normalizeParameters} enabled={fields.length > 0} fields={fields}
        layout={layout} setLayout={setLayout} setNotice={setNotice} parametersDisabled={parametersBusy || starting || saving}
        values={values} setValues={setValues} session={session} setSession={setSession} job={job} setJob={setJob}
        analysisJob={analysisJob} setAnalysisJob={setAnalysisJob} hardwareBusy={hardwareBusy}
        runs={history.list.runs} setError={setError} onRefresh={history.reload}>
        <RunControls controls={controls} canEdit={!parametersBusy && fields.length > 0}
          canStart={!jobActive && !hardwareBusy && !analysisActive && fields.length > 0}
          layoutDirty={!sameParameterLayout(layout, savedLayout)} hardwareBusyNote={hardwareBusy && !jobActive}
          job={job} onJobUpdate={setJob}
          preview={Object.keys(preview).length > 0 && <details className="xy-workflow-preview"><summary>派生预览（只读）</summary>
            <dl>{Object.entries(preview).map(([name, value]) => <div key={name}><dt>{fields.find(field => field.name === name)?.label ?? name}</dt><dd>{String(value)}</dd></div>)}</dl>
          </details>} />
      </WorkflowPanel>
      <LatestResultPanel latest={history.latest} render={result} onOpen={runId => { history.setSelectedId(runId); setTab("history"); }} />
    </div>
    <div id={`${experimentId}-panel-history`} role="tabpanel" aria-labelledby={`${experimentId}-tab-history`} hidden={tab !== "history"}>
      <RunHistoryView history={history} fields={fields} renderResult={result}
        fillBack={{ disabled: jobActive || starting || live, onClick: selected => {
          setValues(current => normalizeParameters({ ...current, ...historyParameterValues(fields, selected.parameters) })); setTab("run");
        } }}
        reanalyze={hasAnalysis ? { disabled: selected => starting || jobActive || analysisActive || selected.completion_status === "running",
          onClick: selected => void reanalyze(selected.run_id) } : undefined} />
    </div>
    <JobView job={analysisJob} onUpdate={setAnalysisJob} />
  </>;
}
