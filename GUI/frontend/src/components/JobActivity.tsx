import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api } from "../api";
import type { JobSummary } from "../types/api";

export type JobActivity = {
  hardwareBusy: boolean;
  activeJobs: JobSummary[];
  connection?: "connecting" | "connected" | "disconnected";
  finishedJobs?: JobSummary[];
  dismissFinished?: (id: string) => void;
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
  const [activity, setActivity] = useState<JobActivity>({ hardwareBusy: true, activeJobs: [], connection: "connecting" });
  const [finishedJobs, setFinishedJobs] = useState<JobSummary[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    let previous = new Set<string>();
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const [health, jobs] = await Promise.all([
          api<{ status: string; hardware_busy: boolean }>("/api/health", { signal: controller.signal }),
          api<JobSummary[]>("/api/jobs", { signal: controller.signal }),
        ]);
        if (controller.signal.aborted) return;
        const activeJobs = jobs.filter(item => ["queued", "running"].includes(item.status));
        const finished = jobs.filter(item => previous.has(item.id) && ["completed", "failed", "cancelled"].includes(item.status));
        if (finished.length) setFinishedJobs(current => [...finished, ...current].slice(0, 3));
        previous = new Set(activeJobs.map(item => item.id));
        setActivity({ hardwareBusy: health.hardware_busy, activeJobs, connection: "connected" });
      } catch {
        if (!controller.signal.aborted) setActivity(current => ({ ...current, hardwareBusy: true, connection: "disconnected" }));
      } finally { pending = false; }
    };
    refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  return (
    <JobActivityContext.Provider value={{ ...activity, finishedJobs,
      dismissFinished: id => setFinishedJobs(current => current.filter(item => item.id !== id)) }}>
      {children}
    </JobActivityContext.Provider>
  );
}
