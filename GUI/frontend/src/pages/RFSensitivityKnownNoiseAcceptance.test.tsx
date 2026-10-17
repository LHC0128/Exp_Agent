import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import flow from "./rfKnownNoiseWorkflow.fixture.json";
import schemaFixture from "./rfKnownNoiseAcceptance.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
const ID = "rf-sensitivity-aw-known-noise";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const fields = schemaFixture.fields;
const defaults = Object.fromEntries(fields.map(field => [field.name, field.default]));
let active: any;
let layout: { basic: string[]; advanced: string[] };
let applyError: boolean;
const steps = (status = "completed") => Object.fromEntries(flow.acquisition.map(stage => [stage.id,
  { status: stage.id === "A16" ? "pending" : status, error: "" }]));
const historical = (name: string) => ({ run_id: name, timestamp: "saved", run_tag: name,
  completion_status: "completed", phase_calibration_status: "completed", analysis_status: "completed",
  can_analyze: true, can_compare: false, comparison_block_reason: "本实验在一次运行内比较所选控制版本",
  parameters: { ...defaults, RUN_TAG: name, NOISE_NPERSEG: name === "first" ? 800 : 600 },
  artifacts: {}, actual_rates: {}, analysis: { controls: {} } });
class MockEventSource { onmessage = null; onerror = null; close() {} }
beforeEach(() => {
  layout = { basic: fields.filter(f => f.group === "basic").map(f => f.name),
    advanced: fields.filter(f => f.group !== "basic").map(f => f.name) };
  active = null;
  applyError = false;
  vi.stubGlobal("EventSource", MockEventSource);
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options): Promise<any> => {
    if (url === BASE) return { id: ID, title: "已知噪声下 RF 灵敏度（DirectAW）", description: "固定噪声 ON", wiring_notes: [] };
    if (url === `${BASE}/schema`) return { fields, parameter_layout: layout };
    if (url === `${BASE}/workflow`) return flow;
    if (url === "/api/jobs") return [];
    if (url === `${BASE}/sessions/active`) return active;
    if (url === `${BASE}/sessions/test`) return active;
    if (url === `${BASE}/sessions/test/parameters`) {
      if (applyError) throw new Error("参数校验失败");
      const body = JSON.parse(String(options?.body));
      return active = { ...active, revision: active.revision + 1, parameters: body.parameters,
        steps: Object.fromEntries(Object.entries(active.steps).map(([id, state]) => [id,
          flow.parameter_impacts.acquisition.SYSTEM_SETTLE_S.includes(id) ? { status: "stale", error: "" } : state])) };
    }
    if (url === `${BASE}/sessions/test/finish`) return active = { ...active, status: "closed", steps: { ...active.steps, A16: { status: "completed", error: "" } } };
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [historical("first"), historical("second")], total: 2, offset: 0, limit: 50 };
    if (url.endsWith("/summary")) return historical(url.includes("/second/") ? "second" : "first");
    if (url.endsWith("/analysis")) return { run_id: url.includes("/second/") ? "second" : "first", revision: 4,
      options: { noise_nperseg: url.includes("/second/") ? 600 : 800 },
      steps: Object.fromEntries(flow.analysis.map(stage => [stage.id, { status: "completed", error: "" }])) };
    if (url === `${BASE}/defaults`) {
      layout = JSON.parse(String(options?.body)).parameter_layout;
      return { schema: { fields, parameter_layout: layout } };
    }
    if (url === `${BASE}/preflight`) return { ok: false, errors: ["设计 RF 频率不一致"] };
    if (url.startsWith("/api/jobs/")) return { id: "job", kind: `session:${ID}`, status: active?.status === "closed" ? "completed" : "running", events: [] };
    throw new Error(`未模拟 ${url}`);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
async function show() {
  render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}><Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);
  await screen.findByRole("button", { name: "创建分步会话" });
  await waitFor(() => expect(document.querySelectorAll(".xy-node-parameters").length).toBeGreaterThan(0));
  expand();
}
function expand() { document.querySelectorAll<HTMLDetailsElement>(".xy-node-parameters, .xy-node-parameters details").forEach(node => { node.open = true; }); }

it("真实模型74字段全部在实际路由出现，保持双Tab和原图下载", async () => {
  await show();
  expect(fields).toHaveLength(74);
  expect(screen.getAllByRole("tab").map(tab => tab.textContent)).toEqual(["运行", "历史"]);
  for (const field of fields) {
    const labels = screen.queryAllByText(field.label, { exact: true });
    expect(labels.length, `${field.name}缺少节点编辑入口`).toBeGreaterThan(0);
  }
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  expect(screen.getAllByLabelText(/^系统稳定时间/, { selector: "input" }).length).toBeGreaterThan(1);
  expect(screen.getAllByLabelText(/^Cell 温度/, { selector: "input" })).toHaveLength(1);
});

it("存活会话恢复、共享值和分类同步，草稿还原及失败保留，应用后显式重跑", async () => {
  active = { session_id: "test", job_id: "job", revision: 7, status: "waiting", pending_request: null,
    parameters: { ...schemaFixture.parameters, RUN_TAG: "live" }, steps: steps(), outputs: {} };
  await show();
  await waitFor(() => expect(screen.getByLabelText("运行标签")).toHaveValue("live"));
  const inputs = () => screen.getAllByLabelText(/^系统稳定时间/, { selector: "input" });
  const old = defaults.SYSTEM_SETTLE_S;
  fireEvent.change(inputs()[0], { target: { value: "0.8" } });
  inputs().forEach(input => expect(input).toHaveValue(.8));
  expect(screen.getByText(/预计需重跑的已完成步骤：/)).toBeVisible();
  fireEvent.change(inputs()[0], { target: { value: String(old) } });
  expect(screen.queryByText(/预计需重跑的已完成步骤：/)).toBeNull();
  fireEvent.change(inputs()[0], { target: { value: "0.8" } });
  fireEvent.click(screen.getAllByRole("button", { name: "系统稳定时间 移至高级参数" })[0]);
  expand();
  expect(screen.getAllByRole("button", { name: "系统稳定时间 移至基础参数" })).toHaveLength(inputs().length);
  fireEvent.click(screen.getByLabelText("A13 " + flow.acquisition.find(stage => stage.id === "A13")!.title));
  expect(screen.getByRole("button", { name: "运行所选 / 重跑" })).toBeDisabled();
  applyError = true;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await screen.findByText("Error: 参数校验失败");
  expect(inputs()[0]).toHaveValue(.8);
  expect(screen.getByText("参数草稿尚未应用，请先应用参数再执行模块。")).toBeVisible();
  applyError = false;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(screen.queryByText("参数草稿尚未应用，请先应用参数再执行模块。")).toBeNull());
  const applied = mockApi.mock.calls.filter(([url]) => url.endsWith("/parameters")).at(-1)!;
  expect(JSON.parse(String(applied[1]?.body))).toMatchObject({ revision: 7, parameters: { SYSTEM_SETTLE_S: .8 } });
  expect(mockApi.mock.calls.some(([url]) => url.endsWith("/steps"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  expect(layout.advanced).toContain("SYSTEM_SETTLE_S");
  cleanup();
  active = null;
  await show();
  expect(screen.getAllByRole("button", { name: "系统稳定时间 移至基础参数" }).length).toBeGreaterThan(1);
});

it("两轮历史Welch草稿隔离，保存默认值仅取下一次参数，填回不保存不执行", async () => {
  await show();
  const object = screen.getByLabelText("分析操作对象");
  const segment = () => screen.getByLabelText("Welch 段长");
  const next = schemaFixture.parameters.NOISE_NPERSEG;
  fireEvent.change(object, { target: { value: "first" } });
  await waitFor(() => expect(segment()).toHaveValue(800));
  fireEvent.change(segment(), { target: { value: "512" } });
  expect(screen.getByText(/预计需重算的已完成步骤：B03/)).toBeVisible();
  fireEvent.change(object, { target: { value: "second" } });
  await waitFor(() => expect(segment()).toHaveValue(600));
  fireEvent.change(segment(), { target: { value: "256" } });
  fireEvent.change(object, { target: { value: "" } });
  expect(segment()).toHaveValue(next);
  fireEvent.change(object, { target: { value: "first" } });
  await waitFor(() => expect(segment()).toHaveValue(512));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.NOISE_NPERSEG).toBe(next);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "second" }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("second");
  expect(mockApi.mock.calls.filter(([url]) => url === `${BASE}/defaults`)).toHaveLength(1);
  expect(mockApi.mock.calls.some(([url, opts]) => opts?.method === "POST" && (/\/sessions|\/analysis|\/runs$/.test(url)))).toBe(false);
});
