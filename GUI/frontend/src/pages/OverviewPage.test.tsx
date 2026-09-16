import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import { OverviewPage } from "./OverviewPage";

vi.mock("../api", () => ({ api: vi.fn() }));

const mockApi = vi.mocked(api);

const DEVICES = [
  { name: "dg4000", label: "DG4000", kind: "signal_generator" },
  { name: "hf2", label: "HF2", kind: "lockin_amplifier" },
];

beforeEach(() => {
  mockApi.mockReset();
  mockApi.mockImplementation(async (url) => {
    if (url === "/api/health") return { status: "ok", hardware_busy: false } as never;
    if (url === "/api/devices") return DEVICES as never;
    throw new Error(`未预期的接口调用: ${url}`);
  });
});

afterEach(() => {
  cleanup();
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

  it("后端繁忙时切换硬件状态文案", async () => {
    mockApi.mockImplementation(async (url) => {
      if (url === "/api/health") return { status: "ok", hardware_busy: true } as never;
      if (url === "/api/devices") return DEVICES as never;
      throw new Error(`未预期的接口调用: ${url}`);
    });
    render(<OverviewPage />);
    expect(await screen.findByText("设备任务运行中")).toBeInTheDocument();
    expect(screen.getByText("当前锁定其他硬件写入")).toBeInTheDocument();
  });

  it("架构工作台包含四阶段流程带与语义标识", async () => {
    render(<OverviewPage />);
    for (const stage of ["参数配置", "仪器采集", "原始数据", "离线分析"]) {
      expect(screen.getByText(stage)).toBeInTheDocument();
    }
    expect(screen.getByText("安全边界")).toBeInTheDocument();
    expect(screen.getByText("兼容路径")).toBeInTheDocument();
  });

  it("以 iframe 隔离架构图并提供无障碍标题", async () => {
    render(<OverviewPage />);
    const frame = screen.getByTitle("实验系统架构交互图");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame).toHaveAttribute("src", "/architecture/system-architecture.html");
    expect(frame).toHaveAttribute("loading", "lazy");
    expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-same-origin allow-downloads allow-popups");
    expect(frame).toHaveAttribute("allow", "clipboard-write");
  });

  it("提供在新页面打开架构图的独立链接", async () => {
    render(<OverviewPage />);
    const link = screen.getByRole("link", { name: "在新页面打开" });
    expect(link).toHaveAttribute("href", "/architecture/system-architecture.html");
    expect(link).toHaveAttribute("target", "_blank");
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
