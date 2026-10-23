import { createContext, useContext, useEffect, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { useLocation, useParams } from "react-router-dom";
import { api } from "../api";
import type { Job, JobSummary } from "../types/api";

type Destination = { experimentId: string; type: "experiment" | "session" | "analysis" | "comparison"; runId?: string };

/** 任务 kind 是现有后端契约；不要把任意 kind 当成 URL。 */
export function experimentDestination(job: Pick<JobSummary, "kind">): Destination | undefined {
  const [type, experimentId, ...parts] = job.kind.split(":");
  if (!["experiment", "session", "analysis", "comparison"].includes(type) || !/^[a-z0-9][a-z0-9-]*$/.test(experimentId ?? "")) return;
  return { experimentId, type: type as Destination["type"], runId: type === "analysis" ? parts.join(":") || undefined : undefined };
}

export function experimentJobUrl(job: Pick<JobSummary, "kind" | "id">): string | undefined {
  const destination = experimentDestination(job);
  if (destination) return `/experiments/${destination.experimentId}?job=${encodeURIComponent(job.id)}`;
}

type Request = { job: Job; destination: Destination; key: string };
const TaskContext = createContext<Request | undefined>(undefined);
const RequestedIdContext = createContext<string | undefined>(undefined);
export const useRequestedTask = () => useContext(TaskContext);
export const useRequestedJobId = () => useContext(RequestedIdContext);

/** 初始恢复也尊重指定任务，避免较晚完成的自动恢复请求覆盖跳转目标。 */
export function recoverableJob(jobs: JobSummary[], experimentId: string, requestedId?: string,
  group: "acquisition" | "analysis" | "comparison" = "acquisition") {
  const matching = jobs.filter(job => {
    const destination = experimentDestination(job);
    return destination?.experimentId === experimentId && (group === "acquisition"
      ? destination.type === "experiment" || destination.type === "session" : destination.type === group);
  });
  return matching.find(job => job.id === requestedId)
    ?? matching.find(job => job.status === "running") ?? matching.find(job => job.status === "queued")
    ?? (group === "acquisition" ? matching.find(job => Boolean(job.started_at)) : undefined);
}

/** 只读恢复指定任务；查询参数绝不触发启动、应用参数或重分析。 */
export function ExperimentNavigation({ children }: { children: ReactNode }) {
  const location = useLocation();
  const { experimentId } = useParams();
  const jobId = new URLSearchParams(location.search).get("job");
  const [request, setRequest] = useState<Request>();
  const [error, setError] = useState("");
  useEffect(() => {
    setRequest(undefined); setError("");
    if (!jobId) return;
    const controller = new AbortController();
    api<Job>(`/api/jobs/${encodeURIComponent(jobId)}`, { signal: controller.signal }).then(job => {
      if (controller.signal.aborted) return;
      const destination = experimentDestination(job);
      if (!destination || destination.experimentId !== experimentId) throw new Error("该任务不属于当前实验");
      setRequest({ job, destination, key: location.key });
    }).catch(reason => { if (!controller.signal.aborted) setError(`无法恢复指定任务：${String(reason)}。可在下方查看当前实验状态。`); });
    return () => controller.abort();
  }, [experimentId, jobId, location.key]);
  return <RequestedIdContext.Provider value={jobId ?? undefined}><TaskContext.Provider value={request}>
    {error && <div className="alert error" role="alert">{error}</div>}{children}
  </TaskContext.Provider></RequestedIdContext.Provider>;
}

type JobSetter = Dispatch<SetStateAction<Job | undefined>>;
export function useExperimentTask({ ready, setTab, setJob, setAnalysisJob, setComparisonJob, setSelectedId, setHistoryView }: {
  ready: boolean; setTab?: Dispatch<SetStateAction<"run" | "history">>; setJob?: JobSetter;
  setAnalysisJob?: JobSetter; setComparisonJob?: JobSetter; setSelectedId?: Dispatch<SetStateAction<string>>;
  setHistoryView?: Dispatch<SetStateAction<"single" | "comparison">>;
}) {
  const request = useRequestedTask();
  useEffect(() => {
    if (!ready || !request) return;
    const { job, destination } = request;
    if (destination.type === "comparison") {
      setComparisonJob?.(job); setHistoryView?.("comparison"); setTab?.("history");
    } else if (destination.type === "analysis") {
      setAnalysisJob?.(job);
      if (destination.runId) setSelectedId?.(destination.runId);
      setTab?.("run");
    } else {
      setJob?.(job); setTab?.("run");
    }
  }, [ready, request, setTab, setJob, setAnalysisJob, setComparisonJob, setSelectedId, setHistoryView]);
}
