import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./ProjectionNoisePage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => <span>采集任务状态</span> }));

const ID = "projection-noise";
const BASE = `/api/experiments/${ID}`;
const { fields, flow } = fixture;
const mockApi = vi.mocked(api);
const parameters = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const initialLayout = () => ({ basic: fields.filter(field => field.group === "basic").map(field => field.name),
  advanced: fields.filter(field => field.group !== "basic").map(field => field.name) });
const old = { run_id: "1024_old", timestamp: "1024", run_tag: "old", completion_status: "completed" };
const other = { ...old, run_id: "1024_other" };
let current: any;
let preflightOk: boolean;
let metadata: boolean;
let legacy: boolean;
let savedLayout: ReturnType<typeof initialLayout>;
const makeSession = () => ({ session_id: "projection-session", job_id: "projection-job", status: "waiting", revision: 0,
  parameters: parameters(), pending_request: null,
  outputs: { "Pump 载波": "保持当前状态（本实验不写入）", "Pump 门控": "保持当前状态（本实验不写入）", 温控: "ON" },
  steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: "pending", error: "" }])) });

beforeEach(() => {
  current = null; preflightOk = true; metadata = true; legacy = false; savedLayout = initialLayout();
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { id: ID, title: "原子自旋投影噪声", description: "SDS 分步流程；Pump 载波/门控全程不修改", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: savedLayout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === "/api/jobs/projection-job") return { id: "projection-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === "/api/jobs/analysis-job") return { id: "analysis-job", status: "running", events: [] } as never;
    if (url === `${BASE}/workflow`) return (metadata ? flow : { ...flow, parameter_impacts: undefined }) as never;
    if (url === `${BASE}/sessions/active`) return current as never;
    if (url === `${BASE}/sessions` && options?.method === "POST") return current = makeSession();
    if (url === `${BASE}/sessions/projection-session/steps`) {
      if (body.step_ids.includes("A07") && !body.step_ids.includes("A06")) throw new Error("缺少前置：A06 SDS 固定采样率与量程回读");
      return current = { ...current, revision: current.revision + 1,
        steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: body.step_ids.includes(stage.id) ? "completed" : "pending", error: "" }])) };
    }
    if (url === `${BASE}/sessions/projection-session/parameters`) {
      const changed = Object.keys(body.parameters).filter(key => JSON.stringify(body.parameters[key]) !== JSON.stringify(current.parameters[key]));
      const affected = new Set(changed.flatMap(key => flow.parameter_impacts.acquisition[key as keyof typeof flow.parameter_impacts.acquisition] ?? []));
      return current = { ...current, parameters: body.parameters, revision: current.revision + 1,
        steps: Object.fromEntries(Object.entries(current.steps).map(([id, state]: [string, any]) => [id,
          affected.has(id) && state.status === "completed" ? { status: "stale", error: "" } : state])) };
    }
    if (url === `${BASE}/sessions/projection-session/finish`) return current = { ...current, revision: current.revision + 1, status: "closed" };
    if (url === `${BASE}/sessions/projection-session`) return current as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old, other], total: 2 } as never;
    for (const run of [old, other]) {
      if (url === `${BASE}/runs/${run.run_id}/summary`) return { ...run, analysis_status: "not_started", analysis_error: "",
        parameters: { ...parameters(), RUN_TAG: "historical", ACQ_REPEATS: 7 }, artifacts: {} } as never;
      if (url === `${BASE}/runs/${run.run_id}/analysis` && options?.method === "POST") return { id: "analysis-job", status: "running", events: [] } as never;
      if (url === `${BASE}/runs/${run.run_id}/analysis`) return { run_id: run.run_id, revision: 0,
        options: legacy ? {} : { FIT_HALF_WIDTH_HZ: run === old ? 800 : 900, WELCH_NPERSEG: 256 },
        steps: Object.fromEntries(flow.analysis.map(stage => [stage.id, { status: "completed", error: "" }])), summary: {}, fingerprint: {} } as never;
    }
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["主场标定来源未成功"] } as never;
    if (url === `${BASE}/derive`) return { values: {} } as never;
    if (url === `${BASE}/defaults`) {
      savedLayout = body.parameter_layout;
      return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: savedLayout } } as never;
    }
    throw new Error(`未模拟接口 ${url}`);
  });
});
afterEach(cleanup);

const page = () => render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}>
  <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
</MemoryRouter>);
const node = async (id: string) => {
  const stage = [...flow.acquisition, ...flow.analysis].find(stage => stage.id === id)!;
  const region = await screen.findByRole("region", { name: `${id} ${stage.title} 参数`, hidden: true });
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  for (const advanced of region.querySelectorAll<HTMLDetailsElement>("details")) advanced.open = true;
  return within(region);
};

it("真实路由加载9采集2分析，缺前置不补选，可执行及结束会话并下载原图", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "历史" })).toBeVisible();
  expect(flow.acquisition).toHaveLength(9); expect(flow.analysis).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: "A07 可选 OFF 背景与 ON 自旋噪声采集" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: 缺少前置：A06/);
  expect(screen.getByRole("checkbox", { name: "A06 SDS 固定采样率与量程回读" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(current.revision).toBe(1));
  expect(screen.getByRole("checkbox", { name: "A09 安全结束会话" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current.status).toBe("closed"));
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
});

it("16个可见参数均有节点入口，初始化光功率只在A04，顶部操作与任务在流程容器", async () => {
  page(); await screen.findByRole("button", { name: "创建分步会话" });
  const owned = new Set([...flow.acquisition, ...flow.analysis].flatMap(stage => stage.display_parameters));
  expect([...owned].sort()).toEqual(fields.map(field => field.name).sort());
  expect(fields).toHaveLength(16);
  expect(flow.acquisition.filter(s => s.display_parameters.includes("FIXED_PARAMS.Pump_laser_power")).map(s => s.id)).toEqual(["A04"]);
  expect(fields.some(field => /CARRIER|GATE/.test(field.name))).toBe(false);
  const top = screen.getByRole("button", { name: "保存参数与布局" }).closest(".xy-workflow-controls")!;
  expect(top).toContainElement(screen.getByRole("button", { name: "仅预检" }));
  expect(top).toContainElement(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(top).toContainElement(within(top as HTMLElement).getByText("采集任务状态"));
});

it("共享Welch同步、草稿还原、分类保存回读，应用后失效范围来自清单且不自动运行", async () => {
  current = makeSession();
  for (const [id, state] of Object.entries(current.steps) as [string, any][]) if (!["A08", "A09"].includes(id)) state.status = "completed";
  const view = page();
  const prepare = await node("A06"), analyze = await node("B02");
  const original = parameters().WELCH_NPERSEG;
  fireEvent.change(prepare.getByLabelText("Welch 每段点数"), { target: { value: "256" } });
  expect(analyze.getByLabelText("Welch 每段点数")).toHaveValue(256);
  expect(screen.getByText(/预计需重跑的已完成步骤：A06/)).toBeVisible();
  fireEvent.change(analyze.getByLabelText("Welch 每段点数"), { target: { value: String(original) } });
  expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument();
  fireEvent.click(prepare.getByRole("button", { name: "Welch 每段点数 移至基础参数" }));
  expect(analyze.getByRole("button", { name: "Welch 每段点数 移至高级参数" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(savedLayout.basic).toContain("WELCH_NPERSEG");
  view.unmount(); page();
  const reopened = await node("A06");
  expect(reopened.getByRole("button", { name: "Welch 每段点数 移至高级参数" })).toBeVisible();
  fireEvent.change(reopened.getByLabelText("Welch 每段点数"), { target: { value: "512" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(current.parameters.WELCH_NPERSEG).toBe(512));
  expect(current.steps.A06.status).toBe("stale");
  expect(mockApi.mock.calls.filter(([url]) => String(url).endsWith("/steps"))).toHaveLength(0);
});

it("Tab保留草稿、历史快照填回不保存不启动，预检失败阻止完整运行", async () => {
  page(); const tag = await node("A02");
  fireEvent.change(tag.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "填回参数" });
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByText("historical")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(tag.getByLabelText("运行标签")).toHaveValue("draft");
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("主场标定来源未成功");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(tag.getByLabelText("运行标签")).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("两个历史目录与下一次分析草稿隔离，保存默认值不写历史覆盖", async () => {
  page(); await node("B02");
  const fitWidth = () => within(screen.getByRole("region", {
    name: "B02 Welch、洛伦兹诊断与结果图 参数", hidden: true,
  })).getByLabelText("拟合频带半宽 (Hz)");
  fireEvent.change(fitWidth(), { target: { value: "500" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await waitFor(() => expect(fitWidth()).toHaveValue(800));
  fireEvent.change(fitWidth(), { target: { value: "810" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: other.run_id } });
  await waitFor(() => expect(fitWidth()).toHaveValue(900));
  fireEvent.change(fitWidth(), { target: { value: "910" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "" } });
  await waitFor(() => expect(fitWidth()).toHaveValue(500));
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await waitFor(() => expect(fitWidth()).toHaveValue(810));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.FIT_HALF_WIDTH_HZ).toBe(500);
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs/${old.run_id}/analysis`
    && options?.method === "POST" && JSON.parse(String(options.body)).analysis_parameters.FIT_HALF_WIDTH_HZ === 810)).toBe(true));
});

it("旧HF2目录不显示或提交SDS分析覆盖值，仍可选择真实兼容分析节点", async () => {
  legacy = true; page(); await node("B02");
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await waitFor(() => expect(screen.queryByRole("region", {
    name: "B02 Welch、洛伦兹诊断与结果图 参数", hidden: true,
  })).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs/${old.run_id}/analysis`
    && options?.method === "POST" && JSON.stringify(JSON.parse(String(options.body)).analysis_parameters) === "{}")).toBe(true));
});

it("旧后端缺元数据时仍可恢复现有会话并安全结束", async () => {
  current = makeSession(); metadata = false; page();
  await screen.findByText(/接口版本过旧/);
  const end = await screen.findByRole("button", { name: "结束会话" });
  await waitFor(() => expect(end).toBeEnabled());
  fireEvent.click(end);
  await waitFor(() => expect(current.status).toBe("closed"));
});
