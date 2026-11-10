import { useEffect, useState } from "react";
import { ExperimentViewTabs, type ExperimentView } from "../components/ExperimentViewTabs";
import { api } from "../api";
import { PageHead } from "../components/PageHead";
import { recoverableJob, useExperimentTask, useRequestedJobId } from "../components/experimentNavigation";
import { useParameterGuard } from "../components/NavigationGuard";
import { JobView } from "../components/JobView";
import { useJobActivity } from "../components/JobActivity";
import { WorkflowPanel, type XYSession } from "../components/workflow/WorkflowPanel";
import { LatestResultPanel, RunControls, useRunControls } from "../components/workflow/RunControls";
import { RunHistoryView, runStatusLabel, useRunHistory } from "../components/workflow/RunHistory";
import { defaultParameterLayout, historyParameterValues, sameParameterLayout, validatedParameterLayout } from "./experimentHelpers";
import type { ExperimentDefinition, ExperimentSchema, Job, JobSummary, ParameterLayout, ParameterValues } from "../types/api";
import "./zawClosedLoop.css";

const ID = "bell-bloom-z-field-calibration";
const BASE = `/api/experiments/${ID}`;
const ANALYSIS_KEYS = { LINEAR_R_SQUARED_MIN: "LINEAR_R_SQUARED_MIN" };
const terminal = (job: Job | undefined) => Boolean(job && ["completed", "failed", "cancelled"].includes(job.status));
type Run = { run_id: string; timestamp: string; run_tag: string; completion_status: string };
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
const number = (value: number | null) => value === null ? "—" : value.toLocaleString("zh-CN", { maximumSignificantDigits: 7 });

function Result({ value }: { value: Summary }) {
  const analysis = value.analysis;
  return <div className="zaw-latest">
    <p>{value.run_id} · {value.timestamp}</p>
    <p>采集：{runStatusLabel(value.completion_status)} · 分析：{runStatusLabel(value.analysis_status)}</p>
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
      <p className="xy-artifact-links">{["analysis.yaml", "analysis.json", "calibration_results.npz"].filter(name => value.artifacts[name]).map(name => <a key={name} href={value.artifacts[name]} download>{name}</a>)}</p>
    </>}
  </div>;
}

export function BellBloomZCalibrationPage() {
  const [tab, setTab] = useState<ExperimentView>("run");
  const [definition, setDefinition] = useState<ExperimentDefinition>();
  const [fields, setFields] = useState<ExperimentSchema["fields"]>([]);
  const [values, setValues] = useState<ParameterValues>({});
  const [layout, setLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [savedLayout, setSavedLayout] = useState(layout);
  const [job, setJob] = useState<Job>();
  const [session, setSession] = useState<XYSession>();
  const [preview, setPreview] = useState<ParameterValues>({});
  const [analysisJob, setAnalysisJob] = useState<Job>();
  const [error, setError] = useState("");
  const { hardwareBusy } = useJobActivity();
  const jobActive = Boolean(job && !terminal(job));
  const analysisActive = Boolean(analysisJob && !terminal(analysisJob));
  const live = Boolean(session && session.status !== "closed");
  const controls = useRunControls({ experimentId: ID, values, layout, setError, setJob,
    onSaved: (schema, confirmed) => { setSavedLayout(confirmed); setFields(schema.fields); } });
  const { starting, saving, setStarting, setNotice } = controls;
  const parametersBusy = jobActive && (!live || session?.status !== "waiting" || Boolean(session?.pending_request));
  const history = useRunHistory<Run, Summary>({ experimentId: ID,
    reloadKeys: [job?.id, job?.status, analysisJob?.status, session?.run_id, session?.steps.A09?.status], setError });
  useParameterGuard(fields, values, layout, savedLayout, live ? session?.parameters as ParameterValues : undefined);
  useExperimentTask({ ready: fields.length > 0, setTab, setJob, setAnalysisJob, setSelectedId: history.setSelectedId });
  const requestedJobId = useRequestedJobId();

  useEffect(() => {
    let disposed = false;
    Promise.all([api<ExperimentDefinition>(BASE), api<ExperimentSchema>(`${BASE}/schema`), api<JobSummary[]>("/api/jobs")]).then(async ([def, schema, jobs]) => {
      if (disposed) return;
      setDefinition(def); setFields(schema.fields);
      setValues(Object.fromEntries(schema.fields.map(field => [field.name, field.default])));
      const initial = validatedParameterLayout(schema.fields, schema.parameter_layout) ?? defaultParameterLayout(schema.fields);
      setLayout(initial); setSavedLayout(initial);
      const active = recoverableJob(jobs, ID, requestedJobId);
      if (active) { const recovered = await api<Job>(`/api/jobs/${active.id}`); if (!disposed) setJob(recovered); }
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    if (!fields.length) return;
    let disposed = false;
    void api<{ values?: ParameterValues }>(`${BASE}/derive`, { method: "POST", body: JSON.stringify({ parameters: values }) })
      .then(data => { if (!disposed) setPreview(data.values ?? {}); })
      .catch(() => { if (!disposed) setPreview({}); });
    return () => { disposed = true; };
  }, [values, fields.length]);

  const reanalyze = async (runId: string) => {
    setStarting(true); setError("");
    try { setAnalysisJob(await api<Job>(`/api/runs/${encodeURIComponent(runId)}/analyze`, { method: "POST", body: JSON.stringify({ experiment_id: ID }) })); }
    catch (reason) { setError(String(reason)); } finally { setStarting(false); }
  };

  return <>
    <PageHead eyebrow="CALIBRATION · BELL BLOOM" title={definition?.title ?? "Bell Bloom Z 磁场频率标定"} description={definition?.description ?? "读取参数与运行记录。"} experiment={definition} />
    {error && <div className="alert error" role="alert">{error}</div>}
    <ExperimentViewTabs experimentId={ID} title={definition?.title} value={tab} onChange={setTab} idPrefix="bb-z" label="Bell Bloom Z 标定视图" />
    <div id="bb-z-panel-run" role="tabpanel" aria-labelledby="bb-z-tab-run" hidden={tab !== "run"}>
      <WorkflowPanel experimentId={ID} analysisParameterKeys={ANALYSIS_KEYS}
        enabled={fields.length > 0} fields={fields} layout={layout} setLayout={setLayout} setNotice={setNotice}
        parametersDisabled={parametersBusy || starting || saving} values={values} setValues={setValues}
        session={session} setSession={setSession} job={job} setJob={setJob}
        analysisJob={analysisJob} setAnalysisJob={setAnalysisJob} hardwareBusy={hardwareBusy}
        runs={history.list.runs} setError={setError} onRefresh={history.reload}>
        <RunControls controls={controls} canEdit={!parametersBusy && fields.length > 0}
          canStart={!jobActive && !hardwareBusy && !analysisActive && fields.length > 0}
          layoutDirty={!sameParameterLayout(layout, savedLayout)} hardwareBusyNote={hardwareBusy && !jobActive}
          job={job} onJobUpdate={setJob}
          preview={Object.keys(preview).length > 0 && <details><summary>派生预览（只读）</summary><dl>{Object.entries(preview).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{String(value)}</dd></div>)}</dl></details>} />
      </WorkflowPanel>
        <LatestResultPanel latest={history.latest} render={value => <Result value={value} />} onOpen={runId => { history.setSelectedId(runId); setTab("history"); }}
          note={jobActive && <p>实验正在运行；下方展示已保存的运行记录及其时间。</p>} />
    </div>
    <div id="bb-z-panel-history" role="tabpanel" aria-labelledby="bb-z-tab-history" hidden={tab !== "history"}>
      <RunHistoryView history={history} fields={fields} renderResult={value => <Result value={value} />}
        fillBack={{ disabled: jobActive || starting || live, onClick: selected => {
          setValues(current => ({ ...current, ...historyParameterValues(fields, selected.parameters) })); setTab("run");
        } }}
        reanalyze={{ disabled: selected => starting || jobActive || analysisActive || ["running", "acquired"].includes(selected.completion_status),
          onClick: selected => void reanalyze(selected.run_id) }} />
    </div>
    <JobView job={analysisJob} onUpdate={setAnalysisJob} />
  </>;
}
