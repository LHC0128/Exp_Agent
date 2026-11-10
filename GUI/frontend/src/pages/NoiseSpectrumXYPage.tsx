import { historyParameterValues } from "./experimentHelpers";
import { useEffect, useState } from "react";
import { ExperimentViewTabs, type ExperimentView } from "../components/ExperimentViewTabs";

import { api } from "../api";
import { useJobActivity } from "../components/JobActivity";
import { JobView } from "../components/JobView";
import { WorkflowPanel, type XYSession } from "../components/workflow/WorkflowPanel";
import { LatestResultPanel, RunControls, useRunControls } from "../components/workflow/RunControls";
import { PageHead } from "../components/PageHead";
import { recoverableJob, useExperimentTask, useRequestedJobId } from "../components/experimentNavigation";
import { useParameterGuard } from "../components/NavigationGuard";
import type {
  ExperimentDefinition,
  ExperimentSchema,
  Job,
  JobSummary,
  ParameterLayout,
  ParameterValues,
} from "../types/api";
import {
  defaultParameterLayout,
  sameParameterLayout,
  validatedParameterLayout,
} from "./experimentHelpers";
import "./zawClosedLoop.css";

const ID = "noise-spectrum-xy";
const BASE = `/api/experiments/${ID}`;
const PAGE_SIZE = 50;
const terminal = (job: Job | undefined) => Boolean(
  job && ["completed", "failed", "cancelled"].includes(job.status),
);

type RunItem = {
  run_id: string;
  timestamp: string;
  run_tag: string;
  completion_status: string;
};
type RunList = { runs: RunItem[]; total: number };
type RunSummary = RunItem & {
  analysis_status: string;
  analysis_error: string;
  parameters: ParameterValues;
  artifacts: Record<string, string>;
  analysis_quality?: {
    psd?: { actual_rate_sa_s: number; nperseg: number; bin_width_hz: number; segments_per_point_range?: number[] | null };
    calibration?: { k: number; b: number; support_points: number; residual_std_hz: number };
    fit?: { valid_points: number; background_valid_points?: number; total_points: number; valid_frequency_extent_hz: number[] | null };
    warnings?: string[];
  };
};
type WelchPreview = {
  error?: string;
  nperseg?: number;
  bin_width_hz?: number;
  segments_per_point?: number;
  scan_seconds?: number;
};

const statusLabels: Record<string, string> = {
  completed: "完成",
  failed: "失败",
  cancelled: "已取消",
  running: "运行中",
  not_started: "未分析",
  quality_failed: "质量验收未通过",
};
const figureLabels: Record<string, string> = {
  "calibration.png": "PSD 脊线与正弦峰值标定",
  "noise_spectrum_2d.png": "二维噪声谱（未标定时纵轴为扫描电压）",
  "noise_spectrum_lines.png": "典型控制点噪声谱",
  "noise_spectra_extracted.png": "提取的可控与不可控噪声谱",
  "diagonal_analysis.png": "PSD 斜线特征诊断",
  "psd_column_diagnostics.png": "PSD 逐列拟合诊断",
  "fit_quality.png": "拟合残差、留出误差与线宽",
  "background_identifiability.png": "不可控背景的模型拟合与远端平台来源",
};
const downloadable = [
  "noise_spectra.npz",
  "noise_spectra.csv",
  "calibration.npz",
  "popt_fit.npz",
  "psd_matrix.npz",
  "analysis_summary.json",
  "fit_diagnostics.npz",
  "global_comparison.npz",
];
const summaryUrl = (runId: string) => `${BASE}/runs/${encodeURIComponent(runId)}/summary`;
const status = (value: string) => statusLabels[value] ?? value;

function Result({ value }: { value: RunSummary }) {
  const figures = Object.entries(figureLabels).filter(([name]) => value.artifacts[name]);
  const files = downloadable.filter((name) => value.artifacts[name]);
  return (
    <div className="zaw-latest">
      <p>{value.run_id} · {value.timestamp}</p>
      <p>采集：{status(value.completion_status)} · 分析：{status(value.analysis_status)}</p>
      {value.analysis_error && <div className="alert error">{value.analysis_error}</div>}
      {value.analysis_quality?.psd && <p>
        实际 Welch 每段点数：{value.analysis_quality.psd.nperseg}；
        频率格间距：{value.analysis_quality.psd.bin_width_hz.toFixed(3)} Hz；
        实际采样率：{value.analysis_quality.psd.actual_rate_sa_s.toFixed(3)} Sa/s；
        每点平均段数：{value.analysis_quality.psd.segments_per_point_range?.join("–") ?? "未记录"}。
      </p>}
      {value.analysis_quality?.calibration && <p>
        移动峰标定 K = {value.analysis_quality.calibration.k.toFixed(1)} Hz/V；
        支持 {value.analysis_quality.calibration.support_points} 个控制点；
        残差标准差 {value.analysis_quality.calibration.residual_std_hz.toFixed(1)} Hz。
      </p>}
      {value.analysis_quality?.fit && <p>
        通过质量验收 {value.analysis_quality.fit.valid_points} / {value.analysis_quality.fit.total_points} 个频率点；
        {value.analysis_quality.fit.background_valid_points !== undefined && <>不可控背景独立有效 {value.analysis_quality.fit.background_valid_points} 点；</>}
        无效点保留为空，不插值或外推。
      </p>}
      {value.analysis_quality?.warnings?.map((warning) => <p key={warning}>{warning}</p>)}
      {!figures.length && <p className="zaw-empty">本次运行暂无结果图。</p>}
      {figures.map(([name, label], index) => (
        <figure className="zaw-figure" key={name}>
          <figcaption>{label}</figcaption>
          <img src={value.artifacts[name]} alt={label} loading={index ? "lazy" : "eager"} />
        </figure>
      ))}
      {files.length > 0 && (
        <p className="xy-artifact-links">{files.map((name) => <a key={name} href={value.artifacts[name]} download>{name}</a>)}</p>
      )}
    </div>
  );
}

export function NoiseSpectrumXYPage() {
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
  const [welchPreview, setWelchPreview] = useState<WelchPreview>();
  const [latest, setLatest] = useState<RunSummary>();
  const [list, setList] = useState<RunList>({ runs: [], total: 0 });
  const [selectedId, setSelectedId] = useState("");
  const [selected, setSelected] = useState<RunSummary>();
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const { hardwareBusy } = useJobActivity();
  const jobActive = Boolean(job && !terminal(job));
  const analysisActive = Boolean(analysisJob && !terminal(analysisJob));
  const sessionActive = Boolean(session && session.status !== "closed");
  const controls = useRunControls({ experimentId: ID, values, layout, setError, setJob,
    onSaved: (schema, confirmed) => { setSavedLayout(confirmed); setFields(schema.fields); } });
  const { starting, setStarting, setNotice } = controls;
  const parametersBusy = jobActive && (!sessionActive || session?.status !== "waiting" || Boolean(session?.pending_request));
  useParameterGuard(fields, values, layout, savedLayout, sessionActive ? session?.parameters as ParameterValues : undefined);
  useExperimentTask({ ready: fields.length > 0, setTab, setJob, setAnalysisJob, setSelectedId });
  const requestedJobId = useRequestedJobId();

  useEffect(() => {
    if (!fields.length) return;
    let disposed = false;
    setWelchPreview(undefined);
    const timer = window.setTimeout(() => {
      void api<{ values: WelchPreview }>(`${BASE}/derive`, {
        method: "POST", body: JSON.stringify({ parameters: values }),
      }).then((result) => {
        if (!disposed) setWelchPreview(result.values);
      }).catch((reason) => {
        if (!disposed) setWelchPreview({ error: `无法计算分段预览：${String(reason)}` });
      });
    }, 200);
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [fields, values]);

  useEffect(() => {
    let disposed = false;
    void Promise.all([
      api<ExperimentDefinition>(BASE),
      api<ExperimentSchema>(`${BASE}/schema`),
      api<JobSummary[]>("/api/jobs"),
    ]).then(async ([def, schema, jobs]) => {
      if (disposed) return;
      setDefinition(def);
      setFields(schema.fields);
      setValues(Object.fromEntries(schema.fields.map((field) => [field.name, field.default])));
      const persisted = schema.parameter_layout_saved
        ? validatedParameterLayout(schema.fields, schema.parameter_layout)
        : undefined;
      const initial = persisted ?? defaultParameterLayout(schema.fields);
      setLayout(initial);
      setSavedLayout(initial);
      const recoverable = recoverableJob(jobs, ID, requestedJobId);
      if (recoverable) {
        const recovered = await api<Job>(`/api/jobs/${recoverable.id}`);
        if (!disposed) setJob(recovered);
      }
    }).catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    let disposed = false;
    void api<RunList>(`${BASE}/runs?limit=${PAGE_SIZE}&offset=${offset}`).then((data) => {
      if (disposed) return;
      setList(data);
      setSelectedId((current) => current || data.runs[0]?.run_id || "");
    }).catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [offset, refresh, job?.status, analysisJob?.status]);

  useEffect(() => {
    let disposed = false;
    void api<RunList>(`${BASE}/runs?limit=1&offset=0`).then(async (data) => {
      const latestRun = data.runs[0];
      const summary = latestRun ? await api<RunSummary>(summaryUrl(latestRun.run_id)) : undefined;
      if (!disposed) setLatest(summary);
    }).catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [refresh, job?.status, analysisJob?.status]);

  useEffect(() => {
    if (!selectedId) return;
    let disposed = false;
    setSelected(undefined);
    void api<RunSummary>(summaryUrl(selectedId))
      .then((data) => { if (!disposed) setSelected(data); })
      .catch((reason) => { if (!disposed) setError(String(reason)); });
    return () => { disposed = true; };
  }, [selectedId, refresh, job?.status, analysisJob?.status]);


  const reanalyze = async (runId: string) => {
    setStarting(true);
    setError("");
    try {
      setAnalysisJob(await api<Job>(`/api/runs/${encodeURIComponent(runId)}/analyze`, {
        method: "POST",
        body: JSON.stringify({ experiment_id: ID }),
      }));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setStarting(false);
    }
  };

  const fillBack = (parameters: ParameterValues) => {
    const compatible = historyParameterValues(fields, parameters);
    setValues((current) => ({ ...current, ...compatible }));
    if (Object.hasOwn(parameters, "HF2_NPERSEG")) {
      setNotice("旧 Welch 每段点数不再填回；新采集仅按表单中的频率格间距自动分段。历史重新分析保留旧规则。");
    }
    setTab("run");
  };


  return (
    <>
      <PageHead
        eyebrow="MEASUREMENT · XY SINE"
        title={definition?.title ?? "XY 控制测量噪声谱（正弦）"}
        description={definition?.description ?? "读取实验定义与运行记录。"}
        experiment={definition}
      />
      {error && <div className="alert error" role="alert">{error}</div>}
      <ExperimentViewTabs experimentId={ID} title={definition?.title} value={tab} onChange={setTab} idPrefix="noise-xy" label="XY 正弦控制噪声谱实验视图" />

      <div id="noise-xy-panel-run" role="tabpanel" aria-labelledby="noise-xy-tab-run" hidden={tab !== "run"}>
        <WorkflowPanel enabled={fields.length > 0} fields={fields} layout={layout} setLayout={setLayout}
          setNotice={setNotice} parametersDisabled={parametersBusy || starting} values={values} setValues={setValues}
          session={session} setSession={setSession} job={job} setJob={setJob}
          analysisJob={analysisJob} setAnalysisJob={setAnalysisJob} hardwareBusy={hardwareBusy}
          runs={list.runs} setError={setError} onRefresh={setRefresh}>
          <RunControls controls={controls} canEdit={!parametersBusy && fields.length > 0}
            canStart={!jobActive && !hardwareBusy && !analysisActive && fields.length > 0}
            layoutDirty={!sameParameterLayout(layout, savedLayout)} hardwareBusyNote={hardwareBusy && !jobActive}
            job={job} onJobUpdate={setJob}
            preview={<div className="xy-workflow-preview" aria-label="Welch 分段预览" aria-live="polite">
                <h3>Welch 分段预览（只读）</h3>
                {welchPreview?.error ? <p className="alert error">{welchPreview.error}</p> : <>
                  <p>预计每段点数：{welchPreview?.nperseg ?? "—"}；
                    每点平均段数：{welchPreview?.segments_per_point ?? "—"}（50% 重叠，非独立段数）。</p>
                  <p>预计频率格间距：{welchPreview?.bin_width_hz?.toFixed(3) ?? "—"} Hz；
                    扫描采集与等待约 {welchPreview?.scan_seconds?.toFixed(1) ?? "—"} 秒。</p>
                </>}
                <p>预览基于请求采样率；实际值以分析结果为准。耗时含每点稳定及恢复温控等待，不含初始化、校相、通信和分析。</p>
              </div>} />
        </WorkflowPanel>
        <LatestResultPanel latest={latest} render={value => <Result value={value} />} onOpen={runId => { setSelectedId(runId); setTab("history"); }} />
      </div>

      <div id="noise-xy-panel-history" role="tabpanel" aria-labelledby="noise-xy-tab-history" hidden={tab !== "history"}>
        <div className="zaw-history">
          <section className="zaw-run-list">
            <h2>历史运行（{list.total}）</h2>
            <button onClick={() => setRefresh((value) => value + 1)}>刷新记录</button>
            <ul>{list.runs.map((run) => (
              <li key={run.run_id}><button className={`zaw-run-card ${selectedId === run.run_id ? "selected" : ""}`} onClick={() => setSelectedId(run.run_id)}><strong>{run.run_id}</strong><span>{run.timestamp}</span><span>{status(run.completion_status)}</span></button></li>
            ))}</ul>
            <button disabled={offset === 0} onClick={() => setOffset((value) => value - PAGE_SIZE)}>上一页</button>
            <button disabled={offset + PAGE_SIZE >= list.total} onClick={() => setOffset((value) => value + PAGE_SIZE)}>下一页</button>
          </section>
          <section className="zaw-run-details">
            {selected ? <>
              <div className="zaw-run-details-actions">
                <button disabled={jobActive || starting} onClick={() => fillBack(selected.parameters)}>填回参数</button>
                <button disabled={starting || jobActive || analysisActive || selected.completion_status === "running"} onClick={() => void reanalyze(selected.run_id)}>重新分析</button>
              </div>
              <Result value={selected} />
              <details><summary>本次参数</summary><table><tbody>{Object.entries(selected.parameters).map(([key, value]) => <tr key={key}><th>{fields.find((field) => field.name === key)?.label ?? key}</th><td>{String(value)}</td></tr>)}</tbody></table></details>
            </> : <p>{selectedId ? "正在读取运行详情…" : "暂无运行记录。"}</p>}
          </section>
        </div>
      </div>
      <JobView job={analysisJob} onUpdate={setAnalysisJob} />
    </>
  );
}
