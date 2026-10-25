import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./StaticSensitivityOptimizeLocalPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => <span>采集任务状态</span> }));
const ID = "static-sensitivity-optimize-local", BASE = `/api/experiments/${ID}`;
const { fields, flow } = fixture;
const mockApi = vi.mocked(api);
const parameters = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const initialLayout = () => ({ basic: fields.filter(field => field.group === "basic").map(field => field.name),
  advanced: fields.filter(field => field.group !== "basic").map(field => field.name) });
const old = { run_id: "0709_0951_opt_local", timestamp: "0709_0951", run_tag: "historical", completion_status: "completed" };
const other = { ...old, run_id: "1024_new" };
let current: any, metadata: boolean, preflightOk: boolean, applyOk: boolean;
let layout: ReturnType<typeof initialLayout>, savedValues: Record<string, any>;
const session = () => ({ session_id: "local-session", job_id: "local-job", status: "waiting", revision: 0,
  parameters: parameters(), pending_request: null, outputs: { Z: "OFF", 温控: "ON" },
  steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: "pending", error: "" }])) });

beforeEach(() => {
  current = null; metadata = preflightOk = applyOk = true; layout = initialLayout(); savedValues = parameters();
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { id: ID, title: "静磁场灵敏度局部优化", description: "DC 逐点与基准重复，最佳点仅作建议", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields: fields.map(field => ({ ...field, default: savedValues[field.name] })), parameter_layout_saved: true, parameter_layout: layout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === "/api/jobs/local-job") return { id: "local-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === "/api/jobs/analysis-job") return { id: "analysis-job", status: "running", events: [] } as never;
    if (url === `${BASE}/workflow`) return (metadata ? flow : { ...flow, parameter_impacts: undefined }) as never;
    if (url === `${BASE}/sessions/active`) return current as never;
    if (url === `${BASE}/sessions` && options?.method === "POST") return current = session();
    if (url === `${BASE}/sessions/local-session`) return current as never;
    if (url === `${BASE}/sessions/local-session/steps`) {
      if (body.step_ids.includes("A04") && !body.step_ids.includes("A03")) throw new Error("缺少前置：A03 连接仪器、同步时钟与回读");
      return current = { ...current, revision: current.revision + 1,
        steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: body.step_ids.includes(stage.id) ? "completed" : "pending", error: "" }])) };
    }
    if (url === `${BASE}/sessions/local-session/parameters`) {
      if (!applyOk) throw new Error("参数应用失败");
      const changed = Object.keys(body.parameters).filter(key => JSON.stringify(body.parameters[key]) !== JSON.stringify(current.parameters[key]));
      const affected = new Set(changed.flatMap(key => flow.parameter_impacts.acquisition[key as keyof typeof flow.parameter_impacts.acquisition] ?? []));
      return current = { ...current, parameters: body.parameters, revision: current.revision + 1,
        steps: Object.fromEntries(Object.entries(current.steps).map(([id, state]: [string, any]) => [id,
          affected.has(id) && state.status === "completed" ? { status: "stale", error: "" } : state])) };
    }
    if (url === `${BASE}/sessions/local-session/finish`) return current = { ...current, status: "closed", revision: current.revision + 1 };
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old, other], total: 2 } as never;
    for (const run of [old, other]) {
      if (url === `${BASE}/runs/${run.run_id}/summary`) return { ...run, analysis_status: "completed", analysis_error: "",
        parameters: { RUN_TAG_PREFIX: "historical", OPTIMIZE_SEQUENCE: savedValues.OPTIMIZE_SEQUENCE, BASELINE_PARAMS: savedValues.BASELINE_PARAMS }, artifacts: {},
        optimization_summary: { source: run === old ? "legacy_existing_summary" : "frozen_point_analysis", baseline_median: 100, baseline_drift_ratio: .1, recommendation: "维持当前基准" } } as never;
      if (url === `${BASE}/runs/${run.run_id}/analysis` && options?.method === "POST") return { id: "analysis-job", status: "running", events: [] } as never;
      if (url === `${BASE}/runs/${run.run_id}/analysis`) return { run_id: run.run_id, revision: 0, options: {}, fingerprint: {}, summary: {},
        steps: Object.fromEntries(flow.analysis.map(stage => [stage.id, { status: "pending", error: "" }])) } as never;
    }
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["候选值越界，禁止启动"] } as never;
    if (url === `${BASE}/derive`) return { values: { 候选点数量: 15 } } as never;
    if (url === `${BASE}/defaults`) {
      layout = body.parameter_layout; savedValues = body.parameters;
      return { message: "已保存", schema: { fields: fields.map(field => ({ ...field, default: savedValues[field.name] })), parameter_layout_saved: true, parameter_layout: layout } } as never;
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

it("真实路由6采集3分析，全部参数按职责出现，顶部保存预检及任务状态", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "历史" })).toBeVisible();
  expect(flow.acquisition).toHaveLength(6); expect(flow.analysis).toHaveLength(3);
  const shown = new Set([...flow.acquisition, ...flow.analysis].flatMap(stage => stage.display_parameters));
  expect(fields.every(field => shown.has(field.name))).toBe(true);
  expect(flow.acquisition.filter(stage => stage.display_parameters.includes("BASELINE_PARAMS"))).toHaveLength(1);
  const plan = await node("A02"), scan = await node("A04");
  expect(plan.getByLabelText("批次标签")).toBeVisible();
  expect(scan.getByLabelText("色散每点采集时间 (s)")).toBeVisible();
  const top = screen.getByRole("button", { name: "保存参数与布局" }).closest(".xy-workflow-controls") as HTMLElement;
  expect(top).toContainElement(screen.getByRole("button", { name: "仅预检" }));
  expect(top).toContainElement(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(within(top).getByText("采集任务状态")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
});

it("选择缺前置不补选，按清单执行并安全结束", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: "A04 逐候选校相、DC 色散与 Z OFF 噪声" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: 缺少前置：A03/);
  expect(screen.getByRole("checkbox", { name: "A03 连接仪器、同步时钟与回读" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(current.revision).toBe(1));
  expect(screen.getByRole("checkbox", { name: "A06 安全结束会话" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current.status).toBe("closed"));
});

it("多参数草稿只预览，应用失败保留，成功后显式重跑且不用新草稿收尾", async () => {
  current = session();
  for (const [id, state] of Object.entries(current.steps) as [string, any][]) if (id !== "A06") state.status = "completed";
  page(); const scan = await node("A04");
  fireEvent.change(scan.getByLabelText("色散每点采集时间 (s)"), { target: { value: ".2" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A04/)).toBeVisible();
  fireEvent.change(scan.getByLabelText("色散每点采集时间 (s)"), { target: { value: ".1" } });
  expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument();
  fireEvent.change(scan.getByLabelText("色散每点采集时间 (s)"), { target: { value: ".2" } });
  fireEvent.change(scan.getByLabelText("噪声重复次数"), { target: { value: "3" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A04/)).toBeVisible();
  expect(current.steps.A04.status).toBe("completed");
  applyOk = false;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await screen.findByText(/Error: 参数应用失败/);
  expect(scan.getByLabelText("噪声重复次数")).toHaveValue(3);
  applyOk = true;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(current.parameters.NOISE_N_AVG).toBe(3));
  expect(current.steps.A04.status).toBe("stale");
  expect(mockApi.mock.calls.some(([url]) => String(url).endsWith("/steps"))).toBe(false);
});

it("分类保存回读、草稿还原、Tab保留与历史填回，预检失败不启动", async () => {
  const view = page(); const plan = await node("A02"), scan = await node("A04");
  fireEvent.click(scan.getByRole("button", { name: "Z 场换算系数 移至基础参数" }));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(layout.basic).toContain("Z_V_TO_NT");
  fireEvent.change(plan.getByLabelText("批次标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "填回参数" });
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(plan.getByLabelText("批次标签")).toHaveValue("draft");
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("候选值越界，禁止启动");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(plan.getByLabelText("批次标签")).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  view.unmount(); page();
  const reloaded = await node("A04");
  expect(reloaded.getByRole("button", { name: "Z 场换算系数 移至高级参数" })).toBeVisible();
});

it("旧RAMP只读摘要，两个历史对象无覆盖值，保存默认值仍仅保存下一次实验", async () => {
  page(); const plan = await node("A02");
  fireEvent.change(plan.getByLabelText("批次标签"), { target: { value: "next-only" } });
  for (const id of [old.run_id, other.run_id, "", old.run_id]) {
    fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: id } });
    if (id) await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/runs/${id}/analysis`)).toBe(true));
  }
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(savedValues.RUN_TAG_PREFIX).toBe("next-only");
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs/${old.run_id}/analysis`
    && options?.method === "POST" && JSON.stringify(JSON.parse(String(options.body)).analysis_parameters) === "{}")).toBe(true));
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const history = within(screen.getByRole("tabpanel", { name: "历史" }));
  expect(await history.findByText(/旧 RAMP 批次：只读取已有单点结论/)).toBeVisible();
  expect(history.getByText(/采集：完成.*分析：完成/)).toBeVisible();
});

it("旧接口缺元数据仍恢复会话并允许安全结束", async () => {
  current = session(); metadata = false; page();
  await screen.findByText(/接口版本过旧/);
  const finish = await screen.findByRole("button", { name: "结束会话" });
  await waitFor(() => expect(finish).toBeEnabled());
  fireEvent.click(finish);
  await waitFor(() => expect(current.status).toBe("closed"));
});
