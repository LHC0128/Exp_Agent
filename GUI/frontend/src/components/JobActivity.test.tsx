import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import type { JobSummary } from "../types/api";
import { JobActivityProvider, useJobActivity } from "./JobActivity";

vi.mock("../api", () => ({ api: vi.fn() }));
let jobs: JobSummary[];
let online: boolean;
function Consumer() {
  const state = useJobActivity();
  return <>
    <p>{JSON.stringify(state)}</p>
    <button onClick={() => state.dismissFinished?.("job")}>关闭通知</button>
  </>;
}
const start = () => render(<JobActivityProvider><Consumer /></JobActivityProvider>);
const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(2000); });
beforeEach(() => {
  vi.useFakeTimers(); online = true;
  jobs = [{ id: "job", kind: "session:demo", status: "running", stage: "waiting", message: "等待下一步", percent: 20, created_at: "2026-10-01" }];
  vi.mocked(api).mockReset().mockImplementation(async path => {
    if (!online) throw new Error("后端离线");
    return (path === "/api/health" ? { status: "ok", hardware_busy: jobs.some(job => job.status === "running") } : jobs) as never;
  });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

it("断连保留最后任务但标记未知并阻断硬件操作，重连恢复真实状态", async () => {
  start();
  await act(async () => undefined);
  expect(screen.getByText(/"connection":"connected"/)).toBeInTheDocument();
  online = false;
  await tick();
  expect(screen.getByText(/"connection":"disconnected"/)).toHaveTextContent('"hardwareBusy":true');
  expect(screen.getByText(/"activeJobs"/)).toHaveTextContent("等待下一步");
  online = true; jobs = [];
  await tick();
  expect(screen.getByText(/"connection":"connected"/)).toHaveTextContent('"hardwareBusy":false');
  expect(screen.getByText(/"activeJobs"/)).toHaveTextContent('"activeJobs":[]');
  expect(screen.getByText(/"finishedJobs"/)).toHaveTextContent('"finishedJobs":[]');
});
it("只通知本次观察到的终态变化，可关闭；初始历史任务不弹通知", async () => {
  jobs.push({ ...jobs[0], id: "old", status: "completed" });
  start();
  await act(async () => undefined);
  expect(screen.getByText(/"finishedJobs"/)).toHaveTextContent('"finishedJobs":[]');
  jobs = [{ ...jobs[0], status: "completed" }];
  await tick();
  expect(screen.getByText(/"finishedJobs"/)).toHaveTextContent('"finishedJobs":[{"id":"job"');
  await tick();
  expect(screen.getByText(/"finishedJobs"/).textContent?.match(/"id":"job"/g)).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "关闭通知" }));
  expect(screen.getByText(/"finishedJobs"/)).toHaveTextContent('"finishedJobs":[]');
});
it("慢请求不会重叠轮询，卸载后中止请求", async () => {
  vi.mocked(api).mockImplementation(() => new Promise(() => undefined));
  const view = start();
  await tick(); await tick();
  expect(api).toHaveBeenCalledTimes(2);
  const signal = vi.mocked(api).mock.calls[0][1]?.signal;
  view.unmount();
  expect(signal?.aborted).toBe(true);
});
