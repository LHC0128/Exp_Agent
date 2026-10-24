import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./BBDutyScanBurstPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => <span>采集任务状态</span> }));
const ID = "bb-duty-scan-burst", BASE = `/api/experiments/${ID}`;
const { fields, flow } = fixture;
const mockApi = vi.mocked(api);
const parameters = () => Object.fromEntries(fields.map(f => [f.name, f.default]));
const layout = () => ({ basic: fields.filter(f => f.group === "basic").map(f => f.name),
  advanced: fields.filter(f => f.group !== "basic").map(f => f.name) });
const old = { run_id: "0527_old", timestamp: "0527", run_tag: "old", completion_status: "completed" };
const other = { ...old, run_id: "0708_other" };
let current: any, preflightOk: boolean, metadata: boolean;
let savedLayout: ReturnType<typeof layout>;
const makeSession = () => ({ session_id: "bb-duty-session", job_id: "bb-duty-job", status: "waiting", revision: 0,
  parameters: parameters(), pending_request: null, outputs: { Pump: "OFF", 温控: "ON" },
  steps: Object.fromEntries(flow.acquisition.map(s => [s.id, { status: "pending", error: "" }])) });

beforeEach(() => {
  current = null; preflightOk = true; metadata = true; savedLayout = layout();
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { id: ID, title: "BB Pump 占空比扫描", description: "固定 Pump 占空比扫描与 SDS 瞬态拟合", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: savedLayout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === "/api/jobs/bb-duty-job") return { id: "bb-duty-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === "/api/jobs/analysis-job") return { id: "analysis-job", status: "running", events: [] } as never;
    if (url === `${BASE}/workflow`) return (metadata ? flow : { ...flow, parameter_impacts: undefined }) as never;
    if (url === `${BASE}/sessions/active`) return current as never;
    if (url === `${BASE}/sessions` && options?.method === "POST") return current = makeSession();
    if (url === `${BASE}/sessions/bb-duty-session/steps`) {
      if (body.step_ids.includes("A07") && !body.step_ids.includes("A06")) throw new Error("缺少前置：A06 SDS 双通道、时基与实际采样回读");
      return current = { ...current, revision: current.revision + 1,
        steps: Object.fromEntries(flow.acquisition.map(s => [s.id,
          { status: body.step_ids.includes(s.id) ? "completed" : "pending", error: "" }])) };
    }
    if (url === `${BASE}/sessions/bb-duty-session/parameters`) {
      const changed = Object.keys(body.parameters).filter(k => JSON.stringify(body.parameters[k]) !== JSON.stringify(current.parameters[k]));
      const affected = new Set(changed.flatMap(k => flow.parameter_impacts.acquisition[k as keyof typeof flow.parameter_impacts.acquisition] ?? []));
      return current = { ...current, parameters: body.parameters, revision: current.revision+1,
        steps: Object.fromEntries(Object.entries(current.steps).map(([id,state]: [string,any]) => [id,
          affected.has(id) && state.status === "completed" ? { status: "stale", error: "" } : state])) };
    }
    if (url === `${BASE}/sessions/bb-duty-session/finish`) return current = { ...current, revision: current.revision+1, status: "closed" };
    if (url === `${BASE}/sessions/bb-duty-session`) return current as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old,other], total: 2 } as never;
    for (const run of [old,other]) {
      if (url === `${BASE}/runs/${run.run_id}/summary`) return { ...run, analysis_status: "not_started", analysis_error: "",
        parameters: { ...parameters(), RUN_TAG: "historical", DUTY_CYCLE_LIST: [0.05, 0.1] }, artifacts: {} } as never;
      if (url === `${BASE}/runs/${run.run_id}/analysis` && options?.method === "POST") return { id: "analysis-job", status: "running", events: [] } as never;
      if (url === `${BASE}/runs/${run.run_id}/analysis`) return { run_id: run.run_id, revision: 0,
        options: { POST_DARK_TIME_S: run === old ? 0.003 : 0.004 },
        steps: Object.fromEntries(flow.analysis.map(s => [s.id, { status: "completed", error: "" }])), summary: {}, fingerprint: {} } as never;
    }
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["占空比预检失败"] } as never;
    if (url === `${BASE}/derive`) return { values: { total_records: 620 } } as never;
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
  const stage = [...flow.acquisition,...flow.analysis].find(s => s.id === id)!;
  const region = await screen.findByRole("region", { name: `${id} ${stage.title} 参数`, hidden: true });
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  for (const advanced of region.querySelectorAll<HTMLDetailsElement>("details")) advanced.open = true;
  return within(region);
};

it("真实路由提供8/3节点，缺前置不补选，显式执行与安全结束", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected","true");
  expect(screen.getByRole("tab", { name: "历史" })).toBeVisible();
  expect(flow.acquisition).toHaveLength(8); expect(flow.analysis).toHaveLength(3);
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  const label = (id: string) => `${id} ${flow.acquisition.find(s => s.id === id)!.title}`;
  fireEvent.click(screen.getByRole("checkbox", { name: label("A07") }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: 缺少前置：A06/);
  expect(screen.getByRole("checkbox", { name: label("A06") })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(current.revision).toBe(1));
  expect(screen.getByRole("checkbox", { name: label("A08") })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current.status).toBe("closed"));
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href",`${BASE}/workflow/diagram`);
});

it("所有字段全部归属节点，初始化不重复，顶部包含保存预检运行与状态", async () => {
  page(); await screen.findByRole("button", { name: "创建分步会话" });
  const owned = new Set([...flow.acquisition,...flow.analysis].flatMap(s => s.display_parameters));
  expect([...owned].sort()).toEqual(fields.map(f => f.name).sort());
  expect(fields).toHaveLength(33);
  expect(flow.acquisition.filter(s => s.display_parameters.includes("TEC_TEMPERATURE")).map(s => s.id)).toEqual(["A04"]);
  const top = screen.getByRole("button", { name: "保存参数与布局" }).closest(".xy-workflow-controls")!;
  expect(top).toContainElement(screen.getByRole("button", { name: "仅预检" }));
  expect(top).toContainElement(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(top).toContainElement(within(top as HTMLElement).getByText("采集任务状态"));
});

it("采集时长跨节点共享，草稿还原、分类保存回读，应用后显式重跑", async () => {
  current = makeSession();
  Object.values(current.steps).forEach((s: any) => { s.status = "completed"; });
  const view = page(), prepared = await node("A06"), scan = await node("A07");
  const original = parameters().POST_DARK_TIME_S;
  fireEvent.change(prepared.getByLabelText("暗弛豫记录/拟合时长 (s)"), { target: { value: "0.015" } });
  expect(scan.getByLabelText("暗弛豫记录/拟合时长 (s)")).toHaveValue(0.015);
  expect(screen.getByText(/预计需重跑的已完成步骤：A06/)).toBeVisible();
  fireEvent.change(scan.getByLabelText("暗弛豫记录/拟合时长 (s)"), { target: { value: String(original) } });
  expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument();
  fireEvent.click(prepared.getByRole("button", { name: "暗弛豫记录/拟合时长 移至基础参数" }));
  await node("A07");
  expect(scan.getByRole("button", { name: "暗弛豫记录/拟合时长 移至高级参数" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(savedLayout.basic).toContain("POST_DARK_TIME_S");
  view.unmount(); page();
  const reopened = await node("A06");
  expect(reopened.getByRole("button", { name: "暗弛豫记录/拟合时长 移至高级参数" })).toBeVisible();
  fireEvent.change(reopened.getByLabelText("暗弛豫记录/拟合时长 (s)"), { target: { value: "0.01" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(current.parameters.POST_DARK_TIME_S).toBe(0.01));
  expect(current.steps.A06.status).toBe("stale");
  expect(current.steps.A07.status).toBe("stale");
  expect(mockApi.mock.calls.some(([url]) => String(url).endsWith("/steps"))).toBe(false);
});

it("Tab保留草稿，历史快照填回不保存不启动，预检失败阻止完整运行", async () => {
  page(); const scan = await node("A07");
  fireEvent.change(scan.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "填回参数" });
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByText("historical")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(scan.getByLabelText("运行标签")).toHaveValue("draft");
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("占空比预检失败");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(scan.getByLabelText("运行标签")).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url,options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("共享暗段参数、两个历史目录草稿与下一次参数隔离", async () => {
  page(); const next = await node("B02");
  fireEvent.change(next.getByLabelText("暗弛豫记录/拟合时长 (s)"), { target: { value: "0.015" } });
  const prepared = await node("A06");
  expect(prepared.getByLabelText("暗弛豫记录/拟合时长 (s)")).toHaveValue(0.015);
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  const field = () => {
    const region = screen.getByRole("region", { name: `B02 ${flow.analysis[1].title} 参数`, hidden: true });
    return within(region).getByLabelText("暗弛豫记录/拟合时长 (s)");
  };
  await waitFor(() => expect(field()).toHaveValue(0.003));
  fireEvent.change(field(), { target: { value: "0.002" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: other.run_id } });
  await waitFor(() => expect(field()).toHaveValue(0.004));
  fireEvent.change(field(), { target: { value: "0.001" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await waitFor(() => expect(field()).toHaveValue(0.002));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" })); await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.POST_DARK_TIME_S).toBe(0.015);
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url,options]) => url === `${BASE}/runs/${old.run_id}/analysis`
    && options?.method === "POST" && JSON.parse(String(options.body)).analysis_parameters.POST_DARK_TIME_S === 0.002)).toBe(true));
});

it("接口元数据不兼容仍恢复存活会话并允许安全结束", async () => {
  current = makeSession(); metadata = false; page();
  await screen.findByText(/接口版本过旧/);
  const end = await screen.findByRole("button", { name: "结束会话" });
  await waitFor(() => expect(end).toBeEnabled()); fireEvent.click(end);
  await waitFor(() => expect(current.status).toBe("closed"));
});
