import type { ReactNode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const routes = vi.hoisted(() => {
  let resolveTools!: () => void;
  const toolsReady = new Promise<void>((resolve) => { resolveTools = resolve; });
  return { loaded: [] as string[], toolsReady, resolveTools };
});

vi.mock("../pages/ExperimentCatalogPage", () => ({ ExperimentCatalogPage: () => <h1>测试实验中心</h1> }));
vi.mock("../pages/OverviewPage", () => {
  routes.loaded.push("overview");
  return { OverviewPage: () => <h1>测试总览</h1> };
});
vi.mock("../pages/ExperimentPage", () => {
  routes.loaded.push("experiment");
  return { ExperimentPage: () => <h1>测试实验配置 {useParams().experimentId}</h1> };
});
vi.mock("../pages/RunsPage", () => {
  routes.loaded.push("runs");
  return { RunsPage: () => <h1>测试历史</h1> };
});
vi.mock("../pages/ToolsPage", async () => {
  routes.loaded.push("tools");
  await routes.toolsReady;
  return { ToolsPage: () => <h1>测试功能模块</h1> };
});
vi.mock("../pages/InstrumentsPage", () => {
  routes.loaded.push("instruments");
  throw new Error("模拟页面模块加载失败");
});
vi.mock("./JobActivity", () => ({ JobActivityProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock("./GlobalJobBanner", () => ({ GlobalJobBanner: () => <p>全局任务仍可见</p> }));

import { Layout } from "./Layout";

function renderLayout(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><Layout /></MemoryRouter>);
}

afterEach(() => { cleanup(); routes.loaded.length = 0; vi.restoreAllMocks(); });

describe("路由按需加载", () => {
  it("直接进入实验中心不加载其他路由页面", () => {
    renderLayout("/experiments");
    expect(screen.getByRole("heading", { name: "测试实验中心" })).toBeInTheDocument();
    expect(routes.loaded).toEqual([]);
    expect(screen.getByText("全局任务仍可见")).toBeInTheDocument();
  });

  it("导航到其他页面才加载对应模块", async () => {
    renderLayout("/experiments");
    fireEvent.click(screen.getByRole("link", { name: /总览/ }));
    expect(await screen.findByRole("heading", { name: "测试总览" })).toBeInTheDocument();
    expect(routes.loaded).toEqual(["overview"]);
    fireEvent.click(screen.getByRole("link", { name: /实验中心/ }));
    expect(screen.getByRole("heading", { name: "测试实验中心" })).toBeInTheDocument();
  });

  it("加载提示限定在内容区，导航和全局任务保持可用", async () => {
    renderLayout("/tools");
    expect(screen.getByText("正在加载页面…")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /实验中心/ })).toBeInTheDocument();
    expect(screen.getByText("全局任务仍可见")).toBeInTheDocument();
    await act(async () => routes.resolveTools());
    expect(await screen.findByRole("heading", { name: "测试功能模块" })).toBeInTheDocument();
  });

  it("模块加载失败可刷新，也可继续导航回实验中心", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    renderLayout("/instruments");
    expect(await screen.findByRole("alert")).toHaveTextContent("页面加载失败");
    expect(screen.getByRole("button", { name: "刷新页面" })).toBeInTheDocument();
    expect(screen.getByText("全局任务仍可见")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: /实验中心/ }));
    expect(screen.getByRole("heading", { name: "测试实验中心" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("保留旧实验地址的重定向", async () => {
    renderLayout("/experiment");
    expect(await screen.findByRole("heading", { name: "测试实验配置 static-sensitivity" })).toBeInTheDocument();
  });
});
