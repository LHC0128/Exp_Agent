import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./CoilDriveCharacterizationPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => <span>采集任务状态</span> }));
const ID = "coil-drive-characterization", BASE = `/api/experiments/${ID}`;
const { fields, flow } = fixture;
const mockApi = vi.mocked(api);
const parameters = () => Object.fromEntries(fields.map(f => [f.name, f.default]));
const layout = () => ({ basic: fields.filter(f => f.group === "basic").map(f => f.name),
  advanced: fields.filter(f => f.group !== "basic").map(f => f.name) });
const old = { run_id: "1113_old", timestamp: "1113", run_tag: "old", completion_status: "completed" };
let current: any, preflightOk: boolean;
const makeSession = () => ({ session_id: "coil-session", job_id: "coil-job", status: "waiting", revision: 0,
  parameters: parameters(), pending_request: null, outputs: { 功放: "OFF", "Z 通道": "未知" },
  steps: Object.fromEntries(flow.acquisition.map(s => [s.id, { status: "pending", error: "" }])) });

beforeEach(() => {
  current = null; preflightOk = true;
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { id: ID, title: "线圈驱动链路电学特性", description: "功放零偏、传递函数与脉冲阶跃", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: layout() } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === "/api/jobs/coil-job") return { id: "coil-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === "/api/jobs/analysis-job") return { id: "analysis-job", status: "running", events: [] } as never;
    if (url === `${BASE}/workflow`) return flow as never;
    if (url === `${BASE}/sessions/active`) return current as never;
    if (url === `${BASE}/sessions` && options?.method === "POST") return current = makeSession();
    if (url === `${BASE}/sessions/coil-session/steps`) {
      if (body.step_ids.includes("A04") && !body.step_ids.includes("A03")) throw new Error("A04 正弦扫频传递函数 缺少前置模块：A03 功放零偏与增益核对");
      return current = { ...current, revision: current.revision + 1,
        steps: Object.fromEntries(flow.acquisition.map(s => [s.id,
          { status: body.step_ids.includes(s.id) ? "completed" : "pending", error: "" }])) };
    }
    if (url === `${BASE}/sessions/coil-session/parameters`) {
      const changed = Object.keys(body.parameters).filter(k => JSON.stringify(body.parameters[k]) !== JSON.stringify(current.parameters[k]));
      const affected = new Set(changed.flatMap(k => (flow.parameter_impacts.acquisition as Record<string, string[]>)[k] ?? []));
      return current = { ...current, parameters: body.parameters, revision: current.revision + 1,
        steps: Object.fromEntries(Object.entries(current.steps).map(([id, state]: [string, any]) => [id,
          affected.has(id) && state.status === "completed" ? { status: "stale", error: "" } : state])) };
    }
    if (url === `${BASE}/sessions/coil-session/finish`) return current = { ...current, revision: current.revision + 1, status: "closed" };
    if (url === `${BASE}/sessions/coil-session`) return current as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old], total: 1 } as never;
    if (url === `${BASE}/runs/${old.run_id}/summary`) return { ...old, analysis_status: "completed", analysis_error: "",
      parameters: { ...parameters(), RUN_TAG: "historical", PULSE_WIDTHS_US: [5, 20] }, artifacts: {} } as never;
    if (url === `${BASE}/runs/${old.run_id}/analysis` && options?.method === "POST") return { id: "analysis-job", status: "running", events: [] } as never;
    if (url === `${BASE}/runs/${old.run_id}/analysis`) return { run_id: old.run_id, revision: 0, options: { AREA_TOLERANCE: 0.05 },
      steps: Object.fromEntries(flow.analysis.map(s => [s.id, { status: "completed", error: "" }])), summary: {}, fingerprint: {} } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["z_pulse_coil_current 限值缺失"] } as never;
    if (url === `${BASE}/derive`) return { values: { sine_drive_peak_v: 0.2, pulse_count: 7 } } as never;
    if (url === `${BASE}/defaults`) return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: body.parameter_layout } } as never;
    throw new Error(`未模拟接口 ${url}`);
  });
});
afterEach(cleanup);
const page = () => render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}>
  <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
</MemoryRouter>);
const node = async (id: string) => {
  const stage = [...flow.acquisition, ...flow.analysis].find(s => s.id === id)!;
  const region = await screen.findByRole("region", { name: `${id} ${stage.title} 参数`, hidden: true });
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  for (const advanced of region.querySelectorAll<HTMLDetailsElement>("details")) advanced.open = true;
  return within(region);
};
const label = (id: string) => `${id} ${flow.acquisition.find(s => s.id === id)!.title}`;

it("真实路由提供 6/2 节点，缺前置拒绝，扫频与脉冲可单独执行后安全结束", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  expect(flow.acquisition.map(s => s.id)).toEqual(["A01", "A02", "A03", "A04", "A05", "A06"]);
  expect(flow.analysis.map(s => s.id)).toEqual(["B01", "B02"]);
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: label("A04") }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选" }));
  await screen.findByText(/缺少前置模块：A03/);
  for (const id of ["A01", "A02", "A03", "A05"]) fireEvent.click(screen.getByRole("checkbox", { name: label(id) }));
  fireEvent.click(screen.getByRole("checkbox", { name: label("A04") }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选" }));
  await waitFor(() => expect(current.steps.A05.status).toBe("completed"));
  expect(current.steps.A04.status).toBe("pending");
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current.status).toBe("closed"));
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
});

it("全部可见参数都有编辑入口，顶部含保存、预检与完整运行", async () => {
  page(); await screen.findByRole("button", { name: "创建分步会话" });
  const owned = new Set([...flow.acquisition, ...flow.analysis].flatMap(s => s.display_parameters));
  expect([...owned].sort()).toEqual(fields.map(f => f.name).sort());
  const top = screen.getByRole("button", { name: "保存参数与布局" }).closest(".xy-workflow-controls")!;
  expect(top).toContainElement(screen.getByRole("button", { name: "仅预检" }));
  expect(top).toContainElement(screen.getByRole("button", { name: "预检并运行实验" }));
});

it("共享等待时间跨 A03/A04/A05 同步，应用后三个测量节点失效且不自动执行", async () => {
  current = makeSession();
  Object.values(current.steps).forEach((s: any) => { s.status = "completed"; });
  page();
  const zero = await node("A03"), sweep = await node("A04"), pulse = await node("A05");
  fireEvent.change(sweep.getByLabelText("输出切换后等待 (s)"), { target: { value: "0.5" } });
  expect(zero.getByLabelText("输出切换后等待 (s)")).toHaveValue(0.5);
  expect(pulse.getByLabelText("输出切换后等待 (s)")).toHaveValue(0.5);
  expect(screen.getByText(/预计需重跑的已完成步骤：A03/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(current.parameters.SETTLE_S).toBe(0.5));
  expect(["A03", "A04", "A05"].map(id => current.steps[id].status)).toEqual(["stale", "stale", "stale"]);
  expect(current.steps.A02.status).toBe("completed");
  expect(mockApi.mock.calls.some(([url]) => String(url).endsWith("/steps"))).toBe(false);
});

it("历史填回不保存不启动，预检失败阻止完整运行", async () => {
  page(); const preflight = await node("A01");
  fireEvent.change(preflight.getByLabelText("运行标签"), { target: { value: "draft" } });
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("z_pulse_coil_current 限值缺失");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  expect(preflight.getByLabelText("运行标签")).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("历史目录的面积容差覆盖值独立于下一次实验参数", async () => {
  page(); const next = await node("B02");
  fireEvent.change(next.getByLabelText("脉冲面积/平台容差"), { target: { value: "0.03" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  const field = () => within(screen.getByRole("region", { name: `B02 ${flow.analysis[1].title} 参数`, hidden: true }))
    .getByLabelText("脉冲面积/平台容差");
  await waitFor(() => expect(field()).toHaveValue(0.05));
  fireEvent.change(field(), { target: { value: "0.1" } });
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" })); await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.AREA_TOLERANCE).toBe(0.03);
  fireEvent.click(screen.getByRole("button", { name: "全选分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs/${old.run_id}/analysis`
    && options?.method === "POST" && JSON.parse(String(options.body)).analysis_parameters.AREA_TOLERANCE === 0.1)).toBe(true));
});
