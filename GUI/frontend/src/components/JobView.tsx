import { useEffect, type Dispatch, type SetStateAction } from "react";

import { api } from "../api";
import type { Job, JobEvent, JobStatus } from "../types/api";
import { Status } from "./Status";

type JobUpdate = Dispatch<SetStateAction<Job | undefined>>;

function isJobEvent(value: unknown): value is JobEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Partial<JobEvent>;
  return (
    typeof event.index === "number" &&
    typeof event.timestamp === "string" &&
    typeof event.stage === "string" &&
    typeof event.message === "string"
  );
}

function isDoneEvent(value: unknown): value is { done: true; status: JobStatus } {
  if (!value || typeof value !== "object") return false;
  const event = value as { done?: unknown; status?: unknown };
  return event.done === true && ["completed", "failed", "cancelled"].includes(String(event.status));
}

export function JobView({ job, onUpdate }: { job?: Job; onUpdate: JobUpdate }) {
  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;

    let disposed = false;
    const source = new EventSource(`/api/jobs/${job.id}/events`);
    source.onmessage = (message) => {
      let payload: unknown;
      try {
        payload = JSON.parse(message.data);
      } catch {
        return;
      }
      if (isDoneEvent(payload)) {
        source.close();
        api<Job>(`/api/jobs/${job.id}`).then((value) => {
          if (!disposed) onUpdate(value);
        }).catch(() => {
          if (!disposed) {
            onUpdate((current) => current?.id === job.id
              ? { ...current, status: payload.status }
              : current);
          }
        });
        return;
      }
      if (!isJobEvent(payload)) return;
      onUpdate((current) => {
        if (!current || current.id !== job.id) return current;
        const events = current.events.some((event) => event.index === payload.index)
          ? current.events
          : [...current.events, payload].sort((left, right) => left.index - right.index);
        return {
          ...current,
          status: "running",
          stage: payload.stage,
          message: payload.message,
          percent: payload.percent ?? current.percent,
          events,
        };
      });
    };
    source.onerror = () => {
      api<Job>(`/api/jobs/${job.id}`).then((value) => {
        if (!disposed) onUpdate(value);
      }).catch(() => {
        // EventSource 会按浏览器退避策略自动重连。
      });
    };
    return () => {
      disposed = true;
      source.close();
    };
  }, [job?.id, job?.status, onUpdate]);

  if (!job) return null;
  const result = job.result as { run_dir?: string } | undefined;
  return (
    <div className="job">
      <div className="job-top">
        <Status tone={job.status === "failed" ? "bad" : job.status === "completed" ? "ok" : "work"}>
          {job.status}
        </Status>
        <strong>{job.message}</strong>
        <span>{Math.round(job.percent || 0)}%</span>
      </div>
      <div className="progress"><i style={{ width: `${job.percent || 0}%` }} /></div>
      {job.status === "completed" && result?.run_dir && (
        <div className="alert success job-result">结果已保存：{result.run_dir}</div>
      )}
      <div className="log">
        {job.events.slice(-12).map((event) => (
          <p key={event.index}>
            <time>{event.timestamp?.split("T")[1]}</time>
            {event.message}
          </p>
        ))}
      </div>
      {job.status === "running" && job.kind.startsWith("experiment:") && (
        <button
          className="danger"
          onClick={() => api<Job>(`/api/jobs/${job.id}/cancel`, { method: "POST" }).then((value) => onUpdate(value))}
        >
          安全停止
        </button>
      )}
    </div>
  );
}
