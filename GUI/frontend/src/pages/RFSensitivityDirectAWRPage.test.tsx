import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import flow from "./rfDirectAWRWorkflow.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
const ID = "rf-sensitivity-aw-r";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "rf_sens_aw_r", group: "basic" },
  { name: "Z_RF_FREQ", label: "R 响应扫描频率", type: "number", default: 3020, group: "basic" },
  { name: "Z_RF_NOISE_FREQ", label: "校相与噪声频率", type: "number", default: 3000, group: "basic" },
  { name: "XY_TRIGGER_FREQ", label: "共同触发频率", type: "number", default: 100, group: "basic" },
  { name: "TEMP_SWITCH_ON_LAG_S", label: "温控恢复等待", type: "number", default: 5, group: "basic" },
  { name: "NOISE_NPERSEG", label: "Welch 段长", type: "integer", default: 1000, group: "basic" },
  { name: "DEMOD3_IDX", label: "RF 解调器索引", type: "integer", default: 3, group: "basic" },
  { name: "RF_INPUT_ROUTE", label: "RF 输入线路", type: "string", default: "physical", group: "basic", options: [{ value: "physical", label: "物理连接" }, { value: "internal", label: "内部回采（仅 MFLI）" }] },
  { name: "RF_AUX_OUTPUT_IDX", label: "RF 辅助输出通道", type: "integer", default: 1, group: "basic", options: [0, 1, 2, 3].map(value => ({ value, label: `AuxOut${value + 1}` })) },
  { name: "RF_PHYSICAL_INPUT_SOURCE", label: "RF 物理输入通道", type: "string", default: "aux_input1", group: "basic", options: [{ value: "aux_input0", label: "Aux Input1" }, { value: "aux_input1", label: "Aux Input2" }] },
];
const layout = { basic: fields.map(field => field.name), advanced: [] };
const parameters = Object.fromEntries(fields.map(field => [field.name, field.default]));
const job = (id: string, kind: string) => ({ id, kind, status: "running", events: [], message: "执行中" });
const summary = {
  run_id: "first", timestamp: "2026-10-11", run_tag: "history", completion_status: "completed",
  phase_calibration_status: "completed", analysis_status: "completed", response_component: "R", noise_component: "X",
  can_analyze: true, can_compare: true, comparison_block_reason: "", parameters: { ...parameters, RUN_TAG: "history", Z_RF_FREQ: 3040, Z_RF_NOISE_FREQ: 2990 },
  analysis: { response_fit: { success: true, slope_V_per_nT: 0.005 }, sensitivity: { flat_median_fT_per_sqrtHz: 20 } },
  artifacts: { "full_analysis.png": "/r-plot.png" }, actual_rates: {}, hf2_response_snapshot: {}, hf2_noise_snapshot: {},
};
let active: any;
let preflightOk: boolean;
let comparisonRuns: any[];
function makeSession() {
  return { session_id: "r-session", job_id: "r-job", revision: 0, status: "waiting", pending_request: null,
    parameters, steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: "completed", error: "" }])), outputs: {}, run_id: "first" };
}
class MockEventSource { onmessage = null; onerror = null; constructor(public url: string) {} close() {} }
beforeEach(() => {
  active = null;
  preflightOk = true;
  comparisonRuns = [];
  vi.stubGlobal("EventSource", MockEventSource);
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options): Promise<any> => {
    if (url === BASE) return { id: ID, title: "RF 灵敏度（DirectAW R）", description: "R 标定 / X 噪声", wiring_notes: [] };
    if (url === `${BASE}/schema`) return { fields, parameter_layout: layout };
    if (url === `${BASE}/workflow`) return flow;
    if (url === "/api/jobs") return [];
    if (url === `${BASE}/sessions/active`) return active;
    if (url === `${BASE}/sessions`) return active = { ...makeSession(), parameters: JSON.parse(String(options?.body)).parameters };
    if (url === `${BASE}/sessions/r-session`) return active;
    if (url === `${BASE}/sessions/r-session/steps`) return active;
    if (url === `${BASE}/sessions/r-session/parameters`) return active = { ...active, revision: active.revision + 1, parameters: JSON.parse(String(options?.body)).parameters };
    if (url === `${BASE}/sessions/r-session/finish`) return active = { ...active, status: "closed" };
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary, { ...summary, run_id: "second" }], total: 2, offset: 0, limit: 50 };
    if (url.endsWith("/summary")) return summary;
    if (url.endsWith("/analysis")) return { run_id: "first", revision: 0, options: { noise_nperseg: 1000 }, steps: Object.fromEntries(flow.analysis.map(stage => [stage.id, { status: "pending", error: "" }])) };
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["AuxOut2 安全电压范围缺失"] };
    if (url === `${BASE}/defaults`) return { schema: { fields, parameter_layout: JSON.parse(String(options?.body)).parameter_layout } };
    if (url.endsWith("/analyze")) return job("analysis", `analysis:${ID}:first`);
    if (url === `${BASE}/compare`) {
      comparisonRuns = JSON.parse(String(options?.body)).runs;
      return { ...job("comparison", `comparison:${ID}`), status: "completed", result: { runs: comparisonRuns, warnings: [], artifacts: { "full_analysis_comparison.png": "/r-compare.png", "full_analysis_comparison.pdf": "/r-compare.pdf" } } };
    }
    if (url === `${BASE}/runs` && options?.method === "POST") return job("run", `experiment:${ID}`);
    if (url.startsWith("/api/jobs/")) return job(url.split("/").at(-1)!, `session:${ID}`);
    throw new Error(`未模拟 ${url}`);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
async function renderPage() {
  render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}><Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);
  await screen.findByRole("button", { name: "创建分步会话" });
  await waitFor(() => expect(document.querySelectorAll(".xy-node-parameters").length).toBeGreaterThan(0));
  document.querySelectorAll<HTMLDetailsElement>(".xy-node-parameters").forEach(node => { node.open = true; });
}

it("R 变体的可选 RF 索引和物理辅助通道贯穿默认参数", async () => {
  await renderPage();
  fireEvent.change(screen.getByLabelText("RF 解调器索引"), { target: { value: "1" } });
  fireEvent.change(screen.getByLabelText("RF 辅助输出通道"), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText("RF 物理输入通道"), { target: { value: "aux_input0" } });
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  const body = JSON.parse(String(mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)![1]?.body));
  expect(body.parameters).toMatchObject({ DEMOD3_IDX: 1, RF_INPUT_ROUTE: "physical", RF_AUX_OUTPUT_IDX: 0, RF_PHYSICAL_INPUT_SOURCE: "aux_input0" });
});

it("真实路由的双 Tab、R 标定、节点顺序、独立频率保存和历史填回", async () => {
  await renderPage();
  expect(within(screen.getByRole("tablist", { name: "RF 灵敏度（DirectAW R）视图" })).getAllByRole("tab")).toHaveLength(2);
  expect(await within(screen.getByRole("tabpanel", { name: "运行" })).findByAltText("R 标定 / X 噪声与 RF 灵敏度")).toHaveAttribute("src", "/r-plot.png");
  const ids = [...document.querySelectorAll(".xy-flow-columns > section:first-child .xy-step-select strong")].map(node => node.textContent?.slice(0, 3));
  expect(ids).toEqual(["A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09", "A10", "A13", "A12", "A14", "A15", "A16"]);
  fireEvent.change(screen.getByLabelText("R 响应扫描频率"), { target: { value: "3050" } });
  expect(screen.getByLabelText("校相与噪声频率")).toHaveValue(3000);
  expect(screen.getByLabelText("共同触发频率")).toHaveValue(100);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(true));
  const save = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(save[1]?.body)).parameters).toMatchObject({ Z_RF_FREQ: 3050, Z_RF_NOISE_FREQ: 3000 });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "填回参数" });
  expect(screen.queryByRole("button", { name: "重命名" })).toBeNull();
  expect(screen.queryByRole("button", { name: "删除本次实验" })).toBeNull();
  expect(screen.queryByLabelText("仅显示星标")).toBeNull();
  expect(screen.queryByRole("button", { name: "星标 first" })).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByLabelText("R 响应扫描频率")).toHaveValue(3050);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("R 响应扫描频率")).toHaveValue(3040);
  expect(screen.getByLabelText("校相与噪声频率")).toHaveValue(2990);
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
});

it("缺安全范围时预检失败，禁止启动", async () => {
  preflightOk = false;
  await renderPage();
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("AuxOut2 安全电压范围缺失");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
});

it("恢复 R 会话、按清单勾选执行和结束，并保持共享字段同步", async () => {
  active = makeSession();
  await renderPage();
  expect(await screen.findByText(/会话 r-sessio/)).toBeVisible();
  const waits = screen.getAllByLabelText("温控恢复等待");
  fireEvent.change(waits[0], { target: { value: "6" } });
  waits.forEach(input => expect(input).toHaveValue(6));
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "应用参数到会话" })).toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url.endsWith("/steps"))).toBe(true));
  const selected = JSON.parse(String(mockApi.mock.calls.find(([url]) => url.endsWith("/steps"))![1]?.body)).step_ids;
  expect(selected).toEqual(flow.acquisition.map(stage => stage.id).filter(id => id !== "A16"));
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url.endsWith("/finish"))).toBe(true));
});

it("历史重新分析和英文图例对比导出均使用 R 实验 ID", async () => {
  await renderPage();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "重新分析" });
  fireEvent.click(screen.getByRole("button", { name: "重新分析" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url === "/api/runs/first/analyze")).toBe(true));
  const analysis = mockApi.mock.calls.find(([url]) => url === "/api/runs/first/analyze")!;
  expect(JSON.parse(String(analysis[1]?.body)).experiment_id).toBe(ID);
});

it("多次 R 标定对比提供自定义英文图例和 PNG/PDF 下载", async () => {
  await renderPage();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByLabelText("选择 first 进行对比"));
  fireEvent.click(screen.getByLabelText("选择 second 进行对比"));
  fireEvent.change(screen.getByLabelText("图例：first"), { target: { value: "Run A" } });
  fireEvent.click(screen.getByRole("button", { name: "生成对比" }));
  expect(await screen.findByAltText("R 标定 / X 噪声与 RF 灵敏度对比")).toHaveAttribute("src", "/r-compare.png");
  expect(comparisonRuns).toEqual([{ run_id: "first", legend: "Run A" }, { run_id: "second", legend: "second" }]);
  expect(screen.getByRole("link", { name: "下载 PDF" })).toHaveAttribute("href", "/r-compare.pdf");
  expect(screen.getByRole("link", { name: "下载 PNG" })).toHaveAttribute("href", "/r-compare.png");
});
