import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api } from "../api";
import type { JobSummary } from "../types/api";

export type JobActivity = {
  hardwareBusy: boolean;
  activeJobs: JobSummary[];
};

const JobActivityContext = createContext<JobActivity>({ hardwareBusy: false, activeJobs: [] });

export function useJobActivity(): JobActivity {
  return useContext(JobActivityContext);
}

/**
 * 全局任务活动提供者：轮询健康状态与任务列表，
 * 供全局横幅展示、各页面禁用硬件操作按钮。
 */
export function JobActivityProvider({ children }: { children: ReactNode }) {
  const [activity, setActivity] = useState<JobActivity>({ hardwareBusy: false, activeJobs: [] });

  useEffect(() => {
    let disposed = false;
    const refresh = () => {
      Promise.all([
        api<{ status: string; hardware_busy: boolean }>("/api/health"),
        api<JobSummary[]>("/api/jobs"),
      ]).then(([health, jobs]) => {
        if (disposed) return;
        const activeJobs = jobs.filter((item) => ["queued", "running"].includes(item.status));
        setActivity({ hardwareBusy: health.hardware_busy, activeJobs });
      }).catch(() => {
        // 后端不可用时保持静默，页面自身的错误提示负责反馈。
      });
    };
    refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <JobActivityContext.Provider value={activity}>
      {children}
    </JobActivityContext.Provider>
  );
}
