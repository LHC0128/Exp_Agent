import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { PageHead } from "../components/PageHead";
import { StaticSensitivityDashboard } from "../components/StaticSensitivityDashboard";
import { StaticSensitivityHistory } from "../components/StaticSensitivityHistory";
import type { ExperimentDefinition, ExperimentSchema, Job, JobSummary, ParameterLayout, ParameterValues } from "../types/api";
import { defaultParameterLayout, sameParameterLayout, validatedParameterLayout } from "./experimentHelpers";
import "./zawClosedLoop.css";

type Tab = "run" | "history" | "architecture";
const ID = "static-sensitivity";

export function StaticSensitivityPage() {
  const [tab, setTab] = useState<Tab>("run");
  const [definition, setDefinition] = useState<ExperimentDefinition>();
  const [fields, setFields] = useState<ExperimentSchema["fields"]>([]);
  const [layout, setLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [savedLayout, setSavedLayout] = useState<ParameterLayout>({ basic: [], advanced: [] });
  const [values, setValues] = useState<ParameterValues>({});
  const [job, setJob] = useState<Job>();
  const [prefill, setPrefill] = useState<ParameterValues | null>(null);
  const [loadError, setLoadError] = useState("");
  useEffect(() => { let disposed = false; Promise.all([api<ExperimentDefinition>(`/api/experiments/${ID}`), api<ExperimentSchema>(`/api/experiments/${ID}/schema`)]).then(([def, schema]) => { if (disposed) return; const persisted = schema.parameter_layout_saved ? validatedParameterLayout(schema.fields, schema.parameter_layout) : undefined; const initial = persisted ?? defaultParameterLayout(schema.fields); setDefinition(def); setFields(schema.fields); setLayout(initial); setSavedLayout(initial); setValues(Object.fromEntries(schema.fields.map((field) => [field.name, field.default])) as ParameterValues); }).catch((reason) => { if (!disposed) setLoadError(String(reason)); }); return () => { disposed = true; }; }, []);
  useEffect(() => { let disposed = false; api<JobSummary[]>("/api/jobs").then(async (items) => { const match = items.find((item) => item.kind === `experiment:${ID}` && ["queued", "running"].includes(item.status)) ?? items.find((item) => item.kind === `experiment:${ID}` && item.started_at); if (disposed || !match) return; try { const detail = await api<Job>(`/api/jobs/${match.id}`); if (!disposed) setJob(detail); } catch { /* 任务详情不可用时保持空状态。 */ } }).catch(() => undefined); return () => { disposed = true; }; }, []);
  const description = useMemo(() => definition?.description ?? "读取实验定义与默认参数。", [definition]);
  const layoutDirty = fields.length > 0 && !sameParameterLayout(layout, savedLayout);
  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => { if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return; const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')); const index = tabs.findIndex((item) => item === document.activeElement); if (index < 0) return; const next = (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length; tabs[next].focus(); setTab(tabs[next].dataset.tab as Tab); };
  return <><PageHead eyebrow="EXPERIMENT · STATIC_SENSITIVITY" title={definition?.title ?? "正在载入实验"} description={description} />{loadError && <div className="alert error">实验参数加载失败：{loadError}</div>}{definition && fields.length > 0 && <><div className="zaw-tab-strip" role="tablist" aria-label="静磁场灵敏度实验视图" onKeyDown={handleKeyDown}>{(["run", "history", "architecture"] as Tab[]).map((item) => <button key={item} role="tab" data-tab={item} aria-selected={tab === item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>{item === "run" ? "运行" : item === "history" ? "历史" : "架构"}</button>)}</div>{tab === "run" && <StaticSensitivityDashboard experimentId={ID} definition={definition} fields={fields} layout={layout} layoutDirty={layoutDirty} onLayoutChange={setLayout} onLayoutSaved={(next) => { setLayout(next); setSavedLayout(next); }} values={values} setValues={setValues} job={job} setJob={setJob} prefill={prefill} clearPrefill={() => setPrefill(null)} />}{tab === "history" && <StaticSensitivityHistory experimentId={ID} fields={fields} onFillBack={(parameters) => setPrefill(parameters as ParameterValues)} onJumpToRun={() => setTab("run")} />}{tab === "architecture" && <ArchitectureView />}</>}</>;
}

function ArchitectureView() { const url = "/architecture/static-sensitivity.html"; return <section className="mx-arch"><div className="mx-arch-head"><div><small>EXPERIMENT ARCHITECTURE</small><h2>采集流程架构</h2><p>展示相位校准、Pump 门控、Z RAMP 磁共振曲线、噪声采集和离线分析链路。</p></div><a className="architecture-open" href={url} target="_blank" rel="noreferrer">在新页面打开</a></div><div className="architecture-frame"><iframe src={url} title="静磁场灵敏度实验采集流程架构图" loading="lazy" sandbox="allow-scripts allow-same-origin allow-downloads allow-popups" /></div></section>; }
