import { useEffect, useState } from "react";
import { api } from "../../api";
import type { Job, SchemaField, RunSummary } from "../../types/api";
import { JobView } from "../JobView";
import { MetricsCards } from "./MetricsCards";
import type { ZAWClosedLoopRunSummary } from "./types";
import { SUMMARY_PATH, CONVERGENCE_PNG, WAVEFORM_COMPARISON_PNG } from "./images";

type RunDetailsProps = {
  experimentId: string;
  run: RunSummary | null;
  fields: SchemaField[];
  onFillBack: (parameters: Record<string, unknown>) => void;
  onJumpToRun: () => void;
};

const formatNumber = (value: unknown): string => {
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "—";
    return Number.isInteger(value) ? value.toString() : value.toString();
  }
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value === null || value === undefined) return "—";
  return String(value);
};

const fieldByExternal = (fields: SchemaField[], externalName: string) =>
  fields.find((field) => field.name === externalName || field.label === externalName);

export function RunDetails({ experimentId, run, fields, onFillBack, onJumpToRun }: RunDetailsProps) {
  const [summary, setSummary] = useState<ZAWClosedLoopRunSummary | undefined>();
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
    api<ZAWClosedLoopRunSummary>(SUMMARY_PATH(run.id))
      .then((value) => {
        if (disposed) return;
        setSummary(value);
      })
      .catch((reason) => !disposed && setError(String(reason)));
    return () => {
      disposed = true;
    };
  }, [run?.id]);

  useEffect(() => {
    if (!analysisJob) return;
    if (["completed", "failed", "cancelled"].includes(analysisJob.status)) {
      // 重新分析完成后刷新一次 summary
      if (analysisJob.status === "completed" && run) {
        api<ZAWClosedLoopRunSummary>(SUMMARY_PATH(run.id))
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
      const next = await api<Job>(`/api/runs/${encodeURIComponent(run.id)}/analyze`, {
        method: "POST",
        body: JSON.stringify({ experiment_id: experimentId }),
      });
      setAnalysisJob(next);
    } catch (reason) {
      setError(String(reason));
      setReanalysisBusy(false);
    }
  };

  const convergence = summary.artifacts[CONVERGENCE_PNG];
  const waveform = summary.artifacts[WAVEFORM_COMPARISON_PNG];
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
      <MetricsCards summary={summary} busy={false} />
      <p className="zaw-run-details-meta">
        停止原因：{summary.stop_reason} · 迭代数：{summary.iteration_count} · 阈值：{summary.target_relative_rms === null ? "—" : `${(summary.target_relative_rms * 100).toFixed(2)}%`}
      </p>
      {Object.keys(summary.static_calibration).length > 0 && (
        <details className="zaw-calibration">
          <summary>静态标定信息</summary>
          <table>
            <tbody>
              {Object.entries(summary.static_calibration).map(([key, value]) => (
                <tr key={key}><th>{key}</th><td>{formatNumber(value)}</td></tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      <div className="zaw-run-details-figures">
        {convergence && (
          <figure>
            <figcaption>收敛曲线</figcaption>
            <img src={convergence} alt="收敛曲线" loading="lazy" />
          </figure>
        )}
        {waveform && (
          <figure>
            <figcaption>波形对比</figcaption>
            <img src={waveform} alt="波形对比" loading="lazy" />
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
