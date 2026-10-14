import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import flow from "./probeAmWorkflow.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false }) }));
vi.mock("../components/JobView", () => ({
  JobView: ({ job, onUpdate }: any) => job
    ? <button onClick={() => onUpdate({ ...job, status: "completed" })}>完成测试任务</button>
    : null,
}));

const mockApi = vi.mocked(api);
const BASE = "/api/experiments/noise-spectrum-xy-uncontrolled-probe-am";
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "probe_am", group: "basic", unit: "" },
  { name: "NOISE_REPEAT_FREQ_HZ", label: "噪声波形重复频率", type: "number", default: 10, group: "basic", unit: "Hz" },
  { name: "NOISE_AMPLITUDE_VPP", label: "AM 噪声输出幅度", type: "number", default: 2, group: "basic", unit: "Vpp" },
  { name: "NOISE_BAND_STARTS_HZ", label: "设计噪声谱段起点", type: "array", default: [10000], group: "basic", unit: "Hz" },
  { name: "NOISE_BAND_STOPS_HZ", label: "设计噪声谱段终点", type: "array", default: [20000], group: "basic", unit: "Hz" },
  { name: "NOISE_BAND_PSDS_V2_PER_HZ", label: "设计噪声谱密度", type: "array", default: [1], group: "basic", unit: "V²/Hz" },
  { name: "PROBE_AOM_CARRIER_VPP", label: "Probe AOM 载波幅度", type: "number", default: 0.05, group: "basic", unit: "Vpp" },
  { name: "PROBE_AOM_AM_DEPTH_PERCENT", label: "外部 AM 深度", type: "number", default: 100, group: "basic", unit: "%" },
  { name: "INJECT_CONTROLLED_NOISE", label: "注入 Z 已知可控噪声", type: "boolean", default: false, group: "basic", unit: "" },
  { name: "Z_NOISE_AMPLITUDE_VPP", label: "Z 噪声输出幅度", type: "number", default: 0.0034, group: "basic", unit: "Vpp" },
  { name: "ANALYSIS_BIN_WIDTH_HZ", label: "频率格间距", type: "number", default: 50, group: "basic", unit: "Hz" },
  { name: "HF2_DEMOD_RATE", label: "校相采样率", type: "number", default: 200000, group: "advanced", unit: "Sa/s" },
];
let summary: any;
let activeSession: any;
let extraRuns: any[];
let defaults: Record<string, any>;
let parameterLayout: { basic: string[]; advanced: string[] };
const makeSession = () => ({ session_id: "probe-session", job_id: "probe-session-job", revision: 0,
  status: "waiting", parameters: { ...defaults },
  steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: "pending", error: "" }])),
  outputs: { X: "ON", Y: "ON", Z: "OFF", 温控: "ON", 共用触发: "ON", "Probe AM": "载波 ON·噪声 OFF" }, pending_request: null });

const openParameters = async (id: string) => {
  const stage = [...flow.acquisition, ...flow.analysis].find(item => item.id === id)!;
  const region = await screen.findByLabelText(`${id} ${stage.title} 参数`);
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  return within(region);
};

const openPreview = async () => {
  const summary = await screen.findByText("AM 噪声波形、可选 Z 注入与 Welch 预览");
  const details = summary.closest("details")!;
  if (!details.open) fireEvent.click(summary);
  return within(details);
};

const renderPage = () => render(
  <MemoryRouter initialEntries={["/experiments/noise-spectrum-xy-uncontrolled-probe-am"]}>
    <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
  </MemoryRouter>,
);

beforeEach(() => {
  activeSession = null;
  extraRuns = [];
  defaults = Object.fromEntries(fields.map(field => [field.name, field.default]));
  parameterLayout = { basic: fields.filter(field => field.group === "basic").map(field => field.name), advanced: ["HF2_DEMOD_RATE"] };
  summary = {
    run_id: "0923_1200_probe_am", timestamp: "0923_1200", run_tag: "history-probe",
    completion_status: "completed", analysis_status: "quality_failed",
    analysis_error: "移动峰自动标定未通过；已按运行参数 K/B 生成暂定控制轴，结果仅供暂时分析",
    parameters: { RUN_TAG: "history-probe", NOISE_AMPLITUDE_VPP: 2, INJECT_CONTROLLED_NOISE: true },
    analysis_quality: {
      calibration: { k: 14270, b: -100, support_points: 200, source: "configured_K_B" },
      warnings: ["未检测到足够显著的移动峰；本次按运行参数 K/B 推算峰位，图和拟合仅供暂时分析。"],
      fit_quality: {
        valid_background_bins: 7, valid_controlled_bins: 8, total_bins: 10,
        negative_delta_bins: 3, positive_delta_bins: 4,
        negative_controlled_delta_bins: 2, positive_controlled_delta_bins: 6,
        background_method_on: { off_resonance_plateau: 4 }, background_method_off: { off_resonance_plateau: 5 },
      },
    },
    artifacts: {
      "noise_spectrum_2d.png": "/average.png",
      "controlled_uncontrolled_comparison.png": "/states.png",
      "lorentzian_line_shapes.png": "/lorentzian.png",
      "signed_spectral_difference.png": "/difference.png",
    },
  };
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === BASE) return { title: "XY 控制测量已知不可控 Probe AM 噪声谱", description: "Probe AM", wiring_notes: ["Probe AOM CH1/CH2"] };
    if (url === `${BASE}/schema`) return { schema_version: 2, fields: fields.map(field => ({ ...field, default: defaults[field.name] })), parameter_layout_saved: true, parameter_layout: parameterLayout };
    if (url === "/api/jobs") return [];
    if (url === `${BASE}/workflow`) return flow;
    if (url === `${BASE}/sessions/active`) return activeSession;
    if (url === "/api/jobs/probe-session-job") return { id: "probe-session-job", kind: "session:noise-spectrum-xy-uncontrolled-probe-am", status: "running", events: [] };
    if (url === `${BASE}/sessions` && options?.method === "POST") return activeSession = makeSession();
    if (url === `${BASE}/sessions/probe-session/parameters`) {
      const parameters = JSON.parse(String(options?.body)).parameters;
      const impacts = flow.parameter_impacts.acquisition as Record<string, string[]>;
      const impacted = new Set(Object.keys(parameters).filter(name => JSON.stringify(parameters[name]) !== JSON.stringify(activeSession.parameters[name])).flatMap(name => impacts[name] ?? []));
      return activeSession = { ...activeSession, revision: activeSession.revision + 1, parameters,
        steps: Object.fromEntries(Object.entries(activeSession.steps).map(([id, state]: [string, any]) => [id,
          impacted.has(id) && state.status !== "pending" ? { status: "stale", error: "" } : state])) };
    }
    if (url === `${BASE}/sessions/probe-session/steps`) {
      const body = JSON.parse(String(options?.body));
      const missing = flow.acquisition.filter(stage => body.step_ids.includes(stage.id)).flatMap(stage =>
        stage.dependencies.filter(id => !body.step_ids.includes(id) && activeSession.steps[id].status !== "completed"));
      if (missing.length) throw new Error("缺少前置：" + missing.join("、"));
      return activeSession = { ...activeSession, revision: activeSession.revision + 1,
        steps: { ...activeSession.steps, ...Object.fromEntries(body.step_ids.map((id: string) => [id, { status: "completed", error: "" }])) } };
    }
    if (url === `${BASE}/sessions/probe-session/finish`) return activeSession = { ...activeSession, status: "closed", revision: activeSession.revision + 1 };
    if (url.endsWith("/analysis") && options?.method === "POST") return { id: "analysis", status: "running", events: [] };
    if (url.endsWith("/analysis")) return { run_id: decodeURIComponent(url.split("/").at(-2)!), revision: 2,
      options: { bin_width_hz: 50, frequency_min_hz: 50, frequency_max_hz: 1000, fit_half_width_hz: 400 },
      steps: Object.fromEntries(flow.analysis.map(stage => [stage.id, { status: "completed", error: "" }])),
      summary: { status: "completed" }, fingerprint: {} };
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary, ...extraRuns], total: 1 + extraRuns.length };
    if (url.endsWith("/summary")) return summary;
    if (url === `${BASE}/preflight`) return { ok: true, errors: [] };
    if (url === `${BASE}/derive`) return { values: {
      sample_rate_sa_s: 163840, nyquist_hz: 81920, line_spacing_hz: 10,
      design_peak_v: 0.125, aligned_amplitude_vpp: 0.25, aligned_amplitude_safe: true,
      z_aligned_amplitude_vpp: 0.02, z_aligned_amplitude_safe: true,
      nperseg: 900, bin_width_hz: 50, scan_seconds: 3000 } };
    if (url === `${BASE}/defaults`) {
      const body = JSON.parse(String(options?.body));
      defaults = body.parameters; parameterLayout = body.parameter_layout;
      return { message: "参数与布局已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: parameterLayout } };
    }
    if (url === `${BASE}/runs` && options?.method === "POST") return { id: "acquisition", kind: "experiment:noise-spectrum-xy-uncontrolled-probe-am", status: "running", events: [] };
    if (url.endsWith("/analyze")) return { id: "analysis", status: "running", events: [] };
    throw new Error(`未模拟 ${url}`);
  });
});

afterEach(cleanup);

it("读取真实节点定义，依赖拒绝保留显式选择并提供原图下载", async () => {
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await waitFor(() => expect(screen.getByLabelText("A14 AM 噪声 ON/OFF 配对扫描")).toBeEnabled());
  fireEvent.click(screen.getByLabelText("A14 AM 噪声 ON/OFF 配对扫描"));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  expect(await screen.findByText("Error: 缺少前置：A13")).toBeVisible();
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  expect(await screen.findByText(/已结束会话的执行记录/)).toBeVisible();
});

it("Probe AOM 载波参数在专属节点编辑，改 Z 注入后预览受影响模块", async () => {
  renderPage();
  const carrier = await openParameters("A08");
  expect(carrier.getByLabelText("外部 AM 深度 (%)")).toHaveValue(100);
  const noise = await openParameters("A12");
  fireEvent.change(noise.getByLabelText("注入 Z 已知可控噪声"), { target: { value: "true" } });
  const preview = await openPreview();
  expect(preview.getByText(/Z 可控噪声将持续注入/)).toBeVisible();
  const probeAlign = preview.getByRole("button", { name: "对齐 Probe AM 幅度" });
  const zAlign = preview.getByRole("button", { name: "对齐 Z 噪声幅度" });
  await waitFor(() => expect(probeAlign).toBeEnabled());
  fireEvent.click(probeAlign);
  await waitFor(() => expect(zAlign).toBeEnabled());
  fireEvent.click(zAlign);
  expect(noise.getByLabelText("AM 噪声输出幅度 (Vpp)")).toHaveValue(0.25);
  expect(noise.getByLabelText("Z 噪声输出幅度 (Vpp)")).toHaveValue(0.02);
});

it("历史填回参数与质量未通过信息，保留交互流程图", async () => {
  renderPage();
  const runTab = screen.getByRole("tabpanel", { name: "运行" });
  await within(runTab).findByText(/移动峰自动标定未通过/);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await screen.findByRole("button", { name: "填回参数" })).toBeEnabled();
  const historyPanel = screen.getByRole("tabpanel", { name: "历史" });
  expect(within(historyPanel).getByAltText("平均 PSD 二维谱")).toHaveAttribute("src", "/average.png");
  expect(within(historyPanel).getByText(/暂定控制轴 K = 14270.0 Hz\/V，B = -100.0 Hz/)).toBeVisible();
  expect(within(historyPanel).getByText(/受控响应与不可控背景的基线和有符号差谱（双 Y 轴）/)).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  const node = await openParameters("A12");
  expect(node.getByLabelText("注入 Z 已知可控噪声")).toHaveValue("false");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  const restored = await openParameters("A12");
  expect(restored.getByLabelText("注入 Z 已知可控噪声")).toHaveValue("true");
  const tablist = screen.getByRole("tablist", { name: "XY Probe AM 噪声实验视图" });
  expect(within(tablist).getAllByRole("tab")).toHaveLength(2);
  expect(within(tablist).queryByRole("tab", { name: "架构" })).not.toBeInTheDocument();
  expect(document.querySelector('iframe[src^="/architecture/"]')).toBeNull();
  const runButton = within(tablist).getByRole("tab", { name: "运行" });
  const historyButton = within(tablist).getByRole("tab", { name: "历史" });
  runButton.focus();
  for (const key of ["ArrowLeft", "ArrowRight"]) {
    fireEvent.keyDown(tablist, { key });
    expect(historyButton).toHaveFocus();
    expect(historyButton).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(tablist, { key });
    expect(runButton).toHaveFocus();
    expect(runButton).toHaveAttribute("aria-selected", "true");
  }
  expect(screen.getByRole("region", { name: "交互实验流程图" })).toBeVisible();
  expect(restored.getByLabelText("注入 Z 已知可控噪声")).toHaveValue("true");
});

it("完整运行携带会话参数；预检失败不启动采集", async () => {
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "预检并运行实验" })).toBeEnabled());
  const normal = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/preflight`) return { ok: false, errors: ["AM 输入电压超限"] };
    return normal(url, options);
  });
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("AM 输入电压超限")).toBeVisible();
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
});
