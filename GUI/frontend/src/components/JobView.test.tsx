import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobView } from "./JobView";
import type { Job, JobEvent } from "../types/api";

vi.mock("../api", () => ({
  api: vi.fn(),
}));

import { api } from "../api";

type EventListener = (message: { data: string }) => void;

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onmessage: EventListener | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }
}

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    kind: "experiment:demo",
    status: "queued",
    stage: "queued",
    percent: 0,
    message: "等待执行",
    created_at: "2025-01-01T00:00:00",
    started_at: null,
    finished_at: null,
    events: [],
    ...overrides,
  };
}

function makeEvent(overrides: Partial<JobEvent> = {}): JobEvent {
  return {
    index: 0,
    timestamp: "2025-01-01T00:00:01",
    stage: "queued",
    message: "等待硬件空闲",
    percent: 0,
    level: "info",
    data: {},
    ...overrides,
  };
}

function renderJobView(initial: Job) {
  let current: Job | undefined = initial;
  const updates: Job[] = [];
  render(
    <JobView
      job={initial}
      onUpdate={(updater) => {
        const next =
          typeof updater === "function"
            ? (updater as (value: Job | undefined) => Job | undefined)(current)
            : updater;
        if (next) {
          current = next;
          updates.push(next);
        }
      }}
    />,
  );
  const source = FakeEventSource.instances[FakeEventSource.instances.length - 1];
  return { current: () => current, updates: () => updates, source };
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  FakeEventSource.instances = [];
});

describe("JobView 排队任务状态", () => {
  it("queued 事件保持任务状态为 queued", async () => {
    const { current, updates, source } = renderJobView(makeJob());
    await act(async () => {
      source.onmessage?.({ data: JSON.stringify(makeEvent()) });
    });
    expect(current()?.status).toBe("queued");
    expect(current()?.message).toBe("等待硬件空闲");
    expect(current()?.events.map((event) => event.stage)).toEqual(["queued"]);
    expect(updates().length).toBeGreaterThan(0);
  });

  it("非排队进度事件把状态更新为 running", async () => {
    const { current, source } = renderJobView(makeJob());
    await act(async () => {
      source.onmessage?.({ data: JSON.stringify(makeEvent()) });
    });
    expect(current()?.status).toBe("queued");
    await act(async () => {
      source.onmessage?.({
        data: JSON.stringify(
          makeEvent({ index: 1, stage: "start", message: "任务开始" }),
        ),
      });
    });
    expect(current()?.status).toBe("running");
    expect(current()?.message).toBe("任务开始");
  });

  it("终态事件拉取后端状态并更新为 completed", async () => {
    vi.mocked(api).mockResolvedValue(
      makeJob({ status: "completed", stage: "complete", message: "任务完成", percent: 100 }),
    );
    const { current, source } = renderJobView(makeJob());
    await act(async () => {
      source.onmessage?.({
        data: JSON.stringify({ done: true, status: "completed" }),
      });
    });
    expect(vi.mocked(api)).toHaveBeenCalledWith("/api/jobs/job-1");
    expect(current()?.status).toBe("completed");
    expect(source.closed).toBe(true);
  });

  it("SSE 进度事件更新 ETA，普通日志事件不覆盖", async () => {
    const { current, source } = renderJobView(makeJob({ status: "running" }));
    await act(async () => {
      source.onmessage?.({
        data: JSON.stringify(
          makeEvent({
            index: 1,
            stage: "optimal_control_scan",
            message: "第 1 点；总进度 1/10",
            percent: 9,
            data: { estimated_remaining_seconds: 600 },
          }),
        ),
      });
    });
    expect(current()?.estimated_remaining_seconds).toBe(600);
    await act(async () => {
      source.onmessage?.({
        data: JSON.stringify(
          makeEvent({ index: 2, stage: "running", message: "普通日志", percent: 9 }),
        ),
      });
    });
    expect(current()?.estimated_remaining_seconds).toBe(600);
    await act(async () => {
      source.onmessage?.({
        data: JSON.stringify(
          makeEvent({
            index: 3,
            stage: "analysis",
            message: "分析 run",
            percent: 90,
            data: { estimated_remaining_seconds: null },
          }),
        ),
      });
    });
    expect(current()?.estimated_remaining_seconds).toBeNull();
  });

  it("进度卡显示格式化 ETA，分析阶段显示分析中", async () => {
    const running = renderJobView(
      makeJob({
        status: "running",
        stage: "optimal_control_scan",
        percent: 45,
        estimated_remaining_seconds: 3725,
      }),
    );
    expect(running.current() && document.body.textContent).toContain("预计剩余 1:02:05");
    cleanup();
    const analysing = renderJobView(
      makeJob({ status: "running", stage: "analysis", percent: 90 }),
    );
    expect(document.body.textContent).toContain("分析中");
    expect(document.body.textContent).not.toContain("预计剩余");
    cleanup();
  });

  it("排队取消后 SSE 收到 cancelled 终态", async () => {
    vi.mocked(api).mockRejectedValue(new Error("网络失败"));
    const { current, source } = renderJobView(makeJob());
    await act(async () => {
      source.onmessage?.({
        data: JSON.stringify({ done: true, status: "cancelled" }),
      });
    });
    // 拉取失败时用终态事件兜底，任务状态仍显示 cancelled。
    expect(current()?.status).toBe("cancelled");
    expect(source.closed).toBe(true);
  });
});
