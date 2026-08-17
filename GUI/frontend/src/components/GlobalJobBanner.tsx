import { api } from "../api";
import type { JobSummary } from "../types/api";
import { useJobActivity } from "./JobActivity";
import { Status } from "./Status";

function kindLabel(kind: string): string {
  if (kind.startsWith("experiment:")) return "实验运行";
  if (kind.startsWith("analysis:")) return "离线分析";
  if (kind === "clock-sync") return "时钟同步";
  if (kind === "phase-calibration") return "相位校准";
  return kind;
}

export function GlobalJobBanner() {
  const { activeJobs } = useJobActivity();
  if (activeJobs.length === 0) return null;
  const cancel = (job: JobSummary) => {
    api(`/api/jobs/${job.id}/cancel`, { method: "POST" }).catch(() => {
      // 取消失败时由轮询重新显示任务状态。
    });
  };
  return (
    <div className="global-job-banner">
      {activeJobs.map((job) => (
        <div className="global-job-item" key={job.id}>
          <Status tone={job.status === "queued" ? "work" : "ok"}>
            {job.status === "queued" ? "等待中" : "运行中"}
          </Status>
          <strong>{kindLabel(job.kind)}</strong>
          <span className="global-job-message">{job.message}</span>
          <span className="global-job-percent">{Math.round(job.percent || 0)}%</span>
          <button className="secondary compact" onClick={() => cancel(job)}>取消</button>
        </div>
      ))}
    </div>
  );
}
