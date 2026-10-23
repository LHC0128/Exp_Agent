import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./MxYRFProbeDetuningOptimizationPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => <span>采集任务状态</span> }));

const ID = "mx-y-rf-probe-detuning-optimization";
const BASE = `/api/experiments/${ID}`;
const { fields, flow } = fixture;
const mockApi = vi.mocked(api);
const parameters = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const initialLayout = () => ({ basic: fields.filter(field => field.group === "basic").map(field => field.name),
  advanced: fields.filter(field => field.group !== "basic").map(field => field.name) });
const old = { run_id: "1018_old", timestamp: "1018", run_tag: "old", completion_status: "completed" };
const other = { ...old, run_id: "1018_other" };
let current: any;
let preflightOk: boolean;
let metadata: boolean;
let savedLayout: ReturnType<typeof initialLayout>;
const makeSession = () => ({ session_id: "probe-session", job_id: "probe-job", status: "waiting", revision: 0,
  parameters: parameters(), pending_request: null, outputs: { "Y RF": "OFF", 温控: "ON" },
  steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: "pending", error: "" }])) });

beforeEach(() => {
  current = null; preflightOk = true; metadata = true; savedLayout = initialLayout();
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { id: ID, title: "Mx Y RF Probe 光功率与失谐灵敏度优化", description: "Probe/PZT 分步流程，不连接 TEC", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: savedLayout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === "/api/jobs/probe-job") return { id: "probe-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === "/api/jobs/analysis-job") return { id: "analysis-job", status: "running", events: [] } as never;
    if (url === `${BASE}/workflow`) return (metadata ? flow : { ...flow, parameter_impacts: undefined }) as never;
    if (url === `${BASE}/sessions/active`) return current as never;
    if (url === `${BASE}/sessions` && options?.method === "POST") return current = makeSession();
    if (url === `${BASE}/sessions/probe-session/steps`) {
      if (body.step_ids.includes("A07") && !body.step_ids.includes("A06")) throw new Error("缺少前置：A06 静态 PZT 与响应解调准备");
      return current = { ...current, revision: current.revision + 1,
        steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: body.step_ids.includes(stage.id) ? "completed" : "pending", error: "" }])) };
    }
    if (url === `${BASE}/sessions/probe-session/parameters`) {
      const changed = Object.keys(body.parameters).filter(key => JSON.stringify(body.parameters[key]) !== JSON.stringify(current.parameters[key]));
      const affected = new Set(changed.flatMap(key => flow.parameter_impacts.acquisition[key as keyof typeof flow.parameter_impacts.acquisition] ?? []));
      return current = { ...current, parameters: body.parameters, revision: current.revision + 1,
        steps: Object.fromEntries(Object.entries(current.steps).map(([id, state]: [string, any]) => [id,
          affected.has(id) && state.status === "completed" ? { status: "stale", error: "" } : state])) };
    }
    if (url === `${BASE}/sessions/probe-session/finish`) return current = { ...current, revision: current.revision + 1, status: "closed" };
    if (url === `${BASE}/sessions/probe-session`) return current as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old, other], total: 2 } as never;
    for (const run of [old, other]) {
      if (url === `${BASE}/runs/${run.run_id}/summary`) return { ...run, analysis_status: "not_started", analysis_error: "",
        parameters: { ...parameters(), RUN_TAG: "historical", PROBE_POWER_POINTS: 1 }, artifacts: {} } as never;
      if (url === `${BASE}/runs/${run.run_id}/analysis` && options?.method === "POST") return { id: "analysis-job", status: "running", events: [] } as never;
      if (url === `${BASE}/runs/${run.run_id}/analysis`) return { run_id: run.run_id, revision: 0,
        options: { LOW_FREQ_SKIP_HZ: run === old ? 8 : 9 },
        steps: Object.fromEntries(flow.analysis.map(stage => [stage.id, { status: "completed", error: "" }])), summary: {}, fingerprint: {} } as never;
    }
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["PZT 电压扫描起点必须小于终点"] } as never;
    if (url === `${BASE}/derive`) return { values: { grid_point_count: 21 } } as never;
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

it("真实路由加载本实验清单和原图，缺前置拒绝，选择执行与结束保留依赖语义", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "历史" })).toBeVisible();
  expect(flow.acquisition).toHaveLength(9); expect(flow.analysis).toHaveLength(2);
  for (const stage of [...flow.acquisition, ...flow.analysis]) for (const gate of stage.quality_gates) {
    expect(screen.getByText(`质量门槛：${gate}`)).toBeVisible();
  }
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: "A07 Probe/PZT 蛇形扫描与完整灵敏度采集" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: 缺少前置：A06/);
  expect(screen.getByRole("checkbox", { name: "A06 静态 PZT 与响应解调准备" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(current.revision).toBe(1));
  expect(screen.getByRole("checkbox", { name: "A09 安全结束会话" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current.status).toBe("closed"));
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
});

it("40个可见参数均归属真实节点，初始化只在设置节点，顶部操作与日志在流程容器", async () => {
  page();
  await screen.findByRole("button", { name: "创建分步会话" });
  const owned = new Set([...flow.acquisition, ...flow.analysis].flatMap(stage => stage.display_parameters));
  expect([...owned].sort()).toEqual(fields.map(field => field.name).sort());
  expect(fields).toHaveLength(40);
  for (const [id, key] of [["A04", "FIXED_PARAMS.main_magnetic_field"], ["A05", "PUMP_CARRIER_FREQUENCY_HZ"]]) {
    expect(flow.acquisition.filter(stage => stage.display_parameters.includes(key)).map(stage => stage.id)).toEqual([id]);
  }
  const button = screen.getByRole("button", { name: "保存参数与布局" });
  const top = button.closest(".xy-workflow-controls")!;
  expect(top).toContainElement(screen.getByRole("button", { name: "仅预检" }));
  expect(top).toContainElement(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(top).toContainElement(within(top as HTMLElement).getByText("采集任务状态"));
});

it("共享响应参数同步、草稿还原、分类保存回读，应用后不自动执行", async () => {
  current = makeSession();
  for (const [id, state] of Object.entries(current.steps) as [string, any][]) if (!["A08", "A09"].includes(id)) state.status = "completed";
  const view = page();
  const prepare = await node("A06");
  const acquire = await node("A07");
  const original = parameters().RESPONSE_TIME_CONSTANT_S;
  fireEvent.change(prepare.getByLabelText("响应时间常数 (s)"), { target: { value: "0.002" } });
  expect(acquire.getByLabelText("响应时间常数 (s)")).toHaveValue(0.002);
  expect(screen.getByText(/预计需重跑的已完成步骤：A06/)).toBeVisible();
  fireEvent.change(acquire.getByLabelText("响应时间常数 (s)"), { target: { value: String(original) } });
  expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument();
  fireEvent.click(prepare.getByRole("button", { name: "响应时间常数 移至基础参数" }));
  expect(acquire.getByRole("button", { name: "响应时间常数 移至高级参数" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(savedLayout.basic).toContain("RESPONSE_TIME_CONSTANT_S");
  view.unmount(); page();
  const reopened = await node("A06");
  expect(reopened.getByRole("button", { name: "响应时间常数 移至高级参数" })).toBeVisible();
  fireEvent.change(reopened.getByLabelText("响应时间常数 (s)"), { target: { value: "0.003" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(current.parameters.RESPONSE_TIME_CONSTANT_S).toBe(0.003));
  expect(current.steps.A06.status).toBe("stale");
  expect(mockApi.mock.calls.filter(([url]) => String(url).endsWith("/steps"))).toHaveLength(0);
});

it("Tab保留草稿、历史使用运行快照，填回不保存不启动，预检失败阻止完整运行", async () => {
  page();
  const tag = await node("A02");
  fireEvent.change(tag.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "填回参数" });
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByText("historical")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(tag.getByLabelText("运行标签")).toHaveValue("draft");
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("PZT 电压扫描起点必须小于终点");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(tag.getByLabelText("运行标签")).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("两个历史目录与下一次分析草稿相互隔离，保存默认值不写历史覆盖", async () => {
  page();
  const next = await node("B02");
  fireEvent.change(next.getByLabelText("平坦段搜索最低频率 (Hz)"), { target: { value: "5" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await waitFor(() => expect(screen.getByLabelText("平坦段搜索最低频率 (Hz)")).toHaveValue(8));
  fireEvent.change(screen.getByLabelText("平坦段搜索最低频率 (Hz)"), { target: { value: "11" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: other.run_id } });
  await waitFor(() => expect(screen.getByLabelText("平坦段搜索最低频率 (Hz)")).toHaveValue(9));
  fireEvent.change(screen.getByLabelText("平坦段搜索最低频率 (Hz)"), { target: { value: "12" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "" } });
  await waitFor(() => expect(screen.getByLabelText("平坦段搜索最低频率 (Hz)")).toHaveValue(5));
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await waitFor(() => expect(screen.getByLabelText("平坦段搜索最低频率 (Hz)")).toHaveValue(11));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.LOW_FREQ_SKIP_HZ).toBe(5);
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs/${old.run_id}/analysis`
    && options?.method === "POST" && JSON.parse(String(options.body)).analysis_parameters.LOW_FREQ_SKIP_HZ === 11)).toBe(true));
});

it("旧后端缺元数据时提示不兼容，仍恢复会话以安全结束", async () => {
  current = makeSession(); metadata = false;
  page();
  await screen.findByText(/接口版本过旧/);
  const end = await screen.findByRole("button", { name: "结束会话" });
  await waitFor(() => expect(end).toBeEnabled());
  fireEvent.click(end);
  await waitFor(() => expect(current.status).toBe("closed"));
});
