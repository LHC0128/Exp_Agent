import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));

const ID = "rf-sensitivity-aw";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
let summary: any;
let jobs: any[];
let analysisJob: any;
let comparisonJob: any;
let comparisonRuns: any[];
let preflightOk: boolean;
let pageTotal: number;
let sources: MockEventSource[];
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "rf_sens_aw", group: "basic" },
  { name: "ARB_WAVEFORM_FILE", label: "最优控制波形版本", type: "string", default: "", group: "basic", options: [{ value: "", label: "请选择最优控制版本" }, ...["v1", "v8"].map(version => ({ value: `${version}/waveforms/optimal_control_waveform.csv`, label: version }))] },
  ...["FREQ", "AMPLITUDE", "OFFSET", "DUTY", "PHASE"].map((suffix, index) => ({ name: `XY_TRIGGER_${suffix}`, label: `共同触发${suffix}`, type: "number", default: [100, 5, 2.5, 50, 0][index], group: "basic" })),
  { name: "DEMOD3_ADC_SELECT", label: "Demod3 输入", type: "integer", default: 1, group: "advanced", read_only: true },
  { name: "LOOPBACK_AUX_SCALE", label: "回环增益", type: "number", default: 2.2044, group: "advanced" },
];
const layout = { basic: fields.filter(field => field.group === "basic").map(field => field.name), advanced: ["DEMOD3_ADC_SELECT", "LOOPBACK_AUX_SCALE"] };

class MockEventSource {
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { sources.push(this); }
  close() {}
}

const renderPage = () => render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}><Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);

beforeEach(() => {
  sources = [];
  vi.stubGlobal("EventSource", MockEventSource);
  jobs = [];
  pageTotal = 1;
  preflightOk = true;
  analysisJob = { id: "analysis", kind: `analysis:${ID}:1009_latest`, status: "running", stage: "analysis", message: "正在分析", events: [] };
  comparisonJob = { id: "comparison", kind: `comparison:${ID}`, status: "running", stage: "analysis", message: "生成对比", events: [] };
  comparisonRuns = [];
  summary = {
    run_id: "1009_latest", timestamp: "2026-10-09T12:00:00", run_tag: "test", completion_status: "completed", phase_calibration_status: "completed", analysis_status: "completed", can_analyze: true, can_compare: true, comparison_block_reason: "",
    parameters: { RUN_TAG: "history-tag", ARB_WAVEFORM_FILE: "v1/waveforms/optimal_control_waveform.csv", XY_TRIGGER_FREQ: 200, LOOPBACK_AUX_SCALE: 3.5 }, failure_reason: "", analysis_error: "", analysis_block_reason: "", temperature_control: {}, xy_phase_calibration: { converged: true }, safety_shutdown: { completed: true }, actual_rates: { demod3_noise_Sa_s: 90000 }, hf2_response_snapshot: {}, hf2_noise_snapshot: {},
    artifacts: { "full_analysis.png": "/plot.png?v=1" }, analysis: { response_fit: { success: true, method: "dispersive_curve_fit", slope_V_per_nT: 0.001 }, frequency_response_correction: { applied: true, hwhm_Hz: 560 }, sensitivity: { flat_median_fT_per_sqrtHz: 10, flat_fmin_Hz: 3, flat_fmax_Hz: 560 } },
  };
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit): Promise<any> => {
    if (url === BASE) return { title: "RF 灵敏度（DirectAW）", description: "XYZ 共用时序信号2主输出触发", wiring_notes: [] };
    if (url === `${BASE}/schema`) return { schema_version: 2, fields, parameter_layout: layout };
    if (url === "/api/jobs") return jobs;
    if (url === "/api/jobs/acquisition") return { id: "acquisition", kind: `experiment:${ID}`, status: "running", message: "响应扫描", estimated_remaining_seconds: 120, events: [] };
    if (url === "/api/jobs/analysis") return analysisJob;
    if (url === "/api/jobs/comparison") return comparisonJob;
    if (url === "/api/jobs/comparison/cancel") return { ...comparisonJob, status: "cancelled" };
    if (url === `${BASE}/compare`) {
      comparisonRuns = JSON.parse(String(options?.body)).runs;
      return comparisonJob;
    }
    if (url === "/api/jobs/acquisition/cancel") return { id: "acquisition", kind: `experiment:${ID}`, status: "cancelled", events: [] };
    if (url.startsWith(`${BASE}/runs?`)) return { runs: pageTotal ? [{ ...summary, run_id: url.endsWith("offset=50") ? "1008_older" : summary.run_id }] : [], total: pageTotal, limit: 50, offset: url.endsWith("offset=50") ? 50 : 0 };
    if (url.endsWith("/summary")) return summary;
    if (url === `${BASE}/preflight`) {
      const chosen = JSON.parse(String(options?.body)).parameters.ARB_WAVEFORM_FILE;
      return { ok: preflightOk && Boolean(chosen), errors: !preflightOk ? ["共同触发参数不一致"] : chosen ? [] : ["请选择 oc_sens 中的最优控制波形版本"] };
    }
    if (url === `${BASE}/defaults`) return { schema: { fields, parameter_layout: JSON.parse(String(options?.body)).parameter_layout } };
    if (url === `${BASE}/runs` && options?.method === "POST") return { id: "acquisition", kind: `experiment:${ID}`, status: "running", message: "正在采集", events: [] };
    if (url.endsWith("/analyze")) return analysisJob;
    throw new Error(`未模拟 ${url}`);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("从实验路由进入双 Tab，切换保留参数，历史填回不启动采集", async () => {
  renderPage();
  const tabs = screen.getByRole("tablist", { name: "RF 灵敏度（DirectAW）视图" });
  expect(within(tabs).getAllByRole("tab")).toHaveLength(2);
  expect(await within(screen.getByRole("tabpanel", { name: "运行" })).findByAltText("X 响应与 RF 灵敏度")).toHaveAttribute("src", "/plot.png?v=1");
  expect(screen.getByText(/已作洛伦兹校正/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("运行标签"), { target: { value: "edited" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "填回参数" })).toBeEnabled());
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("edited");
  fireEvent.keyDown(tabs, { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: "历史" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("history-tag");
  expect(screen.getByLabelText("最优控制波形版本")).toHaveValue("v1/waveforms/optimal_control_waveform.csv");
  expect(screen.getByLabelText("共同触发FREQ")).toHaveValue(200);
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
});

it("保存参数与分组布局，并把共同触发同步到两套旧键", async () => {
  renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.change(screen.getByLabelText("共同触发FREQ"), { target: { value: "200" } });
  fireEvent.change(screen.getByLabelText("最优控制波形版本"), { target: { value: "v8/waveforms/optimal_control_waveform.csv" } });
  fireEvent.click(screen.getByRole("button", { name: "运行标签 移至高级参数" }));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  const call = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  const body = JSON.parse(String(call[1]?.body));
  for (const suffix of ["FREQ", "AMPLITUDE", "OFFSET", "DUTY", "PHASE"]) expect(body.parameters[`XY_TRIGGER_${suffix}`]).toEqual(body.parameters[`Z_TRIGGER_${suffix}`]);
  expect(body.parameters.ARB_WAVEFORM_FILE).toBe("v8/waveforms/optimal_control_waveform.csv");
  expect(body.parameter_layout.advanced).toContain("RUN_TAG");
  fireEvent.click(screen.getByRole("tab", { name: /^高级参数/ }));
  expect(screen.getByLabelText("Demod3 输入")).toBeDisabled();
  expect(screen.getByLabelText("回环增益")).toBeEnabled();
});

it("预检失败不启动，空历史仍可浏览", async () => {
  preflightOk = false;
  pageTotal = 0;
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "预检并运行实验" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("共同触发参数不一致")).toBeVisible();
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByText("暂无运行记录。")).toBeVisible();
});

it("恢复活动采集任务，显示 ETA 并使用真实 JobView 取消接口", async () => {
  jobs = [{ id: "acquisition", kind: `experiment:${ID}`, status: "running" }];
  renderPage();
  const cancel = await screen.findByRole("button", { name: "安全停止" });
  expect(screen.getByText(/预计剩余/)).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  fireEvent.click(cancel);
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/api/jobs/acquisition/cancel", { method: "POST" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "安全停止" })).toBeNull());
});

it("成功预检后提交运行，并同步两套触发参数", async () => {
  renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.change(screen.getByLabelText("共同触发FREQ"), { target: { value: "200" } });
  fireEvent.change(screen.getByLabelText("最优控制波形版本"), { target: { value: "v8/waveforms/optimal_control_waveform.csv" } });
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByRole("button", { name: "安全停止" });
  const startIndex = mockApi.mock.calls.findIndex(([url, options]) => url === `${BASE}/runs` && options?.method === "POST");
  const preflightIndex = mockApi.mock.calls.findIndex(([url]) => url === `${BASE}/preflight`);
  expect(startIndex).toBeGreaterThan(preflightIndex);
  const body = JSON.parse(String(mockApi.mock.calls[startIndex][1]?.body));
  expect(body.parameters.XY_TRIGGER_FREQ).toBe(200);
  expect(body.parameters.Z_TRIGGER_FREQ).toBe(200);
  expect(body.parameters.ARB_WAVEFORM_FILE).toBe("v8/waveforms/optimal_control_waveform.csv");
});

it("最优控制版本默认留空，未选择时预检阻止启动，选择后切换 Tab 保留", async () => {
  renderPage();
  const select = await screen.findByLabelText("最优控制波形版本");
  expect(select).toHaveValue("");
  expect(within(select).getByRole("option", { name: "v8" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("请选择 oc_sens 中的最优控制波形版本")).toBeVisible();
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  fireEvent.change(select, { target: { value: "v8/waveforms/optimal_control_waveform.csv" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(select).toHaveValue("v8/waveforms/optimal_control_waveform.csv");
});

it("校相失败只显示诊断，并禁止重新分析", async () => {
  summary = { ...summary, completion_status: "failed", phase_calibration_status: "failed", can_analyze: false, failure_reason: "XY 校相信号低于门槛", analysis_block_reason: "采集尚未完整完成，仅可查看诊断", artifacts: {}, analysis: null };
  renderPage();
  expect(await within(screen.getByRole("tabpanel", { name: "运行" })).findByText("XY 校相信号低于门槛")).toBeVisible();
  expect(screen.queryByAltText("X 响应与 RF 灵敏度")).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await screen.findByRole("button", { name: "重新分析" })).toBeDisabled();
});

it("重新分析完成后刷新所选记录与图片", async () => {
  renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "重新分析" }));
  await screen.findByRole("button", { name: "取消任务" });
  summary = { ...summary, artifacts: { "full_analysis.png": "/plot.png?v=2" } };
  analysisJob = { ...analysisJob, status: "completed" };
  await act(async () => sources.find(source => source.url === "/api/jobs/analysis/events")!.onmessage?.({ data: JSON.stringify({ done: true, status: "completed" }) }));
  await waitFor(() => expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByAltText("X 响应与 RF 灵敏度")).toHaveAttribute("src", "/plot.png?v=2"));
});

it("历史列表支持分页", async () => {
  pageTotal = 51;
  renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "下一页" }));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(`${BASE}/runs?limit=50&offset=50`));
  expect(await screen.findByRole("button", { name: /1008_older/ })).toBeVisible();
});

async function selectTwoRuns() {
  pageTotal = 51;
  renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "选择 1009_latest 进行对比" }));
  expect(screen.getByRole("button", { name: "生成对比" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "选择 1008_older 进行对比" }));
}

it("跨页勾选、图例和 Tab 切换保留顺序，生成后下载，编辑后隐藏旧图", async () => {
  await selectTwoRuns();
  fireEvent.change(screen.getByLabelText("图例：1009_latest"), { target: { value: " Control A " } });
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(screen.getByLabelText("图例：1009_latest")).toHaveValue(" Control A ");
  expect(screen.getByRole("checkbox", { name: "选择 1008_older 进行对比" })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "生成对比" }));
  await screen.findByRole("button", { name: "取消任务" });
  expect(comparisonRuns).toEqual([{ run_id: "1009_latest", legend: "Control A" }, { run_id: "1008_older", legend: "1008_older" }]);
  expect(screen.getByRole("button", { name: "正在生成对比…" })).toBeDisabled();
  expect(screen.getByLabelText("图例：1009_latest")).toBeDisabled();
  comparisonJob = { ...comparisonJob, status: "completed", result: {
    runs: comparisonRuns, warnings: ["1008_older：数值斜率回退，灵敏度谱未作洛伦兹校正。"],
    artifacts: { "full_analysis_comparison.png": "/compare.png", "full_analysis_comparison.pdf": "/compare.pdf" }
  } };
  await act(async () => sources.find(source => source.url === "/api/jobs/comparison/events")!.onmessage?.({ data: JSON.stringify({ done: true, status: "completed" }) }));
  expect(await screen.findByAltText("X 响应与 RF 灵敏度对比")).toHaveAttribute("src", "/compare.png");
  expect(screen.getByText("1008_older：数值斜率回退，灵敏度谱未作洛伦兹校正。")).toBeVisible();
  expect(screen.getByRole("link", { name: "下载 PNG" })).toHaveAttribute("href", "/compare.png");
  expect(screen.getByRole("link", { name: "下载 PDF" })).toHaveAttribute("download");
  fireEvent.change(screen.getByLabelText("图例：1009_latest"), { target: { value: "Changed" } });
  expect(screen.getByText("选择或图例已修改，请重新生成对比。")).toBeVisible();
  expect(screen.queryByAltText("X 响应与 RF 灵敏度对比")).toBeNull();
  expect(screen.queryByRole("link", { name: "下载 PDF" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "移除 1008_older" }));
  expect(screen.getByRole("button", { name: "生成对比" })).toBeDisabled();
});

it("不可对比的运行显示原因且禁用选择，图例校验在提交前生效", async () => {
  summary = { ...summary, can_compare: false, comparison_block_reason: "分析尚未完成，请先重新分析" };
  renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await screen.findByRole("checkbox", { name: "选择 1009_latest 进行对比" })).toBeDisabled();
  expect(screen.getByText("不可对比：分析尚未完成，请先重新分析")).toBeVisible();
});

it("无效图例阻止生成，取消后保留选择并允许重试", async () => {
  await selectTwoRuns();
  fireEvent.change(screen.getByLabelText("图例：1009_latest"), { target: { value: "中文" } });
  expect(screen.getByRole("button", { name: "生成对比" })).toBeDisabled();
  expect(screen.getByText("图例请使用单行英文文字、数字或符号。")).toBeVisible();
  fireEvent.change(screen.getByLabelText("图例：1009_latest"), { target: { value: "Control" } });
  fireEvent.click(screen.getByRole("button", { name: "生成对比" }));
  fireEvent.click(await screen.findByRole("button", { name: "取消任务" }));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/api/jobs/comparison/cancel", { method: "POST" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "生成对比" })).toBeEnabled());
  expect(screen.getByLabelText("图例：1009_latest")).toHaveValue("Control");
  expect(screen.queryByAltText("X 响应与 RF 灵敏度对比")).toBeNull();
});

it("提交失败保留选择与图例，并显示错误", async () => {
  await selectTwoRuns();
  mockApi.mockRejectedValueOnce(new Error("所选运行正在重新分析"));
  fireEvent.click(screen.getByRole("button", { name: "生成对比" }));
  expect(await screen.findByText(/所选运行正在重新分析/)).toBeVisible();
  expect(screen.getByRole("button", { name: "生成对比" })).toBeEnabled();
  expect(screen.getByLabelText("图例：1009_latest")).toBeVisible();
  expect(screen.getByLabelText("图例：1008_older")).toBeVisible();
});

it("恢复正在执行的对比任务，完成后恢复图例和预览", async () => {
  jobs = [{ id: "comparison", kind: `comparison:${ID}`, status: "running" }];
  renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "取消任务" });
  comparisonJob = { ...comparisonJob, status: "completed", result: {
    runs: [{ run_id: "1009_latest", legend: "Recovered A" }, { run_id: "1008_older", legend: "Recovered B" }],
    warnings: [], artifacts: { "full_analysis_comparison.png": "/recovered.png", "full_analysis_comparison.pdf": "/recovered.pdf" }
  } };
  await act(async () => sources.find(source => source.url === "/api/jobs/comparison/events")!.onmessage?.({ data: JSON.stringify({ done: true, status: "completed" }) }));
  expect(await screen.findByAltText("X 响应与 RF 灵敏度对比")).toHaveAttribute("src", "/recovered.png");
  expect(screen.getByLabelText("图例：1009_latest")).toHaveValue("Recovered A");
  expect(screen.getByLabelText("图例：1008_older")).toHaveValue("Recovered B");
});
