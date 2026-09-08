import { useEffect, useState, type Dispatch, type SetStateAction } from "react";

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

type ClockItem = {
  label?: unknown;
  device_type?: unknown;
  device_id?: unknown;
  before?: unknown;
  after?: unknown;
  target?: unknown;
  ok?: unknown;
  error?: unknown;
};

function isClockSyncResult(value: unknown): value is ClockItem[] {
  return Array.isArray(value) && value.every((item) => (
    item && typeof item === "object" && "label" in item && "ok" in item
  ));
}

function ClockSyncTable({ items }: { items: ClockItem[] }) {
  return (
    <div className="job-result-table-wrap">
      <table className="job-result-table">
        <thead>
          <tr><th>设备</th><th>类型</th><th>修改前</th><th>修改后</th><th>目标</th><th>结果</th></tr>
        </thead>
        <tbody>
          {items.map((item, index) => (
            <tr key={index} className={item.ok ? "" : "row-failed"}>
              <td>{String(item.label ?? item.device_id ?? "")}</td>
              <td>{String(item.device_type ?? "")}</td>
              <td>{String(item.before ?? "")}</td>
              <td>{String(item.after ?? "")}</td>
              <td>{String(item.target ?? "")}</td>
              <td>{item.ok ? "成功" : `失败${item.error ? `：${item.error}` : ""}`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ResultView({ job }: { job: Job }) {
  const result = job.result;
  if (result === undefined || result === null) return null;
  const runDir = (result as { run_dir?: unknown }).run_dir;
  return (
    <div className="job-results">
      {typeof runDir === "string" && (
        <div className="alert success job-result">结果已保存：{runDir}</div>
      )}
      {job.kind === "clock-sync" && isClockSyncResult(result) && <ClockSyncTable items={result} />}
      {job.kind !== "clock-sync" && runDir === undefined && (
        <details className="job-result-details">
          <summary>查看结果</summary>
          <pre>{JSON.stringify(result, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

export function JobView({ job, onUpdate }: { job?: Job; onUpdate: JobUpdate }) {
  const [expanded, setExpanded] = useState(false);
  useEffect(() => { setExpanded(false); }, [job?.id]);

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
        // 排队事件不能把任务状态无条件改成 running：
        // queued 事件期间后端状态仍是 queued，详情页、全局横幅与
        // SSE 必须保持一致；其余进度事件说明任务已真正开始运行。
        const nextStatus = payload.stage === "queued" ? "queued" : "running";
        return {
          ...current,
          status: nextStatus,
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
  return (
    <div className="job">
      <div className="job-top">
        <Status tone={job.status === "failed" ? "bad" : job.status === "completed" ? "ok" : "work"}>
          {job.status === "queued" ? "queued" : job.status}
        </Status>
        <strong>{job.message}</strong>
        <span>{Math.round(job.percent || 0)}%</span>
      </div>
      <div className="progress"><i style={{ width: `${job.percent || 0}%` }} /></div>
      {job.status === "completed" && <ResultView job={job} />}
      {job.status === "failed" && job.error && <div className="alert error">{job.error}</div>}
      <div className="log">
        {(expanded ? job.events : job.events.slice(-12)).map((event) => (
          <p key={event.index}>
            <time>{event.timestamp?.split("T")[1]}</time>
            {event.message}
          </p>
        ))}
      </div>
      {job.events.length > 12 && (
        <button className="secondary compact log-toggle" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "收起日志" : `查看全部 ${job.events.length} 条日志`}
        </button>
      )}
      {["queued", "running"].includes(job.status) && (
        <button
          className="danger"
          onClick={() => api<Job>(`/api/jobs/${job.id}/cancel`, { method: "POST" })
            .then((value) => onUpdate(value))
            .catch(() => {
              // 取消失败时保留当前状态，由 SSE 或轮询纠正。
            })}
        >
          {job.kind.startsWith("experiment:") ? "安全停止" : "取消任务"}
        </button>
      )}
    </div>
  );
}
