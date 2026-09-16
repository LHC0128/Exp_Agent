import { useEffect, useState } from "react";
import { api } from "../../api";
import { useJobActivity } from "../JobActivity";
import { JobView } from "../JobView";
import type {
  ExperimentDefinition,
  ExperimentSchema,
  Job,
  ParameterLayout,
  ParameterValues,
  SchemaField,
} from "../../types/api";
import { ParameterForm } from "./ParameterForm";
import type { MxYOptimalControlFrequencyRunSummary } from "./types";
import { readCalibration } from "./types";
import {
  COMPARISON_PNG,
  PHASE_FREQUENCY_PNG,
  SUMMARY_PATH,
} from "./images";
import { sameParameterLayout, validatedParameterLayout } from "../../pages/experimentHelpers";

type DashboardProps = {
  experimentId: string;
  definition: ExperimentDefinition;
  fields: SchemaField[];
  layout: ParameterLayout;
  /** 页面布局与磁盘保存的布局不一致时为 true。 */
  layoutDirty: boolean;
  onLayoutChange: (next: ParameterLayout) => void;
  /** 后端回读确认布局已落盘后，把页面布局同步为磁盘布局。 */
  onLayoutSaved: (next: ParameterLayout) => void;
  values: ParameterValues;
  setValues: (next: ParameterValues) => void;
  /** 页面级任务状态：切换 Tab 或重新进入页面后仍保留。 */
  job: Job | undefined;
  setJob: (next: Job | undefined) => void;
  prefill: ParameterValues | null;
  clearPrefill: () => void;
};

export function Dashboard({
  experimentId,
  definition,
  fields,
  layout,
  layoutDirty,
  onLayoutChange,
  onLayoutSaved,
  values,
  setValues,
  job,
  setJob,
  prefill,
  clearPrefill,
}: DashboardProps) {
  const { hardwareBusy, activeJobs } = useJobActivity();
  const [starting, setStarting] = useState(false);
  const [savingDefaults, setSavingDefaults] = useState(false);
  const [preflight, setPreflight] = useState<{ ok: boolean; errors: string[] } | undefined>();
  const [defaultsStatus, setDefaultsStatus] = useState<{ ok: boolean; message: string } | undefined>();
  const [moveNotice, setMoveNotice] = useState("");
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
      // 提交当前全部参数；后端只在启用对照时校验对照专属字段。
      const result = await api<{ ok: boolean; errors: string[] }>(
        `/api/experiments/${experimentId}/preflight`,
        { method: "POST", body: JSON.stringify({ parameters: values }) },
      );
      setPreflight(result);
      if (!result.ok) return;
      setJob(await api<Job>(`/api/experiments/${experimentId}/runs`, {
        method: "POST",
        body: JSON.stringify({ parameters: values }),
      }));
    } catch (reason) {
      setError(String(reason));
    } finally {
      setStarting(false);
    }
  };

  const saveDefaults = async () => {
    setError("");
    setSavingDefaults(true);
    setDefaultsStatus(undefined);
    try {
      const result = await api<{ ok: boolean; message: string; schema: ExperimentSchema }>(
        `/api/experiments/${experimentId}/defaults`,
        { method: "PUT", body: JSON.stringify({ parameters: values, parameter_layout: layout }) },
      );
      // 保存成功后回读 schema，确认磁盘布局与页面一致后再清除未保存标记。
      const persisted = result.schema?.parameter_layout_saved
        ? validatedParameterLayout(result.schema.fields, result.schema.parameter_layout)
        : undefined;
      if (!persisted) {
        throw new Error("后端未确认参数分类已保存，请确认 GUI 后端已重启后重试");
      }
      if (!sameParameterLayout(persisted, layout)) {
        throw new Error("后端回传的参数分类与当前布局不一致，请刷新页面后重试");
      }
      onLayoutSaved(persisted);
      setDefaultsStatus({ ok: true, message: result.message });
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
          layout={layout}
          values={values}
          onChange={handleFieldChange}
          onLayoutChange={onLayoutChange}
          onMoveNotice={setMoveNotice}
          disabled={jobActive || starting}
        />
        {moveNotice && <p className="zaw-layout-dirty" role="status">{moveNotice}</p>}
        {layoutDirty && (
          <p className="zaw-layout-dirty" role="status">布局有未保存修改</p>
        )}
        <button
          className="zaw-secondary"
          disabled={savingDefaults || jobActive || !fields.length}
          onClick={() => void saveDefaults()}
        >
          {savingDefaults ? "保存中…" : "保存参数与布局"}
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
        <LatestPreview job={job} busy={jobActive} />
      </section>
    </div>
  );
}

function LatestPreview({
  job,
  busy,
}: {
  job: Job | undefined;
  busy: boolean;
}) {
  const [summary, setSummary] = useState<MxYOptimalControlFrequencyRunSummary | undefined>();

  useEffect(() => {
    if (!job || !job.result) return;
    const runDir = (job.result as { run_dir?: unknown }).run_dir;
    if (typeof runDir !== "string") return;
    const runId = runDir.split(/[\\/]/).pop() ?? "";
    if (!runId) return;
    api<MxYOptimalControlFrequencyRunSummary>(SUMMARY_PATH(runId))
      .then(setSummary)
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, job?.status]);

  if (!summary && !busy) {
    return <p className="zaw-empty">尚无运行结果，启动一次实验后这里会展示频率响应曲线与相位-频率热图。</p>;
  }
  if (busy && !summary) {
    return <p className="zaw-empty">正在采集，运行结束后会展示结果。</p>;
  }
  if (!summary) return null;

  const calibration = readCalibration(summary.calibration);
  const comparison = summary.artifacts[COMPARISON_PNG];
  const heatmap = summary.artifacts[PHASE_FREQUENCY_PNG];
  const optimal = summary.optimal_response_metadata;
  const constant = summary.constant_response_metadata;
  return (
    <div className="zaw-latest">
      <div className="zaw-metrics-grid">
        <div className="zaw-metric">
          <small>完成状态</small>
          <span className="zaw-metric-value">{summary.completion_status}</span>
        </div>
        <div className="zaw-metric">
          <small>常数对照</small>
          <span className="zaw-metric-value">{summary.comparison_enabled ? "已启用" : "未启用"}</span>
        </div>
        <div className="zaw-metric">
          <small>最优控制有效点</small>
          <span className="zaw-metric-value">{formatCount(optimal.valid_points)}</span>
        </div>
        <div className="zaw-metric">
          <small>常数控制有效点</small>
          <span className="zaw-metric-value">{formatCount(constant.valid_points)}</span>
        </div>
      </div>
      {summary.comparison_enabled && calibration.extrapolated && (
        <div className="alert error">
          目标恒定 Z 电压 {formatNumber(calibration.targetVoltageV)} V 超出标定有效范围，结果依赖全局线性外推。
        </div>
      )}
      <p className="zaw-latest-meta">
        {summary.timestamp} · {summary.run_tag}
        {summary.comparison_enabled && calibration.calibrationRun
          ? ` · 标定：${calibration.calibrationRun}`
          : ""}
      </p>
      {comparison && (
        <figure className="zaw-figure">
          <figcaption>
            {summary.comparison_enabled ? "最优控制中位数 vs 常数控制对照" : "最优控制中位数曲线"}
          </figcaption>
          <img src={comparison} alt="RF 频率响应曲线" loading="lazy" />
        </figure>
      )}
      {heatmap && (
        <figure className="zaw-figure">
          <figcaption>相位-频率热图</figcaption>
          <img src={heatmap} alt="相位-频率热图" loading="lazy" />
        </figure>
      )}
    </div>
  );
}

const formatNumber = (value: unknown): string =>
  typeof value === "number" && Number.isFinite(value) ? String(value) : "—";

const formatCount = (value: unknown): string =>
  typeof value === "number" && Number.isFinite(value) ? String(value) : "—";
