import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import flow from "./staticSensitivityWorkflow.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
const mockApi = vi.mocked(api);
const ID = "static-sensitivity";
const BASE = `/api/experiments/${ID}`;
const fields = [
  { name: "run_tag", label: "运行标签", type: "string", default: "sens", group: "basic" },
  { name: "pump_laser_power", label: "Pump 光功率", type: "number", default: .2, group: "basic", unit: "V" },
  { name: "dispersion_steps", label: "色散扫描点数", type: "integer", default: 41, group: "basic" },
  { name: "dispersion_duration_s", label: "色散每点采集时间", type: "number", default: .1, group: "advanced", unit: "s" },
  { name: "hf2_demod_rate", label: "色散采样率", type: "number", default: 1000, group: "advanced", unit: "Sa/s" },
  { name: "dispersion_temp_switch_on_settle_s", label: "每组后温控恢复等待", type: "number", default: 3, group: "basic", unit: "s" },
  { name: "noise_duration", label: "单次噪声时长", type: "number", default: 1, group: "basic", unit: "s" },
  { name: "z_v_to_nt", label: "Z 场换算系数", type: "number", default: 3517, group: "advanced", unit: "nT/V" },
];
const defaults = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
let summary: any;
let activeSession: any;
let sessionJob: any;
let analysisJob: any;
let analysisOptions: Record<string, number>;
let preflightOk: boolean;
let applyError: boolean;
let oldMetadata: boolean;
let savedLayout: any;
let sources: MockEventSource[];
class MockEventSource {
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { sources.push(this); }
  close() {}
}
const makeSession = (completed = false) => ({ session_id: "static-session", job_id: "session-job", revision: 3, status: "waiting",
  parameters: defaults(), steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: completed ? "completed" : "pending", error: "" }])),
  outputs: { Z: "OFF", 温控: "ON", "时序 2": "保持当前状态" }, run_id: "test_dc", demod_mode: "dispersion" });
const tab = (name: string) => within(screen.getByRole("tablist", { name: "静磁场灵敏度实验视图" })).getByRole("tab", { name });
const node = (id: string) => screen.getByRole("checkbox", { name: new RegExp(`^${id} `) }).closest("li")!;
const edit = (id: string, label: string, value: string) => fireEvent.change(within(node(id)).getByLabelText(label), { target: { value } });
const renderPage = async () => {
  const view = render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}><Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);
  await screen.findByRole("checkbox", { name: /^A01 / });
  document.querySelectorAll<HTMLDetailsElement>(".xy-node-parameters, .zaw-parameter-advanced").forEach(item => { item.open = true; });
  return view;
};

beforeEach(() => {
  mockApi.mockReset(); sources = []; vi.stubGlobal("EventSource", MockEventSource);
  activeSession = null; preflightOk = true; applyError = oldMetadata = false; savedLayout = undefined;
  sessionJob = { id: "session-job", kind: `session:${ID}`, status: "running", events: [], message: "等待下一步" };
  analysisJob = { id: "analysis-job", kind: `analysis:${ID}:test_dc`, status: "running", events: [], message: "分阶段分析", stage: "analysis" };
  analysisOptions = { test_dc: 1950, second: 2100 };
  summary = { run_id: "test_dc", timestamp: "test", run_tag: "history", completion_status: "completed",
    parameters: { ...defaults(), run_tag: "history", z_v_to_nt: 1950 },
    analysis: { success: true, fit_valid: true, best_range_label: "10mA", sensitivity_flat_fT_per_sqrt_Hz: 100 },
    artifacts: { "full_analysiswithoutpump.png": "/analysis.png" } };
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { title: "静磁场灵敏度", description: "直流逐点色散与交互流程图", execution_mode: "typed_workflow", required_devices: ["HF2"], acquisition_program: "experiments/Static_Magnetic_Field_Sensitivity.py" };
    if (url === `${BASE}/schema`) return { schema_version: 3, fields, parameter_layout_saved: Boolean(savedLayout), parameter_layout: savedLayout };
    if (url === "/api/jobs") return [];
    if (url === "/api/jobs/session-job") return sessionJob;
    if (url === "/api/jobs/analysis-job") return analysisJob;
    if (url === "/api/jobs/session-job/cancel") return { ...sessionJob, message: "已请求安全停止" };
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary, { ...summary, run_id: "second" }], total: 2 };
    if (url.endsWith("/summary")) return summary;
    if (url === `${BASE}/workflow`) return oldMetadata ? { ...flow, parameter_impacts: undefined } : flow;
    if (url === `${BASE}/sessions/active`) return activeSession;
    if (url === `${BASE}/sessions`) { activeSession = makeSession(); return activeSession; }
    if (url === `${BASE}/sessions/static-session`) return activeSession;
    if (url.endsWith("/steps")) {
      activeSession = { ...activeSession, revision: activeSession.revision + 1,
        error: body.step_ids.includes("A09") && activeSession.steps.A08.status !== "completed" ? "A09 缺少前置模块 A08" : null };
      return activeSession;
    }
    if (url.endsWith("/parameters")) {
      if (applyError) throw new Error("应用失败");
      const changed = Object.keys(body.parameters).filter(key => body.parameters[key] !== activeSession.parameters[key]);
      const affected = new Set(changed.flatMap(key => (flow.parameter_impacts.acquisition as Record<string, string[]>)[key] ?? []));
      activeSession = { ...activeSession, parameters: body.parameters, revision: activeSession.revision + 1,
        steps: Object.fromEntries(Object.entries(activeSession.steps).map(([key, value]: [string, any]) => [key, affected.has(key) ? { ...value, status: "stale" } : value])) };
      return activeSession;
    }
    if (url.endsWith("/finish")) { activeSession = { ...activeSession, status: "closed", revision: activeSession.revision + 1, safety_shutdown: { completed: true } }; sessionJob.status = "completed"; return activeSession; }
    if (url.endsWith("/analysis")) {
      if (options?.method === "POST") return analysisJob;
      const id = url.split("/").at(-2)!;
      return { run_id: id, revision: 4, options: { z_v_to_nt: analysisOptions[id] }, fingerprint: {}, summary: {},
        steps: Object.fromEntries(flow.analysis.map(stage => [stage.id, { status: "completed", error: "" }])) };
    }
    if (url === `${BASE}/defaults`) { savedLayout = body.parameter_layout; return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: savedLayout } }; }
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["预检失败"] };
    if (url === `${BASE}/runs`) return { id: "full-job", kind: `experiment:${ID}`, status: "queued", events: [], message: "完整运行" };
    throw new Error(`未模拟 ${url}`);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("从真实路由加载三 Tab，保存节点参数和布局，历史填回不自动运行", async () => {
  const view = await renderPage();
  edit("A09", "色散扫描点数", "61");
  edit("A09", "色散每点采集时间 (s)", "0.2");
  fireEvent.click(within(node("A09")).getByRole("button", { name: "色散每点采集时间 移至基础参数" }));
  const flowContainer = screen.getByRole("region", { name: "交互实验流程图" });
  expect(flowContainer).toContainElement(screen.getByRole("button", { name: "保存参数与布局" }));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const body = JSON.parse(String(mockApi.mock.calls.find(([url]) => url.endsWith("/defaults"))![1]?.body));
  expect(body.parameters.dispersion_steps).toBe(61); expect(body.parameters.dispersion_duration_s).toBe(.2);
  expect(body.parameter_layout.basic).toContain("dispersion_duration_s");
  fireEvent.click(tab("历史"));
  fireEvent.click(await screen.findByRole("button", { name: /test_dc/ }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  expect(within(node("A09")).getByLabelText("运行标签")).toHaveValue("history");
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/runs`)).toBe(false);
  fireEvent.click(tab("架构"));
  expect(screen.getByTitle("静磁场灵敏度实验采集流程架构图")).toHaveAttribute("src", "/architecture/static-sensitivity.html");
  view.unmount(); await renderPage();
  expect(within(node("A09")).getByRole("button", { name: "色散每点采集时间 移至高级参数" })).toBeVisible();
});

it("共享温控参数同步编辑及分类，初始化条件只在设置节点出现", async () => {
  await renderPage();
  edit("A08", "每组后温控恢复等待 (s)", "0.8");
  expect(within(node("A09")).getByLabelText("每组后温控恢复等待 (s)")).toHaveValue(.8);
  fireEvent.click(within(node("A09")).getByRole("button", { name: "每组后温控恢复等待 移至高级参数" }));
  expect(within(node("A08")).getByRole("button", { name: "每组后温控恢复等待 移至基础参数" })).toBeInTheDocument();
  expect(within(node("A04")).getByLabelText("Pump 光功率 (V)")).toBeInTheDocument();
  expect(within(node("A09")).queryByLabelText("Pump 光功率 (V)")).toBeNull();
});

it("恢复会话，草稿预览只在应用后失效，重跑须显式选择", async () => {
  activeSession = makeSession(true);
  await renderPage();
  expect(await screen.findByText(/持续占用硬件锁/)).toBeVisible();
  edit("A08", "每组后温控恢复等待 (s)", "0.8");
  expect(within(node("A08")).getByText("已完成")).toBeVisible();
  expect(within(node("A08")).getByText(/应用草稿后预计需重跑/)).toBeVisible();
  edit("A09", "每组后温控恢复等待 (s)", "3");
  expect(screen.queryByText(/参数草稿尚未应用/)).toBeNull();
  edit("A08", "每组后温控恢复等待 (s)", "0.8");
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(within(node("A08")).getByText("需重跑")).toBeVisible());
  expect(mockApi.mock.calls.some(([url]) => url.endsWith("/steps"))).toBe(false);
  fireEvent.click(screen.getByRole("checkbox", { name: /^A08 / }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url.endsWith("/steps"))).toBe(true));
  expect(JSON.parse(String(mockApi.mock.calls.find(([url]) => url.endsWith("/steps"))![1]?.body)).step_ids).toEqual(["A08"]);
});

it("缺少前置拒绝，参数应用失败保留草稿，空闲安全结束", async () => {
  activeSession = makeSession();
  await renderPage();
  fireEvent.click(screen.getByRole("checkbox", { name: /^A09 / }));
  expect(within(node("A09")).getByText("缺少前置：A08")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  expect(await screen.findByText("A09 缺少前置模块 A08")).toBeVisible();
  applyError = true;
  edit("A09", "色散扫描点数", "61");
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  expect(await screen.findByText(/应用失败/)).toBeVisible();
  expect(within(node("A09")).getByLabelText("色散扫描点数")).toHaveValue(61);
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(screen.getByText(/状态为已结束会话/)).toBeVisible());
  expect(mockApi.mock.calls.some(([url]) => url.endsWith("/finish"))).toBe(true);
});

it("历史小写换算覆盖独立保留，B03 只读，保存只取下一次实验参数", async () => {
  await renderPage();
  edit("B02", "Z 场换算系数 (nT/V)", "3600");
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "test_dc" } });
  await waitFor(() => expect(within(node("B02")).getByLabelText("Z 场换算系数 (nT/V)")).toHaveValue(1950));
  expect(within(node("B03")).queryByLabelText("单次噪声时长 (s)")).toBeNull();
  edit("B02", "Z 场换算系数 (nT/V)", "3900");
  expect(within(node("B02")).getByText(/应用草稿后预计需重跑/)).toBeVisible();
  expect(within(node("B04")).getByText(/应用草稿后预计需重跑/)).toBeVisible();
  expect(within(node("B03")).queryByText(/应用草稿后预计需重跑/)).toBeNull();
  fireEvent.click(tab("历史")); fireEvent.click(tab("运行"));
  expect(within(node("B02")).getByLabelText("Z 场换算系数 (nT/V)")).toHaveValue(3900);
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "second" } });
  await waitFor(() => expect(within(node("B02")).getByLabelText("Z 场换算系数 (nT/V)")).toHaveValue(2100));
  edit("B02", "Z 场换算系数 (nT/V)", "4200");
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "test_dc" } });
  await waitFor(() => expect(within(node("B02")).getByLabelText("Z 场换算系数 (nT/V)")).toHaveValue(3900));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = JSON.parse(String(mockApi.mock.calls.find(([url]) => url.endsWith("/defaults"))![1]?.body));
  expect(saved.parameters.z_v_to_nt).toBe(3600);
  fireEvent.click(screen.getByRole("checkbox", { name: /^B02 / }));
  fireEvent.click(screen.getByRole("checkbox", { name: /^B04 / }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, init]) => url.endsWith("/analysis") && init?.method === "POST")).toBe(true));
  const request = mockApi.mock.calls.find(([url, init]) => url.endsWith("/analysis") && init?.method === "POST")!;
  expect(JSON.parse(String(request[1]?.body)).analysis_parameters).toEqual({ z_v_to_nt: 3900 });
});

it("预检失败不启动，旧格式只查看已有结果，失败结果不冒用旧图", async () => {
  preflightOk = false;
  summary.parameters = { ramp_low: -.1, ramp_freq: 1 };
  summary.analysis = { success: false, fit_valid: false, fit_rejection_reasons: ["色散中心斜率不可用"] };
  await renderPage();
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("预检失败")).toBeVisible();
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/runs`)).toBe(false);
  expect(screen.queryByAltText("静磁场灵敏度分析结果")).toBeNull();
  fireEvent.click(tab("历史"));
  fireEvent.click(await screen.findByRole("button", { name: /test_dc/ }));
  expect(await screen.findByText(/旧格式运行仅可查看/)).toBeVisible();
  expect(screen.getByRole("button", { name: "填回参数" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "重新分析" })).toBeDisabled();
});

it("旧流程元数据仍恢复任务并允许取消和安全结束", async () => {
  activeSession = makeSession(); oldMetadata = true;
  render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}><Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);
  expect(await screen.findByText(/流程接口版本过旧/)).toBeVisible();
  expect(screen.getByRole("button", { name: "结束会话" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "取消任务" }));
  expect(await screen.findByText("已请求安全停止")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url.endsWith("/finish"))).toBe(true));
});

it("SSE 恢复阶段状态，执行中禁改参数和结束，取消入口在流程容器内", async () => {
  activeSession = makeSession(); await renderPage();
  await waitFor(() => expect(sources.length).toBeGreaterThan(0));
  act(() => sources.at(-1)!.onmessage?.({ data: JSON.stringify({ index: 1, timestamp: "2026-10-12T12:00:00", stage: "A09", message: "色散扫描中",
    data: { workflow_session: { ...activeSession, status: "running", current_step: "A09", revision: 4 } } }) }));
  expect(screen.getByRole("button", { name: "结束会话" })).toBeDisabled();
  expect(within(node("A09")).getByLabelText("色散扫描点数")).toBeDisabled();
  expect(screen.getByRole("region", { name: "交互实验流程图" })).toContainElement(screen.getByRole("button", { name: "取消任务" }));
});
