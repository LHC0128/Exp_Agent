import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, it, expect, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import flow from "./rfControlScaleWorkflow.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
const ID = "rf-control-scale-sensitivity", BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const files = ["ControlA/waveforms/optimal_control_waveform.csv", "ControlB/waveforms/optimal_control_waveform.csv"];
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "scale", group: "basic" },
  { name: "CONTROL_WAVEFORM_FILES", label: "控制波形（可多选）", type: "array", default: [files[0]], group: "basic", options: files.map((value, i) => ({ value, label: `Control${i ? "B" : "A"}` })) },
  { name: "CONTROL_SCALE_START", label: "扫描倍率起点", type: "number", default: .9, group: "basic" },
  { name: "CONTROL_SCALE_STOP", label: "扫描倍率终点", type: "number", default: 1.1, group: "basic" },
  { name: "CONTROL_SCALE_POINTS", label: "扫描倍率点数", type: "integer", default: 3, group: "basic" },
  { name: "CONTROL_SCALE_FACTORS", label: "各控制补偿倍率", type: "object", default: {}, group: "basic" },
  { name: "Z_PHASE_SCAN_ENABLED", label: "扫描 RF 触发相位", type: "boolean", default: true, group: "basic" },
  { name: "Z_BURST_PHASE_INIT", label: "正 RF 触发相位", type: "number", default: 181.5, group: "basic" },
  { name: "Z_PHASE_SCAN_START_DEG", label: "RF 校相起始相位", type: "number", default: 0, group: "basic" },
  { name: "NOISE_NPERSEG", label: "Welch 段长", type: "integer", default: 128, group: "basic" },
  { name: "NOISE_OUTLIER_POLICY", label: "噪声异常记录处理", type: "string", default: "mad_high", group: "basic", options: [{ value: "mad_high", label: "保守剔除组内高值异常" }, { value: "none", label: "保留全部噪声记录" }] },
];
const parameters = Object.fromEntries(fields.map(f => [f.name, f.default]));
const summary = { run_id: "history", timestamp: "saved", run_tag: "saved", completion_status: "completed", analysis_status: "not_started", analysis_error: "", parameters: { ...parameters, RUN_TAG: "historical", CONTROL_WAVEFORM_FILES: files }, artifacts: {} };
let session: any;
class MockEventSource { onmessage = null; onerror = null; close() {} }
beforeEach(() => {
  session = null; vi.stubGlobal("EventSource", MockEventSource); mockApi.mockReset();
  mockApi.mockImplementation(async (url, options): Promise<any> => {
    if (url === BASE) return { id: ID, title: "多控制 Scale RF 响应与灵敏度", description: "每个控制 × Scale 独立四级校相，不注入人为噪声", wiring_notes: [] };
    if (url === BASE + "/schema") return { fields, parameter_layout: { basic: fields.map(f => f.name), advanced: [] } };
    if (url === BASE + "/workflow") return flow;
    if (url === "/api/jobs") return [];
    if (url === BASE + "/sessions/active") return session;
    if (url === BASE + "/sessions") return session = { session_id: "test", job_id: "job", revision: 0, status: "waiting", pending_request: null, parameters: JSON.parse(String(options?.body)).parameters, steps: Object.fromEntries(flow.acquisition.map(s => [s.id, { status: "completed", error: "" }])), outputs: {} };
    if (url === BASE + "/sessions/test" || url === BASE + "/sessions/test/steps" || url === BASE + "/sessions/test/finish") return session;
    if (url === BASE + "/sessions/test/parameters") return session = { ...session, revision: session.revision + 1, parameters: JSON.parse(String(options?.body)).parameters };
    if (url.startsWith(BASE + "/runs?")) return { runs: [summary], total: 1 };
    if (url.endsWith("/summary")) return summary;
    if (url.endsWith("/analysis")) return { run_id: "history", revision: 0, options: { noise_nperseg: 128 }, steps: Object.fromEntries(flow.analysis.map(s => [s.id, { status: "pending", error: "" }])) };
    if (url === BASE + "/preflight") return { ok: false, errors: ["实际输出超过电压安全限值"] };
    if (url === BASE + "/derive") return { values: { parameters: JSON.parse(String(options?.body)).parameters } };
    if (url === BASE + "/defaults") return { schema: { fields, parameter_layout: JSON.parse(String(options?.body)).parameter_layout } };
    if (url.startsWith("/api/jobs/")) return { id: "job", kind: `session:${ID}`, status: "running", events: [] };
    throw new Error(`未模拟 ${url}`);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function show() {
  render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}><Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);
  await screen.findByRole("button", { name: "创建分步会话" });
  await waitFor(() => expect(document.querySelectorAll(".xy-node-parameters").length).toBeGreaterThan(0));
  document.querySelectorAll<HTMLDetailsElement>(".xy-node-parameters").forEach(node => { node.open = true; });
}

it("真实路由多选控制、保留草稿、保存布局、历史填回，预检失败不启动", async () => {
  await show();
  expect(screen.getByLabelText("ControlA")).toBeChecked();
  fireEvent.click(screen.getByLabelText("ControlB"));
  fireEvent.change(screen.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("draft");
  expect(screen.getByLabelText("ControlB")).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存，仅影响下一次实验");
  expect(JSON.parse(String(mockApi.mock.calls.find(([url]) => url.endsWith("/defaults"))![1]?.body)).parameters.CONTROL_WAVEFORM_FILES).toEqual(files);
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("实际输出超过电压安全限值");
  expect(mockApi.mock.calls.some(([url, options]) => url === BASE + "/runs" && options?.method === "POST")).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: /history/ }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  await waitFor(() => expect(screen.getByLabelText("运行标签")).toHaveValue("historical"));
});

it("起终点点数和按控制名称的补偿可编辑、保存与应用，横轴预览保留扫描倍率", async () => {
  await show();
  expect(screen.getByLabelText("各控制补偿倍率 · ControlA")).toHaveValue(1);
  fireEvent.click(screen.getByLabelText("ControlB"));
  expect(screen.getByLabelText("各控制补偿倍率 · ControlB")).toHaveValue(1);
  fireEvent.change(screen.getByLabelText("扫描倍率起点"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("扫描倍率终点"), { target: { value: "-2" } });
  fireEvent.change(screen.getByLabelText("扫描倍率点数"), { target: { value: "5" } });
  fireEvent.change(screen.getByLabelText("各控制补偿倍率 · ControlA"), { target: { value: "1.25" } });
  fireEvent.change(screen.getByLabelText("各控制补偿倍率 · ControlB"), { target: { value: "0.8" } });
  expect(screen.getByLabelText("采集预览")).toHaveTextContent("2 → -2，5 点");
  fireEvent.click(screen.getByLabelText("ControlA"));
  expect(screen.getByLabelText("各控制补偿倍率 · ControlB")).toHaveValue(.8);
  fireEvent.click(screen.getByLabelText("ControlA"));
  expect(screen.getByLabelText("各控制补偿倍率 · ControlA")).toHaveValue(1.25);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存，仅影响下一次实验");
  const saved = mockApi.mock.calls.find(([url]) => url.endsWith("/defaults"))!;
  expect(JSON.parse(String(saved[1]?.body)).parameters).toMatchObject({
    CONTROL_SCALE_START: 2, CONTROL_SCALE_STOP: -2, CONTROL_SCALE_POINTS: 5,
    CONTROL_SCALE_FACTORS: { ControlA: 1.25, ControlB: .8 } });
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByRole("button", { name: "结束会话" });
  fireEvent.change(screen.getByLabelText("各控制补偿倍率 · ControlA"), { target: { value: "1.3" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A13/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url.endsWith("/parameters"))).toBe(true));
  const applied = mockApi.mock.calls.find(([url]) => url.endsWith("/parameters"))!;
  expect(JSON.parse(String(applied[1]?.body)).parameters.CONTROL_SCALE_FACTORS).toEqual({ ControlA: 1.3, ControlB: .8 });
});

it("分步流程执行逐点完整校相与测量节点，结束会话可用", async () => {
  await show();
  expect(flow.acquisition.find(stage => stage.id === "A13")?.dependencies).toEqual(["A07"]);
  expect(screen.getByText("每个控制 × Scale 独立四级校相，不注入人为噪声")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByRole("button", { name: "结束会话" });
  fireEvent.click(screen.getByLabelText("A13 " + flow.acquisition.find(s => s.id === "A13")!.title));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url.endsWith("/sessions/test/steps"))).toBe(true));
});

it("A13 设置全部控制倍率共用的手动相位，默认181.5，保存和应用均保留", async () => {
  await show();
  expect(screen.getByLabelText("正 RF 触发相位")).toHaveValue(181.5);
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByRole("button", { name: "结束会话" });
  fireEvent.change(screen.getByLabelText("扫描 RF 触发相位"), { target: { value: "false" } });
  fireEvent.change(screen.getByLabelText("正 RF 触发相位"), { target: { value: "205.5" } });
  expect(screen.getByLabelText("RF 校相起始相位")).toBeDisabled();
  expect(screen.getByText(/全部控制 × Scale 使用手动相位 205.5°/)).toBeVisible();
  expect(screen.getByText(/预计需重跑的已完成步骤：A13/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(screen.queryByText("参数草稿尚未应用，请先应用参数再执行模块。")).toBeNull());
  const applied = mockApi.mock.calls.find(([url]) => url.endsWith("/parameters"))!;
  expect(JSON.parse(String(applied[1]?.body)).parameters).toMatchObject({ Z_PHASE_SCAN_ENABLED: false, Z_BURST_PHASE_INIT: 205.5 });
  fireEvent.click(screen.getByLabelText("A13 " + flow.acquisition.find(stage => stage.id === "A13")!.title));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url.endsWith("/steps"))).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存，仅影响下一次实验");
  const saved = mockApi.mock.calls.find(([url]) => url.endsWith("/defaults"))!;
  expect(JSON.parse(String(saved[1]?.body)).parameters).toMatchObject({ Z_PHASE_SCAN_ENABLED: false, Z_BURST_PHASE_INIT: 205.5 });
});

it("真实历史结果展示倍率指标与 PDF，支持重新分析", async () => {
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options): Promise<any> => {
    if (url.endsWith("/summary")) return { ...summary, artifacts: { "results/analysis.json": "/metrics", "results/full_analysis.png": "/figure.png", "results/full_analysis.pdf": "/figure.pdf" } };
    if (url === "/metrics") return { sensitivity_assumption: "历史 v1：固定标称相位；新 v2：每点四级校相，不改写旧数据。", points: [{ control: "ControlA", scale: 1, valid: true, slope_V_per_nT: 2, center_Hz: 3000, dispersive_hwhm_Hz: 105, sensitivity_fT_per_sqrtHz: 10, noise_records_total: 45, noise_records_rejected: 1 }] };
    if (url === "/api/runs/history/analyze") return { id: "analysis", kind: `analysis:${ID}:history`, status: "completed", events: [] };
    return original(url, options);
  });
  await show();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: /history/ }));
  expect(await screen.findByRole("columnheader", { name: "洛伦兹中心频率 Hz" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "剔除/总条数" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "1/45" })).toBeInTheDocument();
  expect(screen.getAllByText(/历史 v1：固定标称相位/).length).toBeGreaterThan(0);
  expect(screen.getByRole("link", { name: "results/full_analysis.pdf" })).toHaveAttribute("href", "/figure.pdf");
  fireEvent.click(screen.getByRole("button", { name: "重新分析" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url === "/api/runs/history/analyze")).toBe(true));
});
