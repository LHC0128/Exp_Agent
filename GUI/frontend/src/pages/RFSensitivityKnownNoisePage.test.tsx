import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, it, expect, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import flow from "./rfKnownNoiseWorkflow.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
const ID = "rf-sensitivity-aw-known-noise";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const files = ["v9/waveforms/optimal_control_waveform.csv", "v10/waveforms/optimal_control_waveform.csv"];
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "known", group: "basic" },
  { name: "CONTROL_WAVEFORM_FILES", label: "控制波形版本（可多选）", type: "array", default: [files[0]], group: "basic", options: files.map((value, index) => ({ value, label: `v${index + 9} · RF 3000 Hz` })) },
  { name: "NOISE_AMPLITUDE_VPP", label: "实际噪声幅度", type: "number", default: 0.0034, group: "basic" },
  { name: "NOISE_NPERSEG", label: "Welch 段长", type: "integer", default: 128, group: "basic" },
];
const parameters = Object.fromEntries(fields.map(field => [field.name, field.default]));
let active: any;
const summary = { run_id: "history", timestamp: "saved", run_tag: "saved", completion_status: "completed", phase_calibration_status: "completed", analysis_status: "completed", can_analyze: true, can_compare: false, comparison_block_reason: "本轮内比较", parameters: { ...parameters, RUN_TAG: "history", CONTROL_WAVEFORM_FILES: files }, artifacts: {}, actual_rates: {}, analysis: { controls: { v9: { response_fit: { slope_V_per_nT: 0.1 }, sensitivity: { flat_median_fT_per_sqrtHz: 20 } } } } };
class MockEventSource { onmessage = null; onerror = null; close() {} }
beforeEach(() => {
  active = null;
  vi.stubGlobal("EventSource", MockEventSource);
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options): Promise<any> => {
    if (url === BASE) return { id: ID, title: "已知噪声下 RF 灵敏度（DirectAW）", description: "固定噪声 ON", wiring_notes: [] };
    if (url === `${BASE}/schema`) return { fields, parameter_layout: { basic: fields.map(f => f.name), advanced: [] } };
    if (url === `${BASE}/workflow`) return flow;
    if (url === "/api/jobs") return [];
    if (url === `${BASE}/sessions/active`) return active;
    if (url === `${BASE}/sessions`) return active = { session_id: "test", job_id: "job", revision: 0, status: "waiting", pending_request: null, parameters: JSON.parse(String(options?.body)).parameters, steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: "completed", error: "" }])), outputs: {} };
    if (url === `${BASE}/sessions/test`) return active;
    if (url === `${BASE}/sessions/test/steps`) return active;
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary], total: 1, offset: 0, limit: 50 };
    if (url.endsWith("/summary")) return summary;
    if (url.endsWith("/analysis")) return { run_id: "history", revision: 0, options: { noise_nperseg: 128 }, steps: Object.fromEntries(flow.analysis.map(stage => [stage.id, { status: "pending", error: "" }])) };
    if (url === `${BASE}/preflight`) return { ok: false, errors: ["设计 RF 频率不一致"] };
    if (url === `${BASE}/defaults`) return { schema: { fields, parameter_layout: JSON.parse(String(options?.body)).parameter_layout } };
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

it("真实路由保留多选与参数草稿，历史填回与预检失败不启动", async () => {
  await show();
  const select = screen.getByLabelText("控制波形版本（可多选）") as HTMLSelectElement;
  for (const option of select.options) option.selected = true;
  fireEvent.change(select);
  fireEvent.change(screen.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "history" });
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("draft");
  expect(Array.from(select.selectedOptions, option => option.value)).toEqual(files);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  const saved = JSON.parse(String(mockApi.mock.calls.find(([url]) => url.endsWith("/defaults"))![1]?.body));
  expect(saved.parameters.CONTROL_WAVEFORM_FILES).toEqual(files);
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("设计 RF 频率不一致");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "history" }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("history");
});

it("分步会话提交所选节点与当前控制列表", async () => {
  await show();
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByRole("button", { name: "结束会话" });
  fireEvent.click(screen.getByLabelText("A13 " + flow.acquisition.find(stage => stage.id === "A13")!.title));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url.endsWith("/sessions/test/steps"))).toBe(true));
});
