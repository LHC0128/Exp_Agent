import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import type { ExperimentDefinition } from "../types/api";
import type { JobActivity } from "../components/JobActivity";
import { ExperimentCatalogPage } from "./ExperimentCatalogPage";

const activity = vi.hoisted(() => ({ value: { hardwareBusy: false, activeJobs: [] } as JobActivity }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => activity.value }));
vi.mock("../api", () => ({ api: vi.fn() }));
const mockApi = vi.mocked(api);
const FIRST: ExperimentDefinition = {
  id: "experiment-a", title: "实验甲", description: "普通实验", archived: false,
  category: "measurement", category_label: "测量", family: "test", variant: "test",
  execution_mode: "typed_workflow", acquisition_program: "experiments/a.py",
  analysis_program: null, required_devices: [], required_mapping_keys: [],
  wiring_notes: [], safety_notes: [], supports_cancel: true, can_analyze: false,
};
const SECOND: ExperimentDefinition = {
  ...FIRST, id: "experiment-b", title: "实验乙", description: "归档实验",
  archived: true, category: "calibration", category_label: "标定",
};
let items: ExperimentDefinition[];

function respond(url: string, options?: RequestInit): unknown {
  if (url === "/api/experiments") return items.map((item) => ({ ...item }));
  if (url === "/api/experiment-tags") return {
    schema_version: 3,
    archived_experiments: items.filter((item) => item.archived).map((item) => item.id),
    tags: [{ id: "measurement", label: "测量" }, { id: "calibration", label: "标定" }],
    assignments: {}, experiment_titles: {}, experiment_descriptions: {},
  };
  if (url.endsWith("/archive")) {
    const id = url.split("/")[3];
    const body = JSON.parse(String(options?.body));
    items = items.map((item) => item.id === id ? { ...item, archived: body.archived } : item);
    return { ok: true, experiment: items.find((item) => item.id === id) };
  }
  throw new Error(`未预期的接口调用：${url}`);
}

function renderCatalog() {
  return render(<MemoryRouter><ExperimentCatalogPage /></MemoryRouter>);
}

beforeEach(() => {
  window.sessionStorage.clear();
  activity.value = { hardwareBusy: false, activeJobs: [], connection: "connected" };
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  items = [{ ...FIRST }, { ...SECOND }];
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => respond(url, options) as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("实验中心可逆归档", () => {
  it("卡片不展示长介绍和脚本路径，仍能按介绍搜索", async () => {
    renderCatalog();
    await screen.findByRole("heading", { name: "实验甲" });
    expect(screen.queryByText("普通实验")).not.toBeInTheDocument();
    expect(screen.queryByText("experiments/a.py")).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索实验" }), { target: { value: "普通实验" } });
    expect(screen.getByRole("heading", { name: "实验甲" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "管理分类与实验信息" }));
    expect(screen.getByRole("textbox", { name: "实验甲介绍" })).toHaveValue("普通实验");
  });

  it("A 型卡片弱化分类、隐藏模式，运行强调且直接进入任务，管理模式不覆盖编辑控件", async () => {
    activity.value.activeJobs = [{ id: "live", kind: `session:${FIRST.id}`, status: "running", stage: "waiting", percent: 20, message: "等待下一步", created_at: "2026-10-01" }];
    renderCatalog();
    const title = await screen.findByRole("heading", { name: FIRST.title });
    expect(title.closest("article")).toHaveClass("is-running");
    expect(screen.queryByText("新模式")).not.toBeInTheDocument();
    expect(screen.queryByText("旧模式")).not.toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeVisible();
    expect(screen.getByRole("link", { name: `进入实验：${FIRST.title}` })).toHaveAttribute("href", `/experiments/${FIRST.id}?job=live`);
    fireEvent.click(screen.getByRole("button", { name: "管理分类与实验信息" }));
    expect(screen.getByRole("textbox", { name: `${FIRST.title}名称` }).closest("article")).toHaveClass("is-managing");
    expect(screen.getByRole("link", { name: `进入实验：${FIRST.title}` })).toHaveAttribute("href", `/experiments/${FIRST.id}`);
    expect(screen.getByRole("combobox")).toBeEnabled();
  });

  it("返回实验中心恢复搜索、分类与滚动位置，损坏的偏好不阻断加载", async () => {
    const first = renderCatalog();
    await screen.findByRole("heading", { name: "实验甲" });
    fireEvent.click(screen.getByRole("button", { name: "测量 1" }));
    fireEvent.change(screen.getByRole("textbox", { name: "搜索实验" }), { target: { value: "普通实验" } });
    vi.spyOn(window, "scrollY", "get").mockReturnValue(420);
    fireEvent.scroll(window);
    first.unmount();
    const second = renderCatalog();
    await screen.findByRole("heading", { name: "实验甲" });
    expect(screen.getByRole("textbox", { name: "搜索实验" })).toHaveValue("普通实验");
    expect(screen.getByRole("button", { name: "测量 1" })).toHaveClass("active");
    expect(window.scrollTo).toHaveBeenLastCalledWith({ top: 420 });
    second.unmount();
    window.sessionStorage.setItem("exp-agent:catalog-view", "{broken");
    renderCatalog();
    await screen.findByRole("heading", { name: "实验甲" });
    expect(screen.getByRole("textbox", { name: "搜索实验" })).toHaveValue("");
  });
  it("普通列表、分类和搜索排除归档，归档入口可独立搜索但只显示卡片", async () => {
    renderCatalog();
    expect(await screen.findByRole("heading", { name: "实验甲" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "实验乙" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "测量 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "标定 0" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索实验" }), { target: { value: "实验乙" } });
    expect(screen.getByText("未找到匹配的实验。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "已归档 1" }));
    expect(screen.getByRole("heading", { name: "实验乙" })).toBeInTheDocument();
    expect(screen.getByText("标定 · 已归档")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /进入实验/ })).not.toBeInTheDocument();
    expect(screen.getByText("仅保留卡片")).toBeVisible();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索实验" }), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "标定 0" }));
    expect(screen.queryByRole("heading", { name: "实验乙" })).not.toBeInTheDocument();
  });

  it("逐项归档和恢复更新计数，全部归档后提示空状态，恢复后保留原分类", async () => {
    renderCatalog();
    await screen.findByRole("heading", { name: "实验甲" });
    fireEvent.click(screen.getByRole("button", { name: "管理分类与实验信息" }));
    fireEvent.click(screen.getByRole("button", { name: "归档实验甲" }));
    await screen.findByText("“实验甲”已归档");
    expect(screen.getByRole("button", { name: "全部 0" })).toBeInTheDocument();
    expect(screen.getByText(/当前分类没有未归档实验/)).toBeInTheDocument();
    expect(mockApi).toHaveBeenCalledWith("/api/experiments/experiment-a/archive", {
      method: "PUT", body: JSON.stringify({ archived: true }),
    });
    fireEvent.click(screen.getByRole("button", { name: "已归档 2" }));
    fireEvent.click(screen.getByRole("button", { name: "恢复实验甲" }));
    await screen.findByText("“实验甲”已恢复到原分类");
    expect(screen.getByRole("button", { name: "测量 1" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "测量 1" }));
    expect(screen.getByRole("textbox", { name: "实验甲名称" })).toHaveValue("实验甲");
    expect(screen.getByRole("combobox")).toHaveValue("measurement");
    expect(mockApi).toHaveBeenCalledWith("/api/experiments/experiment-a/archive", {
      method: "PUT", body: JSON.stringify({ archived: false }),
    });
  });

  it("保存失败不移除卡片或改变计数", async () => {
    renderCatalog();
    await screen.findByRole("heading", { name: "实验甲" });
    fireEvent.click(screen.getByRole("button", { name: "管理分类与实验信息" }));
    mockApi.mockRejectedValueOnce(new Error("磁盘无法写入"));
    fireEvent.click(screen.getByRole("button", { name: "归档实验甲" }));
    await screen.findByText(/磁盘无法写入/);
    expect(screen.getByRole("textbox", { name: "实验甲名称" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "归档实验甲" })).toBeEnabled();
  });

  it("等待服务器确认期间不乐观移除，并禁用重复或其他目录写操作", async () => {
    renderCatalog();
    await screen.findByRole("heading", { name: "实验甲" });
    fireEvent.click(screen.getByRole("button", { name: "管理分类与实验信息" }));
    let resolve!: (value: unknown) => void;
    mockApi.mockReturnValueOnce(new Promise((done) => { resolve = done; }) as never);
    const button = screen.getByRole("button", { name: "归档实验甲" });
    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "实验甲名称" })).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("新标签名称"), { target: { value: "新标签" } });
    expect(screen.getByRole("button", { name: "添加标签" })).toBeDisabled();
    expect(screen.getByRole("combobox")).toBeDisabled();
    fireEvent.click(button);
    expect(mockApi.mock.calls.filter(([url]) => url.endsWith("/archive"))).toHaveLength(1);
    await act(async () => resolve({ experiment: { ...FIRST, archived: true } }));
    expect(screen.getByRole("button", { name: "全部 0" })).toBeInTheDocument();
  });

  it("代码已移走的归档卡片显示路径且没有一键恢复", async () => {
    items[1] = { ...SECOND, archive_path: "old/experiments" };
    renderCatalog();
    await screen.findByRole("heading", { name: "实验甲" });
    fireEvent.click(screen.getByRole("button", { name: "已归档 1" }));
    expect(screen.getByText("old/experiments")).toBeVisible();
    expect(screen.queryByRole("link", { name: /进入实验/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "管理分类与实验信息" }));
    expect(screen.getByText("恢复前需手动接回代码")).toBeVisible();
    expect(screen.queryByRole("button", { name: "恢复实验乙" })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "实验乙介绍" })).toBeEnabled();
  });

  it("归档列表为空时明确提示", async () => {
    items = [{ ...FIRST }];
    renderCatalog();
    await screen.findByRole("heading", { name: "实验甲" });
    fireEvent.click(screen.getByRole("button", { name: "已归档 0" }));
    expect(screen.getByText("暂无已归档实验。")).toBeInTheDocument();
  });

  it("加载中不显示空目录，失败可原地重试", async () => {
    let reject!: (reason: Error) => void;
    mockApi.mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }) as never);
    renderCatalog();
    expect(screen.getByText("正在加载实验目录…")).toBeInTheDocument();
    expect(screen.queryByText(/当前分类没有未归档实验/)).not.toBeInTheDocument();
    await act(async () => reject(new Error("后端暂不可用")));
    expect(screen.getByRole("alert")).toHaveTextContent("后端暂不可用");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByRole("heading", { name: "实验甲" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("StrictMode 清理后的旧请求不能覆盖最新目录", async () => {
    let resolve!: (value: unknown) => void;
    const stale = new Promise((done) => { resolve = done; });
    let lists = 0;
    mockApi.mockImplementation(async (url, options) => {
      if (url === "/api/experiments" && ++lists === 1) return stale as never;
      return respond(url, options) as never;
    });
    render(<StrictMode><MemoryRouter><ExperimentCatalogPage /></MemoryRouter></StrictMode>);
    await screen.findByRole("heading", { name: "实验甲" });
    const firstSignal = mockApi.mock.calls.find(([url]) => url === "/api/experiments")?.[1]?.signal;
    expect(firstSignal?.aborted).toBe(true);
    await act(async () => resolve([{ ...FIRST, title: "过期目录" }]));
    await waitFor(() => expect(screen.getByRole("heading", { name: "实验甲" })).toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: "过期目录" })).not.toBeInTheDocument();
  });
});
