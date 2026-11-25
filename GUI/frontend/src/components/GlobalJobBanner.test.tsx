import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
const scriptJob = { ...job, id: "script-1", kind: "script:scripts/a/b.py", percent: null,
  estimated_remaining_seconds: null, message: "启动 b.py" };
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
  it("脚本任务显示文件名与完整路径，不伪造实验链接", () => {
    state.activity = { hardwareBusy: true, activeJobs: [scriptJob], connection: "connected" };
    renderBanner();
    expect(screen.getByText("调试脚本 · b.py")).toHaveAttribute("title", "scripts/a/b.py");
    expect(screen.getByText("进度待更新")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /前往实验/ })).not.toBeInTheDocument();
  });
  it("脚本任务取消后等待安全收尾，任务离开活动列表后按钮复原", async () => {
    state.activity = { hardwareBusy: true, activeJobs: [scriptJob], connection: "connected" };
    const view = renderBanner();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "等待脚本安全收尾…" })).toBeDisabled());
    expect(vi.mocked(api).mock.calls.some(([path, options]) => path === "/api/jobs/script-1/cancel" && options?.method === "POST")).toBe(true);
    state.activity = { hardwareBusy: true, activeJobs: [{ ...scriptJob, message: "已请求安全停止" }], connection: "connected" };
    view.rerender(<MemoryRouter><GlobalJobBanner /></MemoryRouter>);
    expect(screen.getByRole("button", { name: "等待脚本安全收尾…" })).toBeDisabled();
    state.activity = { hardwareBusy: false, activeJobs: [], connection: "connected" };
    view.rerender(<MemoryRouter><GlobalJobBanner /></MemoryRouter>);
    expect(screen.queryByRole("button", { name: "等待脚本安全收尾…" })).not.toBeInTheDocument();
    state.activity = { hardwareBusy: true, activeJobs: [scriptJob], connection: "connected" };
    view.rerender(<MemoryRouter><GlobalJobBanner /></MemoryRouter>);
    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
  });
  it("脚本取消请求失败时回到可重试的取消按钮", async () => {
    state.activity = { hardwareBusy: true, activeJobs: [scriptJob], connection: "connected" };
    renderBanner();
    vi.mocked(api).mockRejectedValueOnce(new Error("网络失败"));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("任务可能仍在运行"));
    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
  });
  it("实验任务取消仍显示正在取消…，请求结束后复原", async () => {
    const view = renderBanner();
    let finish = () => {};
    vi.mocked(api).mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(await screen.findByRole("button", { name: "正在取消…" })).toBeDisabled();
    await act(async () => { finish(); });
    await waitFor(() => expect(view.getByRole("button", { name: "取消" })).toBeEnabled());
  });
  it("脚本任务的完成与取消通知用文件名标签", () => {
    const dismiss = vi.fn();
    state.activity = { hardwareBusy: false, activeJobs: [], finishedJobs: [{ ...scriptJob, status: "completed" }], dismissFinished: dismiss };
    const view = renderBanner();
    expect(screen.getByText("调试脚本 · b.py · 已完成")).toHaveAttribute("title", "scripts/a/b.py");
    expect(screen.queryByRole("link", { name: "查看任务" })).not.toBeInTheDocument();
    state.activity = { hardwareBusy: false, activeJobs: [], finishedJobs: [{ ...scriptJob, status: "cancelled" }], dismissFinished: dismiss };
    view.rerender(<MemoryRouter><GlobalJobBanner /></MemoryRouter>);
    expect(screen.getByText("调试脚本 · b.py · 已取消")).toHaveAttribute("title", "scripts/a/b.py");
  });
});
