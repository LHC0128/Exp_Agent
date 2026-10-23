import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { JobActivity } from "./JobActivity";
import { GlobalJobBanner } from "./GlobalJobBanner";

const state = vi.hoisted(() => ({ activity: {} as JobActivity }));
vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("./JobActivity", () => ({ useJobActivity: () => state.activity }));
const job = { id: "job-1", kind: "experiment:demo", status: "running" as const, stage: "scan", percent: 45,
  estimated_remaining_seconds: 3725, message: "最优控制扫描", created_at: "2026-09-13T10:00:00" };
const renderBanner = () => render(<MemoryRouter><GlobalJobBanner /></MemoryRouter>);
beforeEach(() => {
  state.activity = { hardwareBusy: true, activeJobs: [job], connection: "connected" };
  vi.mocked(api).mockReset().mockResolvedValue({ title: "演示实验" });
});
afterEach(cleanup);

describe("全局任务导航", () => {
  it("横幅显示具体实验、百分比和 ETA，链接不发起采集", async () => {
    renderBanner();
    expect(screen.getByText("45%")).toBeInTheDocument();
    expect(screen.getByText("预计剩余 1:02:05")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "前往实验：实验运行 · 演示实验" })).toHaveAttribute("href", "/experiments/demo?job=job-1");
    expect(vi.mocked(api).mock.calls.every(([, options]) => !options?.method)).toBe(true);
  });
  it("分步会话、排队和分析均提供正确实验链接，多任务可展开收起", async () => {
    state.activity.activeJobs = [
      { ...job, kind: "session:demo" },
      { ...job, id: "queued", kind: "experiment:other", status: "queued" },
      { ...job, id: "analysis", kind: "analysis:demo:1010_测试" },
    ];
    renderBanner();
    expect(screen.getAllByRole("link")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "展开全部 3 个任务" }));
    await screen.findByText("排队中");
    expect(screen.getAllByRole("link").map(link => link.getAttribute("href"))).toEqual([
      "/experiments/demo?job=job-1", "/experiments/demo?job=analysis", "/experiments/other?job=queued",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "收起任务列表" }));
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });
  it("断连明确标记未知并禁用取消；取消失败保留可重试错误", async () => {
    state.activity.connection = "disconnected";
    const view = renderBanner();
    expect(screen.getByRole("alert")).toHaveTextContent("任务与仪器状态未知");
    expect(screen.getByText("状态未知")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    state.activity.connection = "connected";
    view.rerender(<MemoryRouter><GlobalJobBanner /></MemoryRouter>);
    vi.mocked(api).mockRejectedValueOnce(new Error("网络失败"));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("任务可能仍在运行"));
    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
  });
  it("结束通知可查看和关闭，非实验任务没有伪造的实验链接", async () => {
    const dismiss = vi.fn();
    state.activity = { hardwareBusy: false, activeJobs: [{ ...job, kind: "clock-sync" }],
      finishedJobs: [{ ...job, id: "finished", status: "failed" }], dismissFinished: dismiss };
    renderBanner();
    expect(screen.getByText("时钟同步")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /前往实验/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看任务" })).toHaveAttribute("href", "/experiments/demo?job=finished");
    fireEvent.click(screen.getByRole("button", { name: "关闭任务通知 finished" }));
    expect(dismiss).toHaveBeenCalledWith("finished");
  });
});
