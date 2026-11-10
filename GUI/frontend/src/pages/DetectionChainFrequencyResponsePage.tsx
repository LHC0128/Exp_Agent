import { historyParameterValues } from "./experimentHelpers";
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
import { defaultParameterLayout, sameParameterLayout, validatedParameterLayout } from "./experimentHelpers";
import type { ExperimentDefinition, ExperimentSchema, Job, JobSummary, ParameterLayout, ParameterValues } from "../types/api";
import "./zawClosedLoop.css";

const ID = "detection-chain-frequency-response";
const BASE = `/api/experiments/${ID}`;
const terminal = (job: Job | undefined) => Boolean(job && ["completed", "failed", "cancelled"].includes(job.status));
type Run = { run_id: string; timestamp: string; run_tag: string; completion_status: string };
type Summary = Run & {
  parameters: ParameterValues;
  analysis_status: string;
  failure_reason: string;
  analysis_error: string;
  temperature_control?: { control_source?: string; actual_temperature_c?: number | null } | null;
  artifacts: Record<string, string>;
};


function Result({ value }: { value: Summary }) {
  return <div className="zaw-latest">
    <p><strong>{value.run_id}</strong> · {value.timestamp}</p>
    <p>采集：{runStatusLabel(value.completion_status)} · 分析：{runStatusLabel(value.analysis_status)}</p>
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
    <p className="xy-artifact-links">
      {["analysis_results.npz"].filter(name => value.artifacts[name]).map(name =>
        <a key={name} href={value.artifacts[name]} download>{name}</a>
      )}
    </p>
  </div>;
}

export function DetectionChainFrequencyResponsePage() {
  const [tab, setTab] = useState<ExperimentView>("run");
  const [definition, setDefinition] = useState<ExperimentDefinition>();
  const [fields, setFields] = useState<ExperimentSchema["fields"]>([]);
  const [values, setValues] = useState<ParameterValues>({});
  const [layout, setLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [savedLayout, setSavedLayout] = useState(layout);
  const [job, setJob] = useState<Job>();
  const [analysisJob, setAnalysisJob] = useState<Job>();
  const [session, setSession] = useState<XYSession>();
  const [error, setError] = useState("");
  const { hardwareBusy } = useJobActivity();
  const jobActive = Boolean(job && !terminal(job));
  const analysisActive = Boolean(analysisJob && !terminal(analysisJob));
  const sessionActive = Boolean(session && session.status !== "closed");
  const controls = useRunControls({ experimentId: ID, values, layout, setError, setJob,
    onSaved: (schema, confirmed) => { setSavedLayout(confirmed); setFields(schema.fields); } });
  const { starting, setStarting, setNotice } = controls;
  const parametersBusy = jobActive && (!sessionActive || session?.status !== "waiting" || Boolean(session?.pending_request));
  const history = useRunHistory<Run, Summary>({ experimentId: ID, reloadKeys: [job?.status, analysisJob?.status], setError });
  useParameterGuard(fields, values, layout, savedLayout, sessionActive ? session?.parameters as ParameterValues : undefined);
  useExperimentTask({ ready: fields.length > 0, setTab, setJob, setAnalysisJob, setSelectedId: history.setSelectedId });
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
      setValues(Object.fromEntries(schema.fields.map(field => [field.name, field.default])));
      const initial = validatedParameterLayout(schema.fields, schema.parameter_layout) ?? defaultParameterLayout(schema.fields);
      setLayout(initial);
      setSavedLayout(initial);
      const active = recoverableJob(jobs, ID, requestedJobId);
      if (active) {
        const recovered = await api<Job>(`/api/jobs/${active.id}`);
        if (!disposed) setJob(recovered);
      }
    }).catch(reason => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, []);

  const reanalyze = async (runId: string) => {
    setError("");
    setStarting(true);
    try {
      setAnalysisJob(await api<Job>(`/api/runs/${encodeURIComponent(runId)}/analyze`, {
        method: "POST", body: JSON.stringify({ experiment_id: ID })
      }));
    } catch (reason) { setError(String(reason)); } finally { setStarting(false); }
  };


  return <>
    <PageHead eyebrow="CALIBRATION · FREQUENCY" title={definition?.title ?? "探测链路频率响应标定"} description={definition?.description ?? "读取参数与运行记录。"} experiment={definition} />
    {error && <div className="alert error" role="alert">{error}</div>}
    <ExperimentViewTabs experimentId={ID} title={definition?.title} value={tab} onChange={setTab} idPrefix="dc" label="探测链路频率响应标定视图" />
    <div id="dc-panel-run" role="tabpanel" aria-labelledby="dc-tab-run" hidden={tab !== "run"}>
      <WorkflowPanel enabled={fields.length > 0} fields={fields} layout={layout} setLayout={setLayout}
        setNotice={setNotice} parametersDisabled={parametersBusy || starting} values={values} setValues={setValues}
        session={session} setSession={setSession} job={job} setJob={setJob}
        analysisJob={analysisJob} setAnalysisJob={setAnalysisJob} hardwareBusy={hardwareBusy}
        runs={history.list.runs} setError={setError} onRefresh={history.reload}
        experimentId={ID}>
        <RunControls controls={controls} canEdit={!parametersBusy && fields.length > 0}
          canStart={!jobActive && !hardwareBusy && !analysisActive && fields.length > 0}
          layoutDirty={!sameParameterLayout(layout, savedLayout)} hardwareBusyNote={hardwareBusy && !jobActive}
          job={job} onJobUpdate={setJob} />
      </WorkflowPanel>
      <LatestResultPanel latest={history.latest} render={value => <Result value={value} />} onOpen={runId => { history.setSelectedId(runId); setTab("history"); }} />
    </div>
    <div id="dc-panel-history" role="tabpanel" aria-labelledby="dc-tab-history" hidden={tab !== "history"}>
      <RunHistoryView history={history} fields={fields} renderResult={value => <Result value={value} />}
        fillBack={{ disabled: jobActive || starting, onClick: selected => {
          setValues(current => ({ ...current, ...historyParameterValues(fields, selected.parameters as ParameterValues) })); setTab("run");
        } }}
        reanalyze={{ disabled: selected => jobActive || analysisActive || starting || selected.completion_status === "running",
          onClick: selected => void reanalyze(selected.run_id) }} />
    </div>
    <JobView job={analysisJob} onUpdate={setAnalysisJob} />
  </>;
}
