import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { PageHead } from "../components/PageHead";
import { recoverableJob, useExperimentTask, useRequestedJobId } from "../components/experimentNavigation";
import { useUnsavedChanges } from "../components/NavigationGuard";
import { ExperimentViewPanel, ExperimentViewTabs, type ExperimentView } from "../components/ExperimentViewTabs";
import { StaticSensitivityDashboard } from "../components/StaticSensitivityDashboard";
import { StaticSensitivityHistory } from "../components/StaticSensitivityHistory";
import type { ExperimentDefinition, ExperimentSchema, Job, JobSummary, ParameterLayout, ParameterValues } from "../types/api";
import { defaultParameterLayout, sameParameterLayout, validatedParameterLayout } from "./experimentHelpers";
import "./zawClosedLoop.css";

const ID = "static-sensitivity";

export function StaticSensitivityPage() {
  const [tab, setTab] = useState<ExperimentView>("run");
  const [definition, setDefinition] = useState<ExperimentDefinition>();
  const [fields, setFields] = useState<ExperimentSchema["fields"]>([]);
  const [layout, setLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [savedLayout, setSavedLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [values, setValues] = useState<ParameterValues>({});
  const [job, setJob] = useState<Job>();
  const [prefill, setPrefill] = useState<ParameterValues | null>(null);
  const [loadError, setLoadError] = useState("");
  const requestedJobId = useRequestedJobId();
  useEffect(() => { let disposed = false; Promise.all([api<ExperimentDefinition>(`/api/experiments/${ID}`), api<ExperimentSchema>(`/api/experiments/${ID}/schema`)]).then(([def, schema]) => { if (disposed) return; const persisted = schema.parameter_layout_saved ? validatedParameterLayout(schema.fields, schema.parameter_layout) : undefined; const initial = persisted ?? defaultParameterLayout(schema.fields); setDefinition(def); setFields(schema.fields); setLayout(initial); setSavedLayout(initial); setValues(Object.fromEntries(schema.fields.map((field) => [field.name, field.default])) as ParameterValues); }).catch((reason) => { if (!disposed) setLoadError(String(reason)); }); return () => { disposed = true; }; }, []);
  useEffect(() => { let disposed = false; api<JobSummary[]>("/api/jobs").then(async (items) => { const match = recoverableJob(items, ID, requestedJobId); if (disposed || !match) return; try { const detail = await api<Job>(`/api/jobs/${match.id}`); if (!disposed) setJob(detail); } catch { /* 任务详情不可用时保持空状态。 */ } }).catch(() => undefined); return () => { disposed = true; }; }, []);
  const description = useMemo(() => definition?.description ?? "读取实验定义与默认参数。", [definition]);
  const layoutDirty = fields.length > 0 && !sameParameterLayout(layout, savedLayout);
  // 常驻运行页按活会话保护参数；页面层只补充尚未保存的布局。
  useUnsavedChanges(layoutDirty);
  useExperimentTask({ ready: fields.length > 0, setTab });
  return <>
    <PageHead eyebrow="EXPERIMENT · STATIC_SENSITIVITY" title={definition?.title ?? "正在载入实验"} description={description} experiment={definition} />
    {loadError && <div className="alert error">实验参数加载失败：{loadError}</div>}
    <ExperimentViewTabs experimentId={ID} title={definition?.title} value={tab} onChange={setTab} label="静磁场灵敏度实验视图" />
    {definition && fields.length > 0 && <>
      <div id={`${ID}-panel-run`} role="tabpanel" aria-labelledby={`${ID}-tab-run`} hidden={tab !== "run"}>
        <StaticSensitivityDashboard experimentId={ID} definition={definition} fields={fields} layout={layout} layoutDirty={layoutDirty} onLayoutChange={setLayout} onLayoutSaved={(next, savedFields) => { setLayout(next); setSavedLayout(next); setFields(savedFields); }} values={values} setValues={setValues} job={job} setJob={setJob} prefill={prefill} clearPrefill={() => setPrefill(null)} />
      </div>
      <ExperimentViewPanel active={tab === "history"} id={`${ID}-panel-history`} labelledBy={`${ID}-tab-history`}>
        <StaticSensitivityHistory experimentId={ID} fields={fields} onFillBack={(parameters) => setPrefill(parameters as ParameterValues)} onJumpToRun={() => setTab("run")} />
      </ExperimentViewPanel>
    </>}
  </>;
}
