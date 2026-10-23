import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { formatEta } from "../formatEta";
import type { ExperimentDefinition, JobSummary } from "../types/api";
import { experimentDestination, experimentJobUrl } from "./experimentNavigation";
import { useJobActivity } from "./JobActivity";
import { Status } from "./Status";

function kindLabel(kind: string): string {
  if (kind.startsWith("experiment:")) return "实验运行";
  if (kind.startsWith("session:")) return "分步会话";
  if (kind.startsWith("analysis:")) return "离线分析";
  if (kind.startsWith("comparison:")) return "对比分析";
  return ({ "clock-sync": "时钟同步", "load-init": "输出负载初始化", "phase-calibration": "相位校准", "lockin-capture": "锁相采集", "z-arbitrary-control": "Z 任意波控制" } as Record<string, string>)[kind] ?? (kind.startsWith("update:") ? "软件更新" : kind);
}

export function GlobalJobBanner() {
  const { activeJobs, connection = "connected", finishedJobs = [], dismissFinished } = useJobActivity();
  const [expanded, setExpanded] = useState(false);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState<string[]>([]);
  const ids = [...new Set([...activeJobs, ...finishedJobs].map(job => experimentDestination(job)?.experimentId).filter((id): id is string => Boolean(id)))].sort().join(",");
  useEffect(() => {
    if (!ids) return;
    const controller = new AbortController();
    for (const id of ids.split(",")) {
      api<ExperimentDefinition>(`/api/experiments/${id}`, { signal: controller.signal }).then(definition => {
        if (!controller.signal.aborted && definition.title) setTitles(current => ({ ...current, [id]: definition.title }));
      }).catch(() => { /* 名称不可用时显示稳定 ID，跳转仍可用。 */ });
    }
    return () => controller.abort();
  }, [ids]);
  const title = (job: JobSummary) => {
    const id = experimentDestination(job)?.experimentId;
    return `${kindLabel(job.kind)}${id ? ` · ${titles[id] ?? id}` : ""}`;
  };
  const cancel = async (job: JobSummary) => {
    setError(""); setCancelling(current => [...current, job.id]);
    try { await api(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST" }); }
    catch (reason) { setError(`取消失败：${String(reason)}。任务可能仍在运行，请核对状态后重试。`); }
    finally { setCancelling(current => current.filter(id => id !== job.id)); }
  };
  const jobs = [...activeJobs].sort((left, right) => Number(left.status === "queued") - Number(right.status === "queued"));
  if (connection === "connected" && !jobs.length && !finishedJobs.length && !error) return null;
  return <div className="global-job-banner" aria-label="全局任务">
    {connection === "connecting" && <p className="connection-note" role="status">正在连接后端…</p>}
    {connection === "disconnected" && <p className="connection-note error" role="alert">后端连接中断，任务与仪器状态未知；下方是最后一次状态。请检查后端，关闭页面不会停止实验。</p>}
    {jobs.length > 1 && <button className="secondary compact task-toggle" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
      {expanded ? "收起任务列表" : `展开全部 ${jobs.length} 个任务`}
    </button>}
    <div className="global-job-list">
      {(expanded ? jobs : jobs.slice(0, 1)).map(job => {
        const url = experimentJobUrl(job);
        return <div className="global-job-item" key={job.id}>
          <Status tone={connection !== "connected" ? "bad" : "work"}>{connection !== "connected" ? "状态未知" : job.status === "queued" ? "排队中" : "运行中"}</Status>
          <strong>{title(job)}</strong>
          <span className="global-job-message">{job.message}</span>
          <span className="global-job-percent">{job.percent == null ? "进度待更新" : `${Math.round(job.percent)}%`}</span>
          {formatEta(job.estimated_remaining_seconds) && <span className="global-job-eta">预计剩余 {formatEta(job.estimated_remaining_seconds)}</span>}
          {url && <Link className="task-link" to={url} aria-label={`前往实验：${title(job)}`}>前往实验 →</Link>}
          <button className="secondary compact" disabled={connection !== "connected" || cancelling.includes(job.id)} onClick={() => void cancel(job)}>{cancelling.includes(job.id) ? "正在取消…" : "取消"}</button>
        </div>;
      })}
    </div>
    {error && <p className="connection-note error" role="alert">{error}</p>}
    {finishedJobs.map(job => <div className={`task-finished ${job.status === "failed" ? "error" : ""}`} key={job.id} role="status">
      <span>{title(job)} · {job.status === "completed" ? "已完成" : job.status === "failed" ? "失败，请查看日志" : "已取消"}</span>
      {experimentJobUrl(job) && <Link to={experimentJobUrl(job)!}>查看任务</Link>}
      <button className="secondary compact" aria-label={`关闭任务通知 ${job.id}`} onClick={() => dismissFinished?.(job.id)}>关闭</button>
    </div>)}
  </div>;
}
