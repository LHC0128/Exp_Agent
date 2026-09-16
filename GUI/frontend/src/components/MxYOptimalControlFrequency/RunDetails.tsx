import { useEffect, useState } from "react";
import { api } from "../../api";
import type { Job, SchemaField } from "../../types/api";
import { JobView } from "../JobView";
import type { MxYOptimalControlFrequencyRunItem, MxYOptimalControlFrequencyRunSummary } from "./types";
import { readCalibration } from "./types";
import { SUMMARY_PATH, COMPARISON_PNG, PHASE_FREQUENCY_PNG } from "./images";

type RunDetailsProps = {
  experimentId: string;
  run: MxYOptimalControlFrequencyRunItem | null;
  fields: SchemaField[];
  onFillBack: (parameters: Record<string, unknown>) => void;
  onJumpToRun: () => void;
};

const formatNumber = (value: unknown): string => {
  if (typeof value === "number") return Number.isFinite(value) ? value.toString() : "—";
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value === null || value === undefined) return "—";
  return String(value);
};

const fieldByExternal = (fields: SchemaField[], externalName: string) =>
  fields.find((field) => field.name === externalName || field.label === externalName);

export function RunDetails({ experimentId, run, fields, onFillBack, onJumpToRun }: RunDetailsProps) {
  const [summary, setSummary] = useState<MxYOptimalControlFrequencyRunSummary | undefined>();
  const [error, setError] = useState("");
  const [analysisJob, setAnalysisJob] = useState<Job | undefined>();
  const [reanalysisBusy, setReanalysisBusy] = useState(false);

  useEffect(() => {
    if (!run) {
      setSummary(undefined);
      return;
    }
    let disposed = false;
    setSummary(undefined);
    setError("");
    api<MxYOptimalControlFrequencyRunSummary>(SUMMARY_PATH(run.run_id))
      .then((value) => {
        if (disposed) return;
        setSummary(value);
      })
      .catch((reason) => !disposed && setError(String(reason)));
    return () => {
      disposed = true;
    };
  }, [run?.run_id]);

  useEffect(() => {
    if (!analysisJob) return;
    if (["completed", "failed", "cancelled"].includes(analysisJob.status)) {
      // 重新分析完成后刷新一次 summary
      if (analysisJob.status === "completed" && run) {
        api<MxYOptimalControlFrequencyRunSummary>(SUMMARY_PATH(run.run_id))
          .then(setSummary)
          .catch((reason) => setError(String(reason)));
      }
      setAnalysisJob(undefined);
      setReanalysisBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisJob?.id, analysisJob?.status]);

  if (!run) {
    return <div className="zaw-run-details"><p className="zaw-empty">从左侧选择一条历史运行查看详情。</p></div>;
  }
  if (error) {
    return <div className="zaw-run-details"><div className="alert error">{error}</div></div>;
  }
  if (!summary) {
    return <div className="zaw-run-details"><p className="zaw-empty">载入运行摘要…</p></div>;
  }

  const reanalysis = async () => {
    setError("");
    setReanalysisBusy(true);
    try {
      const next = await api<Job>(`/api/runs/${encodeURIComponent(run.run_id)}/analyze`, {
        method: "POST",
        body: JSON.stringify({ experiment_id: experimentId }),
      });
      setAnalysisJob(next);
    } catch (reason) {
      setError(String(reason));
      setReanalysisBusy(false);
    }
  };

  const comparison = summary.artifacts[COMPARISON_PNG];
  const phaseFrequency = summary.artifacts[PHASE_FREQUENCY_PNG];
  const comparisonCaption = summary.comparison_enabled
    ? "最优控制中位数 vs 常数控制对照"
    : "最优控制中位数曲线";
  const calibration = readCalibration(summary.calibration);
  const parameterEntries = Object.entries(summary.parameters).sort(([a], [b]) => a.localeCompare(b));

  return (
    <div className="zaw-run-details">
      <div className="zaw-run-details-head">
        <div>
          <small>{summary.timestamp}</small>
          <h3>{summary.run_id}</h3>
          <span>run_tag: {summary.run_tag}</span>
        </div>
        <div className="zaw-run-details-actions">
          <button
            className="zaw-secondary"
            onClick={() => {
              onFillBack(summary.parameters);
              onJumpToRun();
            }}
          >
            填回参数
          </button>
          <button
            className="zaw-secondary"
            disabled={reanalysisBusy || analysisJob !== undefined}
            onClick={() => void reanalysis()}
          >
            {reanalysisBusy || analysisJob ? "重新分析中…" : "重新分析"}
          </button>
        </div>
      </div>
      <p className="zaw-run-details-meta">
        完成状态：{summary.completion_status} · 常数控制对照：{summary.comparison_enabled ? "已启用" : "未启用"}
      </p>
      {summary.comparison_enabled && (
        <details className="zaw-calibration">
          <summary>标定信息</summary>
          {calibration.extrapolated && (
            <div className="alert error">
              目标电压超出标定支撑范围（{formatNumber(calibration.supportVoltageMinV)} – {formatNumber(calibration.supportVoltageMaxV)} V），
              结果依赖全局线性外推，请谨慎使用。
            </div>
          )}
          <table>
            <tbody>
              <tr><th>标定运行</th><td>{formatNumber(calibration.calibrationRun)}</td></tr>
              <tr><th>目标 Larmor 频率 (Hz)</th><td>{formatNumber(calibration.targetLarmorFrequencyHz)}</td></tr>
              <tr><th>目标线圈电流 (A)</th><td>{formatNumber(calibration.targetCurrentA)}</td></tr>
              <tr><th>目标恒定 Z 电压 (V)</th><td>{formatNumber(calibration.targetVoltageV)}</td></tr>
              <tr><th>电压-电流拟合斜率 (A/V)</th><td>{formatNumber(calibration.gainAPerV)}</td></tr>
              <tr>
                <th>标定有效电压范围</th>
                <td>
                  {formatNumber(calibration.supportVoltageMinV)} – {formatNumber(calibration.supportVoltageMaxV)} V
                </td>
              </tr>
              <tr><th>分析 SHA-256</th><td>{formatNumber(calibration.analysisSha256)}</td></tr>
            </tbody>
          </table>
        </details>
      )}
      <div className="zaw-run-details-figures">
        {comparison && (
          <figure>
            <figcaption>{comparisonCaption}</figcaption>
            <img src={comparison} alt={comparisonCaption} loading="lazy" />
          </figure>
        )}
        {phaseFrequency && (
          <figure>
            <figcaption>相位-频率热图</figcaption>
            <img src={phaseFrequency} alt="相位-频率热图" loading="lazy" />
          </figure>
        )}
      </div>
      {parameterEntries.length > 0 && (
        <div className="zaw-parameter-table">
          <h4>实验参数（{parameterEntries.length} 项）</h4>
          <table>
            <thead>
              <tr><th>参数</th><th>本次取值</th></tr>
            </thead>
            <tbody>
              {parameterEntries.map(([key, value]) => {
                const field = fieldByExternal(fields, key);
                const label = field ? `${field.label}${field.unit ? ` (${field.unit})` : ""}` : key;
                return (
                  <tr key={key}>
                    <td>{label}</td>
                    <td>{formatNumber(value)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <JobView job={analysisJob} onUpdate={setAnalysisJob} />
    </div>
  );
}
