import { useEffect, useState } from "react";
import { api } from "../../api";
import { useJobActivity } from "../JobActivity";
import { JobView } from "../JobView";
import type {
  ExperimentDefinition,
  Job,
  ParameterValues,
  SchemaField,
} from "../../types/api";
import { ParameterForm } from "./ParameterForm";
import { MetricsCards } from "./MetricsCards";
import type { ZAWClosedLoopRunSummary } from "./types";
import { CONVERGENCE_PNG, WAVEFORM_COMPARISON_PNG } from "./images";

type DashboardProps = {
  experimentId: string;
  definition: ExperimentDefinition;
  fields: SchemaField[];
  values: ParameterValues;
  setValues: (next: ParameterValues) => void;
  prefill: ParameterValues | null;
  clearPrefill: () => void;
  onLatestRun: (summary: ZAWClosedLoopRunSummary) => void;
};

export function Dashboard({
  experimentId,
  definition,
  fields,
  values,
  setValues,
  prefill,
  clearPrefill,
  onLatestRun,
}: DashboardProps) {
  const { hardwareBusy, activeJobs } = useJobActivity();
  const [starting, setStarting] = useState(false);
  const [savingDefaults, setSavingDefaults] = useState(false);
  const [job, setJob] = useState<Job | undefined>();
  const [preflight, setPreflight] = useState<{ ok: boolean; errors: string[] } | undefined>();
  const [defaultsStatus, setDefaultsStatus] = useState<{ ok: boolean; message: string } | undefined>();
  const [error, setError] = useState("");

  const blockedByOthers = activeJobs.some(
    (item) => item.kind === `experiment:${experimentId}` && ["queued", "running"].includes(item.status),
  ) || hardwareBusy;
  const jobActive = job ? ["queued", "running"].includes(job.status) : false;
  const disabled = starting || jobActive || blockedByOthers || !fields.length;

  // 「填回参数」信号：来自历史 Tab，用户切回运行 Tab 时若有待应用参数，写回并清理。
  useEffect(() => {
    if (prefill) {
      setValues({ ...values, ...prefill });
      clearPrefill();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

  // CONTROL_VERSION / CONTROL_SOURCE_SET 变化时拉一次派生值，仅刷新只读字段。
  useEffect(() => {
    api<{ ok: boolean; values?: Record<string, unknown> }>(`/api/experiments/${experimentId}/derive`, {
      method: "POST",
      body: JSON.stringify({ parameters: values }),
    })
      .then((result) => {
        if (!result.values) return;
        setValues({ ...values, ...(result.values as ParameterValues) });
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values.CONTROL_VERSION, values.CONTROL_SOURCE_SET]);

  // job 完成时拉一次最新运行摘要，供右栏与父组件状态更新。
  useEffect(() => {
    if (!job || jobActive || !job.result) return;
    const runDir = (job.result as { run_dir?: unknown }).run_dir;
    if (typeof runDir !== "string") return;
    const runId = runDir.split(/[\\/]/).pop() ?? "";
    if (!runId) return;
    api<ZAWClosedLoopRunSummary>(`/api/experiments/${experimentId}/runs/${runId}/summary`)
      .then(onLatestRun)
      .catch((reason) => setError(String(reason)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, job?.status]);

  const handleFieldChange = (field: SchemaField, value: ParameterValues[string]) => {
    setValues({ ...values, [field.name]: value });
  };

  const runPreflight = async () => {
    setError("");
    try {
      const result = await api<{ ok: boolean; errors: string[] }>(
        `/api/experiments/${experimentId}/preflight`,
        { method: "POST", body: JSON.stringify({ parameters: values }) },
      );
      setPreflight(result);
    } catch (reason) {
      setError(String(reason));
    }
  };

  const startRun = async () => {
    setError("");
    setPreflight(undefined);
    setStarting(true);
    try {
      const next = await api<Job>(`/api/experiments/${experimentId}/runs`, {
        method: "POST",
        body: JSON.stringify({ parameters: values }),
      });
      setJob(next);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setStarting(false);
    }
  };

  const saveDefaults = async () => {
    setError("");
    setSavingDefaults(true);
    try {
      const result = await api<{ ok: boolean; message: string }>(
        `/api/experiments/${experimentId}/defaults`,
        { method: "PUT", body: JSON.stringify({ parameters: values }) },
      );
      setDefaultsStatus(result);
    } catch (reason) {
      setDefaultsStatus({ ok: false, message: String(reason) });
    } finally {
      setSavingDefaults(false);
    }
  };

  return (
    <div className="zaw-dashboard">
      <section className="zaw-panel zaw-panel-params">
        <div className="zaw-panel-head"><small>PARAMETERS</small><h2>实验参数</h2></div>
        <ParameterForm
          fields={fields}
          values={values}
          onChange={handleFieldChange}
          disabled={jobActive || starting}
        />
        <button
          className="zaw-secondary"
          disabled={savingDefaults || jobActive || !fields.length}
          onClick={() => void saveDefaults()}
        >
          {savingDefaults ? "保存中…" : "存为默认参数"}
        </button>
        {defaultsStatus && (
          <div className={`alert ${defaultsStatus.ok ? "success" : "error"}`}>{defaultsStatus.message}</div>
        )}
      </section>

      <section className="zaw-panel zaw-panel-control">
        <div className="zaw-panel-head"><small>CONTROL</small><h2>控制与状态</h2></div>
        <div className="zaw-control-buttons">
          <button className="zaw-secondary" disabled={disabled} onClick={() => void runPreflight()}>
            仅预检
          </button>
          <button
            className="zaw-primary"
            disabled={disabled || starting}
            onClick={() => void startRun()}
          >
            {jobActive ? "实验运行中" : starting ? "正在启动…" : "预检并运行实验"}
          </button>
        </div>
        {preflight && (
          <div className={`alert ${preflight.ok ? "success" : "error"}`}>
            {preflight.ok ? "参数预检通过，可以启动实验。" : preflight.errors.join("；")}
          </div>
        )}
        {blockedByOthers && !jobActive && (
          <div className="alert">其他硬件任务正在运行，等待其完成后才能启动本实验。</div>
        )}
        {error && <div className="alert error">{error}</div>}
        <div className="zaw-control-meta">
          <div><small>执行模式</small><span>{definition.execution_mode}</span></div>
          <div><small>所需设备</small><span>{definition.required_devices.join(" · ")}</span></div>
          <div><small>采集程序</small><span>{definition.acquisition_program}</span></div>
        </div>
        <JobView job={job} onUpdate={setJob} />
      </section>

      <section className="zaw-panel zaw-panel-result">
        <div className="zaw-panel-head">
          <small>LATEST</small>
          <h2>最近一次结果</h2>
        </div>
        <LatestPreview
          job={job}
          experimentId={experimentId}
          onResolved={onLatestRun}
          busy={jobActive}
        />
      </section>
    </div>
  );
}

function LatestPreview({
  job,
  experimentId,
  onResolved,
  busy,
}: {
  job: Job | undefined;
  experimentId: string;
  onResolved: (summary: ZAWClosedLoopRunSummary) => void;
  busy: boolean;
}) {
  const [summary, setSummary] = useState<ZAWClosedLoopRunSummary | undefined>();

  useEffect(() => {
    if (!job || !job.result) return;
    const runDir = (job.result as { run_dir?: unknown }).run_dir;
    if (typeof runDir !== "string") return;
    const runId = runDir.split(/[\\/]/).pop() ?? "";
    if (!runId) return;
    api<ZAWClosedLoopRunSummary>(`/api/experiments/${experimentId}/runs/${runId}/summary`)
      .then((value) => {
        setSummary(value);
        onResolved(value);
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, job?.status]);

  if (!summary && !busy) {
    return <p className="zaw-empty">尚无运行结果，启动一次实验后这里会展示指标、收敛曲线与波形对比。</p>;
  }
  if (busy && !summary) {
    return <p className="zaw-empty">正在采集，运行结束后会展示结果。</p>;
  }
  if (!summary) return null;

  const convergence = summary.artifacts[CONVERGENCE_PNG];
  const waveform = summary.artifacts[WAVEFORM_COMPARISON_PNG];
  return (
    <div className="zaw-latest">
      <MetricsCards summary={summary} busy={busy} />
      <p className="zaw-latest-meta">
        {summary.timestamp} · {summary.run_tag} · 停止原因：{summary.stop_reason}
      </p>
      {convergence && (
        <figure className="zaw-figure">
          <figcaption>收敛曲线</figcaption>
          <img src={convergence} alt="迭代收敛曲线" loading="lazy" />
        </figure>
      )}
      {waveform && (
        <figure className="zaw-figure">
          <figcaption>目标/实测/命令波形</figcaption>
          <img src={waveform} alt="波形对比" loading="lazy" />
        </figure>
      )}
    </div>
  );
}
