import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import { useJobActivity } from "../components/JobActivity";
import { OverviewPage } from "./OverviewPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: vi.fn() }));

const mockApi = vi.mocked(api);
const mockUseJobActivity = vi.mocked(useJobActivity);

const DEVICES = [
  { name: "dg4000", label: "DG4000", kind: "signal_generator" },
  { name: "hf2", label: "HF2", kind: "lockin_amplifier" },
];

beforeEach(() => {
  mockUseJobActivity.mockReturnValue({ hardwareBusy: false, activeJobs: [] });
  mockApi.mockReset();
  mockApi.mockImplementation(async (url) => {
    if (url === "/api/health") return { status: "ok", instance_id: "instance-1", hardware_busy: false } as never;
    if (url === "/api/devices") return DEVICES as never;
    throw new Error(`未预期的接口调用: ${url}`);
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("总览页", () => {
  it("展示系统状态与已映射设备数量", async () => {
    render(<OverviewPage />);
    expect(await screen.findByText("系统已就绪")).toBeInTheDocument();
    expect(screen.getByText("可以开始读取设备或执行实验")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("2")).toBeInTheDocument());
    expect(screen.getByText("已映射设备")).toBeInTheDocument();
  });

  it("后端繁忙时切换硬件状态文案并禁用重启", async () => {
    mockApi.mockImplementation(async (url) => {
      if (url === "/api/health") return { status: "ok", instance_id: "instance-1", hardware_busy: true } as never;
      if (url === "/api/devices") return DEVICES as never;
      throw new Error(`未预期的接口调用: ${url}`);
    });
    render(<OverviewPage />);
    expect(await screen.findByText("设备任务运行中")).toBeInTheDocument();
    expect(screen.getByText("当前锁定其他硬件写入")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重启后端" })).toBeDisabled();
  });

  it("活动分析任务期间禁用重启", async () => {
    mockUseJobActivity.mockReturnValue({
      hardwareBusy: false,
      activeJobs: [{ id: "job-1", kind: "analysis:demo", status: "running" } as never],
    });
    render(<OverviewPage />);
    expect(await screen.findByRole("button", { name: "重启后端" })).toBeDisabled();
  });

  it("取消确认时不请求重启接口", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<OverviewPage />);
    const button = await screen.findByRole("button", { name: "重启后端" });
    button.click();
    expect(confirm).toHaveBeenCalled();
    expect(mockApi).not.toHaveBeenCalledWith("/api/restart", { method: "POST" });
    confirm.mockRestore();
  });

  it("检测到新实例后刷新设备并提示恢复", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let healthCalls = 0;
    mockApi.mockImplementation(async (url) => {
      if (url === "/api/health") {
        healthCalls += 1;
        return {
          status: "ok",
          instance_id: healthCalls > 1 ? "instance-2" : "instance-1",
          hardware_busy: false,
        } as never;
      }
      if (url === "/api/devices") return DEVICES as never;
      if (url === "/api/restart") return { status: "restarting" } as never;
      throw new Error(`未预期的接口调用: ${url}`);
    });
    render(<OverviewPage />);
    const button = await screen.findByRole("button", { name: "重启后端" });
    button.click();
    expect(await screen.findByText("后端已重启，页面已恢复。", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(mockApi).toHaveBeenCalledWith("/api/restart", { method: "POST" });
  });

  it("后端拒绝重启时展示错误", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockApi.mockImplementation(async (url) => {
      if (url === "/api/health") return { status: "ok", instance_id: "instance-1", hardware_busy: false } as never;
      if (url === "/api/devices") return DEVICES as never;
      if (url === "/api/restart") throw Object.assign(new Error("存在未结束的任务"), { code: "restart_busy" });
      throw new Error(`未预期的接口调用: ${url}`);
    });
    render(<OverviewPage />);
    (await screen.findByRole("button", { name: "重启后端" })).click();
    expect(await screen.findByText("存在未结束的任务")).toBeInTheDocument();
  });

  it("保留系统状态并撤下实验系统架构及打开链接", async () => {
    render(<OverviewPage />);
    expect(await screen.findByText("系统已就绪")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "实验系统架构" })).not.toBeInTheDocument();
    expect(screen.queryByTitle("实验系统架构交互图")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "在新页面打开" })).not.toBeInTheDocument();
  });

  it("移除旧的常用入口且不新增实验或仪器接口请求", async () => {
    render(<OverviewPage />);
    await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/api/devices"));
    expect(screen.queryByText("常用入口")).not.toBeInTheDocument();
    expect(screen.queryByText("QUICK START")).not.toBeInTheDocument();
    expect(screen.queryByText("读取仪器状态")).not.toBeInTheDocument();
    expect(screen.queryByText("系统准备")).not.toBeInTheDocument();
    expect(screen.queryByText("进入实验中心")).not.toBeInTheDocument();
    const requested = mockApi.mock.calls.map(([url]) => url);
    expect([...new Set(requested)].sort()).toEqual(["/api/devices", "/api/health"]);
  });
});
