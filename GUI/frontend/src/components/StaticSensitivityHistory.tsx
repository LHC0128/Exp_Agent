import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { api } from "../api";
import type { Job, ParameterValues, SchemaField } from "../types/api";
import { useRunHistory } from "./workflow/RunHistory";

type Run = { run_id: string; timestamp: string; run_tag: string; completion_status: string };
type Summary = Run & { parameters: ParameterValues; analysis: Record<string, unknown>; artifacts?: Record<string, string> };
type Props = {
  experimentId: string;
  fields: SchemaField[];
  /** 模板的运行/填回占用条件：jobActive || starting || live。 */
  busy: boolean;
  analysisJob?: Job;
  setAnalysisJob: Dispatch<SetStateAction<Job | undefined>>;
  onFillBack: (parameters: ParameterValues) => void;
};

export function StaticSensitivityHistory({ experimentId, fields, busy, analysisJob, setAnalysisJob, onFillBack }: Props) {
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const history = useRunHistory<Run, Summary>({
    experimentId, reloadKeys: [busy, analysisJob?.status], setError, autoSelectLatest: false,
  });
  const selected = history.selected;
  const analysisActive = Boolean(analysisJob && ["queued", "running"].includes(analysisJob.status));
  const oldFormat = Boolean(selected && !("dispersion_duration_s" in selected.parameters));
  useEffect(() => {
    if (!analysisJob || !["completed", "failed", "cancelled"].includes(analysisJob.status)) return;
    setAnalysisJob(undefined);
    setStarting(false);
  }, [analysisJob?.id, analysisJob?.status, setAnalysisJob]);
  const reanalyze = async () => {
    if (!selected) return;
    setStarting(true); setError("");
    try {
      setAnalysisJob(await api<Job>(`/api/runs/${encodeURIComponent(selected.run_id)}/analyze`, {
        method: "POST", body: JSON.stringify({ experiment_id: experimentId }),
      }));
    } catch (reason) { setError(String(reason)); setStarting(false); }
  };
  return <div className="zaw-history">
    <section className="zaw-run-list">
      <div className="zaw-run-list-head"><small>HISTORY</small><h3>历史运行</h3><span>{history.list.runs.length}</span></div>
      {error && <div className="alert error">{error}</div>}
      <ul>{history.list.runs.map(run => <li key={run.run_id}>
        <button className={`zaw-run-card ${history.selectedId === run.run_id ? "selected" : ""}`} onClick={() => history.setSelectedId(run.run_id)}>
          <strong>{run.run_id}</strong><span>{run.timestamp}</span><span>{run.completion_status}</span>
        </button></li>)}</ul>
      {!history.list.runs.length && <p className="zaw-empty">暂无运行记录。</p>}
    </section>
    <section className="zaw-run-details">
      {!selected ? <p className="zaw-empty">{history.selectedId ? "正在读取运行详情…" : "从左侧选择一条历史运行查看详情。"}</p> : <>
        <div className="zaw-run-details-head">
          <div><small>{selected.timestamp}</small><h3>{selected.run_id}</h3><span>run_tag: {selected.run_tag}</span></div>
          <div className="zaw-run-details-actions">
            <button className="zaw-secondary" disabled={busy || oldFormat} onClick={() => onFillBack(selected.parameters)}>填回参数</button>
            <button className="zaw-secondary" disabled={busy || starting || analysisActive || oldFormat || ["running", "acquired"].includes(selected.completion_status)}
              onClick={() => void reanalyze()}>{starting || analysisActive ? "重新分析中…" : "重新分析"}</button>
          </div>
        </div>
        <p className="zaw-run-details-meta">完成状态：{selected.completion_status}</p>
        {oldFormat && <p className="alert">旧格式运行仅可查看已有结果，不支持填回或重新分析。</p>}
        {typeof selected.analysis.error === "string" && <p className="alert error">{selected.analysis.error}</p>}
        {Array.isArray(selected.analysis.fit_rejection_reasons) && selected.analysis.fit_rejection_reasons.length > 0
          && <p className="alert error">{selected.analysis.fit_rejection_reasons.join("；")}</p>}
        <div className="zaw-metrics-grid">
          <div className="zaw-metric"><small>拟合状态</small><span className="zaw-metric-value">{selected.analysis.fit_valid === undefined ? "未分析" : selected.analysis.fit_valid ? "有效" : "无效"}</span></div>
          <div className="zaw-metric"><small>最佳量程</small><span className="zaw-metric-value">{String(selected.analysis.best_range_label ?? "—")}</span></div>
          <div className="zaw-metric"><small>灵敏度</small><span className="zaw-metric-value">{formatNumber(selected.analysis.sensitivity_flat_fT_per_sqrt_Hz)} fT/√Hz</span></div>
        </div>
        <div className="zaw-run-details-figures">{Object.entries(selected.artifacts ?? {})
          .filter(([name]) => name.endsWith(".png") && selected.analysis.success !== false)
          .map(([name, url]) => <figure key={name}><figcaption>{name}</figcaption><img src={url} alt={name} loading="lazy" /></figure>)}</div>
        <div className="zaw-parameter-table"><h4>实验参数（{Object.keys(selected.parameters).length} 项）</h4><table><tbody>
          {Object.entries(selected.parameters).map(([key, value]) => {
            const field = fields.find(item => item.name === key || item.label === key);
            return <tr key={key}><th>{field?.label ?? key}</th><td>{formatValue(value)}</td></tr>;
          })}
        </tbody></table></div>
      </>}
    </section>
  </div>;
}

const formatValue = (value: unknown) => Array.isArray(value) ? value.join(", ") : typeof value === "boolean" ? (value ? "是" : "否") : String(value);
const formatNumber = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toString() : "—";
