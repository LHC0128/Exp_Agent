import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import flow from "./knownNoiseWorkflow.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false }) }));
vi.mock("../components/JobView", () => ({
  JobView: ({ job, onUpdate }: any) => job
    ? <button onClick={() => onUpdate({ ...job, status: "completed" })}>完成测试任务</button>
    : null,
}));

const mockApi = vi.mocked(api);
const BASE = "/api/experiments/noise-spectrum-xy-known-noise";
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "known_noise", group: "basic", unit: "" },
  { name: "NOISE_REPEAT_FREQ_HZ", label: "波形重复频率", type: "number", default: 4, group: "basic", unit: "Hz" },
  { name: "NOISE_AMPLITUDE_VPP", label: "噪声注入幅度", type: "number", default: 1, group: "basic", unit: "Vpp" },
  { name: "NOISE_BAND_STARTS_HZ", label: "噪声段起点", type: "array", default: [300], group: "basic", unit: "Hz" },
  { name: "NOISE_BAND_STOPS_HZ", label: "噪声段终点", type: "array", default: [30000], group: "basic", unit: "Hz" },
  { name: "NOISE_BAND_PSDS_V2_PER_HZ", label: "噪声段设计谱密度", type: "array", default: [1e-6], group: "basic", unit: "V²/Hz" },
  { name: "HF2_DEMOD_RATE", label: "校相采样率", type: "number", default: 200000, group: "advanced", unit: "Sa/s", description: "用于 HF2 校相" },
  { name: "TEMP_SWITCH_ON_SETTLE_S", label: "恢复温控等待时间", type: "number", default: 2, group: "basic", unit: "s" },
  { name: "ANALYSIS_BIN_WIDTH_HZ", label: "频率格间距", type: "number", default: 50, group: "basic", unit: "Hz" },
  { name: "ANALYSIS_DISPERSION_SOURCE_RUN", label: "静磁场标定曲线", type: "string", default: "", group: "basic", unit: "", options: [
    { value: "", label: "待选择标定曲线" }, { value: "static-v3", label: "静磁场 v3 · 90 °C" }, { value: "other-v3", label: "静磁场 v3 · 100 °C" },
  ] },
];
let summary: any;
let activeSession: any;
let extraRuns: any[];
let defaults: Record<string, any>;
let parameterLayout: { basic: string[]; advanced: string[] };
const makeSession = () => ({ session_id: "known-session", job_id: "known-session-job", revision: 0,
  status: "waiting", parameters: { ...defaults },
  steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: "pending", error: "" }])),
  outputs: { X: "ON", Y: "ON", Z: "OFF", 温控: "ON", 共用触发: "ON" }, pending_request: null });

const openParameters = async (id: string) => {
  const stage = [...flow.acquisition, ...flow.analysis].find(item => item.id === id)!;
  const region = await screen.findByLabelText(`${id} ${stage.title} 参数`);
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  return within(region);
};

const renderPage = () => render(
  <MemoryRouter initialEntries={["/experiments/noise-spectrum-xy-known-noise"]}>
    <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
  </MemoryRouter>,
);

beforeEach(() => {
  activeSession = null;
  extraRuns = [];
  defaults = Object.fromEntries(fields.map(field => [field.name, field.default]));
  parameterLayout = { basic: fields.filter(field => field.group === "basic").map(field => field.name), advanced: ["HF2_DEMOD_RATE"] };
  summary = {
    run_id: "0922_1000_known_noise",
    timestamp: "0922_1000",
    run_tag: "history-tag",
    completion_status: "completed",
    analysis_status: "completed",
    analysis_error: "",
    parameters: { RUN_TAG: "history-tag" },
    artifacts: { "measured_vs_truth.png": "/compare.png?v=1" },
  };
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === BASE) return { title: "XY 控制测量已知可控噪声谱", description: "已知噪声验证", wiring_notes: ["确认 Z 链路接线"] };
    if (url === `${BASE}/schema`) return { schema_version: 3, fields: fields.map(field => ({ ...field, default: defaults[field.name] })), parameter_layout_saved: true, parameter_layout: parameterLayout };
    if (url === "/api/jobs") return [];
    if (url === `${BASE}/workflow`) return flow;
    if (url === `${BASE}/sessions/active`) return activeSession;
    if (url === "/api/jobs/known-session-job") return { id: "known-session-job", kind: "session:noise-spectrum-xy-known-noise", status: "running", events: [] };
    if (url === `${BASE}/sessions` && options?.method === "POST") return activeSession = makeSession();
    if (url === `${BASE}/sessions/known-session/parameters`) {
      const parameters = JSON.parse(String(options?.body)).parameters;
      const impacts = flow.parameter_impacts.acquisition as Record<string, string[]>;
      const impacted = new Set(Object.keys(parameters).filter(name => JSON.stringify(parameters[name]) !== JSON.stringify(activeSession.parameters[name])).flatMap(name => impacts[name] ?? []));
      return activeSession = { ...activeSession, revision: activeSession.revision + 1, parameters,
        steps: Object.fromEntries(Object.entries(activeSession.steps).map(([id, state]: [string, any]) => [id,
          impacted.has(id) && state.status !== "pending" ? { status: "stale", error: "" } : state])) };
    }
    if (url === `${BASE}/sessions/known-session/steps`) {
      const body = JSON.parse(String(options?.body));
      const missing = flow.acquisition.filter(stage => body.step_ids.includes(stage.id)).flatMap(stage =>
        stage.dependencies.filter(id => !body.step_ids.includes(id) && activeSession.steps[id].status !== "completed"));
      if (missing.length) throw new Error("缺少前置：" + missing.join("、"));
      return activeSession = { ...activeSession, revision: activeSession.revision + 1,
        steps: { ...activeSession.steps, ...Object.fromEntries(body.step_ids.map((id: string) => [id, { status: "completed", error: "" }])) } };
    }
    if (url === `${BASE}/sessions/known-session/finish`) return activeSession = { ...activeSession, status: "closed", revision: activeSession.revision + 1 };
    if (url.endsWith("/analysis") && options?.method === "POST") return { id: "analysis", status: "running", events: [] };
    if (url.endsWith("/analysis")) return { run_id: decodeURIComponent(url.split("/").at(-2)!), revision: 3,
      options: { bin_width_hz: 50, dispersion_source_run: "", ratio_pass_low: .5, ratio_pass_high: 2 },
      steps: Object.fromEntries(flow.analysis.map(stage => [stage.id, { status: stage.id < "B05" ? "completed" : "waiting_calibration", error: "" }])),
      summary: { status: "waiting_calibration" }, fingerprint: {} };
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary, ...extraRuns], total: 1 + extraRuns.length };
    if (url.endsWith("/summary")) return summary;
    if (url === `${BASE}/preflight`) return { ok: true, errors: [] };
    if (url === `${BASE}/derive`) {
      return { values: {
        sample_rate_sa_s: 65536, line_spacing_hz: 4, nyquist_hz: 32768,
        design_peak_v: 0.84, aligned_amplitude_vpp: 1.68, realized_psd_scale: 0.35,
        waveform_rms_v: 0.10, equivalent_noise_hz_per_rt_hz: 6.95,
        z_calibration_k_hz_per_v: 11703.678,
        nperseg: 9000, segments_per_point: 22, bin_width_hz: 50, scan_seconds: 3000,
      } };
    }
    if (url === `${BASE}/defaults`) {
      const body = JSON.parse(String(options?.body));
      defaults = body.parameters; parameterLayout = body.parameter_layout;
      return { message: "参数与布局已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: parameterLayout } };
    }
    if (url === `${BASE}/runs` && options?.method === "POST") return { id: "acquisition", kind: "experiment:noise-spectrum-xy-known-noise", status: "running", events: [] };
    if (url.endsWith("/analyze")) return { id: "analysis", status: "running", events: [] };
    throw new Error(`未模拟 ${url}`);
  });
});

afterEach(cleanup);

it("读取真实节点定义，依赖拒绝保留显式选择并提供原图下载", async () => {
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await waitFor(() => expect(screen.getByLabelText("A13 ON/OFF 配对扫描")).toBeEnabled());
  fireEvent.click(screen.getByLabelText("A13 ON/OFF 配对扫描"));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  expect(await screen.findByText("Error: 缺少前置：A12")).toBeVisible();
  expect(screen.getByLabelText("A12 DAQ 准备")).not.toBeChecked();
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  expect(await screen.findByText(/已结束会话的执行记录/)).toBeVisible();
});

it("刷新恢复会话，空闲改参先应用并携带修订，再显式重跑", async () => {
  activeSession = { ...makeSession(), revision: 7, parameters: { ...defaults, RUN_TAG: "existing" },
    steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: stage.id === "A15" ? "pending" : "completed", error: "" }])) };
  renderPage();
  const scan = await openParameters("A13");
  await waitFor(() => expect(scan.getByLabelText("运行标签")).toHaveValue("existing"));
  fireEvent.click(screen.getByLabelText("A13 ON/OFF 配对扫描"));
  const run = screen.getByRole("button", { name: "运行所选 / 重跑" });
  await waitFor(() => expect(run).toBeEnabled());
  fireEvent.change(scan.getByLabelText("运行标签"), { target: { value: "next-scan" } });
  expect(run).toBeDisabled();
  expect(screen.getByText(/^预计需重跑的已完成步骤/)).toHaveTextContent("A13 ON/OFF 配对扫描");
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(run).toBeEnabled());
  const update = mockApi.mock.calls.find(([url]) => url.endsWith("known-session/parameters"))!;
  const body = JSON.parse(String(update[1]?.body));
  expect(body.revision).toBe(7);
  expect(Object.keys(body.parameters).filter(name => JSON.stringify(body.parameters[name]) !== JSON.stringify(defaults[name]))).toEqual(["RUN_TAG"]);
  expect(activeSession.steps.A12.status).toBe("completed");
  expect(activeSession.steps.A13.status).toBe("stale");
  fireEvent.click(run);
  await waitFor(() => expect(activeSession.steps.A13.status).toBe("completed"));
  const request = mockApi.mock.calls.find(([url]) => url.endsWith("known-session/steps"))!;
  expect(JSON.parse(String(request[1]?.body))).toEqual({ revision: 8, step_ids: ["A13"] });
});

it("恢复温控参数在三个业务节点同步，分类与保存后刷新同步", async () => {
  const page = renderPage();
  const demod = await openParameters("A08"), phase = await openParameters("A10"), scan = await openParameters("A13");
  fireEvent.change(phase.getByLabelText("恢复温控等待时间 (s)"), { target: { value: "3" } });
  for (const node of [demod, phase, scan]) expect(node.getByLabelText("恢复温控等待时间 (s)")).toHaveValue(3);
  fireEvent.click(phase.getByRole("button", { name: "恢复温控等待时间 移至高级参数" }));
  for (const node of [demod, phase, scan]) expect(node.getByLabelText("恢复温控等待时间 (s)")).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  expect(parameterLayout.advanced).toContain("TEMP_SWITCH_ON_SETTLE_S");
  page.unmount(); renderPage();
  const recovered = await openParameters("A10");
  fireEvent.click(recovered.getByText("高级参数（1）"));
  expect(recovered.getByLabelText("恢复温控等待时间 (s)")).toHaveValue(3);
});

it("未选来源显示等待，历史目录与下一次实验分别保留分析草稿", async () => {
  extraRuns = [{ ...summary, run_id: "other-known-run" }];
  renderPage();
  const next = await openParameters("B05");
  fireEvent.change(next.getByLabelText("静磁场标定曲线"), { target: { value: "static-v3" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: summary.run_id } });
  expect(await screen.findByText(/谱分离已完成，待选择标定曲线后执行/)).toBeVisible();
  const first = await openParameters("B05");
  expect(first.getByLabelText("静磁场标定曲线")).toHaveValue("");
  fireEvent.change(first.getByLabelText("静磁场标定曲线"), { target: { value: "other-v3" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "other-known-run" } });
  const second = await openParameters("B05");
  fireEvent.change(second.getByLabelText("静磁场标定曲线"), { target: { value: "static-v3" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: summary.run_id } });
  const restored = await openParameters("B05");
  expect(restored.getByLabelText("静磁场标定曲线")).toHaveValue("other-v3");
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  expect(defaults.ANALYSIS_DISPERSION_SOURCE_RUN).toBe("static-v3");
  fireEvent.click(screen.getByLabelText("B05 选择静磁场曲线并拟合检测增益"));
  fireEvent.click(screen.getByLabelText("B06 增益换算、注入真值比较与汇总"));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url.endsWith("/analysis") && options?.method === "POST")).toBe(true));
  const request = mockApi.mock.calls.find(([url, options]) => url.endsWith("/analysis") && options?.method === "POST")!;
  const body = JSON.parse(String(request[1]?.body));
  expect(body.step_ids).toEqual(["B05", "B06"]);
  expect(body.revision).toBe(3);
  expect(body.analysis_parameters.ANALYSIS_DISPERSION_SOURCE_RUN).toBe("other-v3");
  expect(body.analysis_parameters).not.toHaveProperty("DISPERSION_DURATION_S");
});

it("节点内调整校相参数并保存，完整运行不携带旧色散采集参数", async () => {
  renderPage();
  const node = await openParameters("A08");
  fireEvent.click(node.getByText("高级参数（1）"));
  fireEvent.change(node.getByLabelText("校相采样率 (Sa/s)"), { target: { value: "150000" } });
  expect(screen.queryByLabelText("色散每点采集时间 (s)")).toBeNull();
  const workflow = within(screen.getByRole("region", { name: "交互实验流程图" }));
  fireEvent.click(workflow.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  fireEvent.click(workflow.getByRole("button", { name: "完整运行" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(true));
  for (const [url, options] of mockApi.mock.calls.filter(([url, options]) => url === `${BASE}/defaults` || (url === `${BASE}/runs` && options?.method === "POST"))) {
    const body = JSON.parse(String(options?.body));
    expect(body.parameters.HF2_DEMOD_RATE).toBe(150000);
    expect(body.parameters.ANALYSIS_DISPERSION_SOURCE_RUN).toBe("");
    expect(body.parameters).not.toHaveProperty("DISPERSION_DURATION_S");
    if (url.endsWith("defaults")) expect(body.parameter_layout.advanced).toEqual(["HF2_DEMOD_RATE"]);
  }
});

it("显示波形预算预览与真值链 K_Z，谱段数组按逗号分隔编辑", async () => {
  renderPage();
  const preview = await screen.findByText("波形、注入真值与 Welch 预览");
  fireEvent.click(preview);
  await openParameters("A11");
  expect(await screen.findByText((content) => content.includes("波形：采样率 65536 Sa/s"))).toBeVisible();
  expect(await screen.findByText((content) => content.includes("等效频率噪声（带内中位）6.950 Hz/√Hz"))).toBeVisible();
  expect(await screen.findByText((content) => content.includes("达到设计谱密度需设注入幅度 1.680 Vpp"))).toBeVisible();
  // 一键对齐把幅度写入为提示值，已对齐时按钮禁用。
  fireEvent.click(screen.getByRole("button", { name: "对齐注入幅度" }));
  expect(screen.getByRole("spinbutton", { name: /噪声注入幅度/ })).toHaveValue(1.68);
  await waitFor(() => expect(screen.getByRole("button", { name: "对齐注入幅度" })).toBeDisabled());
  const starts = await screen.findByRole("textbox", { name: /噪声段起点/ });
  expect(starts).toHaveValue("300");
  fireEvent.change(starts, { target: { value: "300, 5000" } });
  fireEvent.blur(starts);
  expect(screen.getByRole("textbox", { name: /噪声段起点/ })).toHaveValue("300, 5000");
});

it("结果区展示判据通过与中位比值，历史详情可用", async () => {
  summary.analysis_quality = { comparison: { valid_points: 40, total_points: 111, median_ratio: 1.23, criteria_pass: true, negative_differential_points: 3, pass_low: 0.5, pass_high: 2.0 }, truth_chain: { K_Z_Hz_per_V: 11703.678, dispersion_slope_v_per_hz: 0.0002 } };
  renderPage();
  const panel = screen.getByRole("tabpanel", { name: "运行" });
  expect(await within(panel).findByText(/判据通过：带内中位比值 1\.230/)).toBeVisible();
  expect(within(panel).getByAltText("测得谱与真值谱对比")).toHaveAttribute("src", "/compare.png?v=1");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "填回参数" })).toBeEnabled());
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  await openParameters("A13");
  expect(screen.getByLabelText("运行标签")).toHaveValue("known_noise");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("history-tag");
});

it("从真实路由进入双 Tab，预检失败不启动采集", async () => {
  renderPage();
  expect(within(screen.getByRole("tablist", { name: "XY 已知噪声注入实验视图" })).getAllByRole("tab")).toHaveLength(3);
  await waitFor(() => expect(screen.getByRole("button", { name: "完整运行" })).toBeEnabled());
  const normal = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/preflight`) return { ok: false, errors: ["噪声段超出 Nyquist"] };
    return normal(url, options);
  });
  fireEvent.click(screen.getByRole("button", { name: "完整运行" }));
  expect(await screen.findByText("噪声段超出 Nyquist")).toBeVisible();
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
});

it("背景不可辨识时显示空值并保留有符号差分统计", async () => {
  summary.analysis_quality = { comparison: {
    valid_points: 40, total_points: 111, median_ratio: null, criteria_pass: false,
    negative_differential_points: 3, signed_differences_retained: true,
    in_band_valid_points: 12, in_band_total_points: 100,
    in_band_background: { median_ratio: null, resolved_points: 0, total_points: 100 },
    out_of_band_background: { median_ratio: 1.02, resolved_points: 40, total_points: 111 },
  } };
  renderPage();
  const panel = screen.getByRole("tabpanel", { name: "运行" });
  expect(await within(panel).findByText(/判据未通过：带内中位比值 —/)).toBeVisible();
  expect(within(panel).getByText(/非正差分 3 个已保留/)).toBeVisible();
  expect(within(panel).getByText(/带内背景 ON\/OFF 中位比值：—；\s*可辨识 0 \/ 100 点/)).toBeVisible();
  expect(within(panel).getByText(/带外背景 ON\/OFF 中位比值：1.020/)).toBeVisible();
});

it("架构 Tab 内嵌总览与子图切换，可在新页面打开", async () => {
  renderPage();
  const tablist = screen.getByRole("tablist", { name: "XY 已知噪声注入实验视图" });
  fireEvent.click(within(tablist).getByRole("tab", { name: "架构" }));
  const panel = screen.getByRole("tabpanel", { name: "架构" });
  const frame = within(panel).getByTitle("XY 已知可控噪声谱实验采集架构图");
  expect(frame).toHaveAttribute("src", "/architecture/noise-spectrum-xy-known-noise.html");
  expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-same-origin allow-downloads allow-popups");
  expect(within(panel).getByRole("link", { name: "在新页面打开" })).toHaveAttribute("href", "/architecture/noise-spectrum-xy-known-noise.html");
  fireEvent.click(within(panel).getByRole("tab", { name: "单点时序" }));
  expect(within(panel).getByTitle("XY 已知可控噪声谱实验单控制点时序图")).toHaveAttribute("src", "/architecture/noise-spectrum-xy-known-noise.timing.html");
  fireEvent.click(within(panel).getByRole("tab", { name: "数据分析" }));
  expect(within(panel).getByTitle("XY 已知可控噪声谱实验离线分析与真值链图")).toHaveAttribute("src", "/architecture/noise-spectrum-xy-known-noise.dataflow.html");
  // 键盘导航在三个 Tab 间循环，运行左移回绕到架构。
  fireEvent.click(within(tablist).getByRole("tab", { name: "运行" }));
  const runTab = within(tablist).getByRole("tab", { name: "运行" });
  runTab.focus();
  fireEvent.keyDown(tablist, { key: "ArrowLeft" });
  const archTab = within(tablist).getByRole("tab", { name: "架构" });
  expect(archTab).toHaveFocus();
  expect(archTab).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(tablist, { key: "ArrowRight" });
  expect(within(tablist).getByRole("tab", { name: "运行" })).toHaveFocus();
});
