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
  {"name": "SYSTEM_SETTLE_S", "label": "系统稳定时间", "type": "number", "default": 0.5, "group": "basic", "unit": "s"},
  {"name": "RUN_TAG", "label": "运行标签", "type": "string", "default": "probe_am", "group": "basic", "unit": ""},
  {"name": "TARGET_NOISE_FREQ_START_HZ", "label": "目标噪声频率起点", "type": "number", "default": 0.0, "group": "basic", "unit": "Hz"},
  {"name": "TARGET_NOISE_FREQ_STOP_HZ", "label": "目标噪声频率终点", "type": "number", "default": 50000.0, "group": "basic", "unit": "Hz"},
  {"name": "TARGET_NOISE_FREQ_POINTS", "label": "目标频率扫描点数", "type": "integer", "default": 500, "group": "basic", "unit": ""},
  {"name": "TEMP_SWITCH_ON_SETTLE_S", "label": "恢复温控等待时间", "type": "number", "default": 3.0, "group": "advanced", "unit": "s"},
  {"name": "PUMP_MOD_FREQ", "label": "Pump 调制频率", "type": "number", "default": 90000.0, "group": "basic", "unit": "Hz"},
  {"name": "PUMP_MOD_AMPLITUDE", "label": "Pump 载波幅度", "type": "number", "default": 0.18, "group": "basic", "unit": "Vpp"},
  {"name": "PUMP_MOD_DUTY", "label": "Pump 占空比", "type": "number", "default": 5.0, "group": "basic", "unit": "%"},
  {"name": "RF_GATE_AMPLITUDE", "label": "RF 门控幅度", "type": "number", "default": 5.0, "group": "advanced", "unit": "Vpp"},
  {"name": "RF_GATE_OFFSET", "label": "RF 门控偏置", "type": "number", "default": 2.5, "group": "advanced", "unit": "V"},
  {"name": "XY_CTRL_FREQ", "label": "XY 控制载波频率", "type": "number", "default": 90000.0, "group": "basic", "unit": "Hz"},
  {"name": "XY_CTRL_PHASE", "label": "XY 整体相位", "type": "number", "default": 90.0, "group": "advanced", "unit": "deg"},
  {"name": "XY_CTRL_QUAD", "label": "XY 正交相位差", "type": "number", "default": 90.0, "group": "advanced", "unit": "deg"},
  {"name": "XY_CALIB_ENVELOPE_V", "label": "相位校准正弦峰值", "type": "number", "default": 1.5, "group": "advanced", "unit": "V"},
  {"name": "XY_PHASE_CAL_TOL_DEG", "label": "XY 相位校准容差", "type": "number", "default": 1.0, "group": "advanced", "unit": "deg"},
  {"name": "XY_PHASE_CAL_MAX_ITER", "label": "XY 相位校准最大测量次数", "type": "integer", "default": 10, "group": "advanced", "unit": ""},
  {"name": "XY_PHASE_CAL_MIN_R_V", "label": "XY 校相最低 R", "type": "number", "default": 1e-12, "group": "advanced", "unit": "V"},
  {"name": "XY_PHASE_CAL_MIN_R_RATIO", "label": "XY 校相最低 R 比例", "type": "number", "default": 0.1, "group": "advanced", "unit": ""},
  {"name": "XY_CTRL_K_HZ_PER_V", "label": "正弦控制标定斜率 K", "type": "number", "default": 14270.0, "group": "advanced", "unit": "Hz/V"},
  {"name": "XY_CTRL_B_HZ", "label": "正弦控制标定截距 B", "type": "number", "default": -100.0, "group": "advanced", "unit": "Hz"},
  {"name": "XY_CALIBRATION_MIN_ENVELOPE_V", "label": "标定有效最小包络", "type": "number", "default": 0.0, "group": "advanced", "unit": "V"},
  {"name": "XY_CALIBRATION_MAX_ENVELOPE_V", "label": "标定有效最大包络", "type": "number", "default": 10.0, "group": "advanced", "unit": "V"},
  {"name": "XY_TRIGGER_FREQ", "label": "Time_sequence_2 触发频率", "type": "number", "default": 100.0, "group": "advanced", "unit": "Hz"},
  {"name": "XY_TRIGGER_AMPLITUDE", "label": "Time_sequence_2 触发幅度", "type": "number", "default": 5.0, "group": "advanced", "unit": "Vpp"},
  {"name": "XY_TRIGGER_OFFSET", "label": "Time_sequence_2 触发偏置", "type": "number", "default": 2.5, "group": "advanced", "unit": "V"},
  {"name": "XY_TRIGGER_DUTY", "label": "Time_sequence_2 触发占空比", "type": "number", "default": 50.0, "group": "advanced", "unit": "%"},
  {"name": "XY_TRIGGER_PHASE", "label": "Time_sequence_2 触发相位", "type": "number", "default": 0.0, "group": "advanced", "unit": "deg"},
  {"name": "HF2_DEMOD_IDX", "label": "锁相放大器 Demod 索引", "type": "integer", "default": 0, "group": "advanced", "unit": ""},
  {"name": "HF2_OSC_FREQ", "label": "锁相放大器 振荡器频率", "type": "number", "default": 90000.0, "group": "advanced", "unit": "Hz"},
  {"name": "HF2_SIGNAL_RANGE", "label": "锁相放大器 输入量程", "type": "number", "default": 2.0, "group": "advanced", "unit": "V"},
  {"name": "HF2_DEMOD_ORDER", "label": "锁相放大器 Demod 阶数", "type": "integer", "default": 8, "group": "advanced", "unit": ""},
  {"name": "HF2_DEMOD_TC", "label": "锁相放大器 校相时间常数", "type": "number", "default": 0.000692, "group": "advanced", "unit": "s"},
  {"name": "HF2_DEMOD_RATE", "label": "锁相放大器 Demod 采样率", "type": "number", "default": 100000.0, "group": "advanced", "unit": "Sa/s"},
  {"name": "HF2_DAQ_DURATION", "label": "每点采集时长", "type": "number", "default": 1.0, "group": "basic", "unit": "s"},
  {"name": "HF2_DAQ_TC", "label": "DAQ 时间常数", "type": "number", "default": 7.85e-07, "group": "advanced", "unit": "s"},
  {"name": "HF2_DAQ_RATE", "label": "DAQ 采样率", "type": "number", "default": 450000.0, "group": "advanced", "unit": "Sa/s"},
  {"name": "ANALYSIS_BIN_WIDTH_HZ", "label": "频率格间距", "type": "number", "default": 50.0, "group": "basic", "unit": "Hz"},
  {"name": "ANALYSIS_FREQUENCY_MIN_HZ", "label": "分析频带下限", "type": "number", "default": 300.0, "group": "advanced", "unit": "Hz"},
  {"name": "ANALYSIS_FREQUENCY_MAX_HZ", "label": "分析频带上限", "type": "number", "default": 50000.0, "group": "advanced", "unit": "Hz"},
  {"name": "ANALYSIS_FIT_HALF_WIDTH_HZ", "label": "峰定位半窗口", "type": "number", "default": 3000.0, "group": "advanced", "unit": "Hz"},
  {"name": "FIXED_PARAMS.Pump_laser_power", "label": "Pump 光功率", "type": "number", "default": 0.1, "group": "basic", "unit": "V"},
  {"name": "FIXED_PARAMS.Probe_laser_power", "label": "Probe 光功率", "type": "number", "default": 0.2, "group": "basic", "unit": "V"},
  {"name": "FIXED_PARAMS.temperature", "label": "气室温度", "type": "number", "default": 100.0, "group": "basic", "unit": "°C"},
  {"name": "FIXED_PARAMS.Temp_Switch", "label": "温控开关电平", "type": "number", "default": 5.0, "group": "advanced", "unit": "V"},
  {"name": "FIXED_PARAMS.main_magnetic_field", "label": "主磁场电流", "type": "number", "default": 9.28, "group": "basic", "unit": "mA"},
  {"name": "NOISE_BAND_STARTS_HZ", "label": "设计噪声谱段起点", "type": "array", "default": [105000.0], "group": "basic", "unit": "Hz"},
  {"name": "NOISE_BAND_STOPS_HZ", "label": "设计噪声谱段终点", "type": "array", "default": [115000.0], "group": "basic", "unit": "Hz"},
  {"name": "NOISE_BAND_PSDS_V2_PER_HZ", "label": "设计噪声谱密度", "type": "array", "default": [1e-08], "group": "basic", "unit": "V²/Hz"},
  {"name": "NOISE_REPEAT_FREQ_HZ", "label": "噪声波形重复频率", "type": "number", "default": 20.0, "group": "basic", "unit": "Hz"},
  {"name": "NOISE_AMPLITUDE_VPP", "label": "AM 噪声输出幅度", "type": "number", "default": 0.08472675329211143, "group": "basic", "unit": "Vpp"},
  {"name": "NOISE_SEED", "label": "固定随机种子", "type": "integer", "default": 20260922, "group": "advanced", "unit": ""},
  {"name": "PROBE_AOM_CARRIER_FREQ_HZ", "label": "Probe AOM 载波频率", "type": "number", "default": 100000000.0, "group": "basic", "unit": "Hz"},
  {"name": "PROBE_AOM_CARRIER_VPP", "label": "Probe AOM 载波幅度", "type": "number", "default": 0.05, "group": "basic", "unit": "Vpp"},
  {"name": "PROBE_AOM_AM_DEPTH_PERCENT", "label": "外部 AM 深度", "type": "number", "default": 100.0, "group": "basic", "unit": "%"},
  {"name": "INJECT_CONTROLLED_NOISE", "label": "注入 Z 已知可控噪声", "type": "boolean", "default": true, "group": "basic", "unit": ""},
  {"name": "Z_NOISE_BAND_STARTS_HZ", "label": "Z 噪声谱段起点", "type": "array", "default": [10000.0], "group": "basic", "unit": "Hz"},
  {"name": "Z_NOISE_BAND_STOPS_HZ", "label": "Z 噪声谱段终点", "type": "array", "default": [30000.0], "group": "basic", "unit": "Hz"},
  {"name": "Z_NOISE_BAND_PSDS_V2_PER_HZ", "label": "Z 噪声设计谱密度", "type": "array", "default": [1e-11], "group": "basic", "unit": "V²/Hz"},
  {"name": "Z_NOISE_REPEAT_FREQ_HZ", "label": "Z 噪声重复频率", "type": "number", "default": 10.0, "group": "basic", "unit": "Hz"},
  {"name": "Z_NOISE_AMPLITUDE_VPP", "label": "Z 噪声输出幅度", "type": "number", "default": 0.003403262733487621, "group": "basic", "unit": "Vpp"},
  {"name": "Z_NOISE_SEED", "label": "Z 噪声随机种子", "type": "integer", "default": 20260922, "group": "advanced", "unit": ""},
];
let summary: any;
let activeSession: any;
let extraRuns: any[];
let defaults: Record<string, any>;
let parameterLayout: { basic: string[]; advanced: string[] };
let analysisOptions: Record<string, Record<string, number>>;
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
  parameterLayout = { basic: fields.filter(field => field.group === "basic").map(field => field.name),
    advanced: fields.filter(field => field.group === "advanced").map(field => field.name) };
  analysisOptions = {};
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
    if (url === `${BASE}/schema`) return { schema_version: 3, fields: fields.map(field => ({ ...field, default: defaults[field.name] })), parameter_layout_saved: true, parameter_layout: parameterLayout };
    if (url === "/api/jobs") return [];
    if (url === `${BASE}/workflow`) return flow;
    if (url === `${BASE}/sessions/active`) return activeSession;
    if (url === `${BASE}/sessions/probe-session`) return activeSession;
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
      const steps = { ...activeSession.steps, ...Object.fromEntries(body.step_ids.map((id: string) => [id, { status: "completed", error: "" }])) };
      if (body.step_ids.includes("A15")) steps.A13 = { status: "stale", error: "解调器已恢复；再次扫描需要 DAQ 准备" };
      return activeSession = { ...activeSession, revision: activeSession.revision + 1,
        steps };
    }
    if (url === `${BASE}/sessions/probe-session/finish`) return activeSession = { ...activeSession, status: "closed", revision: activeSession.revision + 1 };
    if (url.endsWith("/analysis") && options?.method === "POST") return { id: "analysis", status: "running", events: [] };
    if (url.endsWith("/analysis")) return { run_id: decodeURIComponent(url.split("/").at(-2)!), revision: 2,
      options: analysisOptions[decodeURIComponent(url.split("/").at(-2)!)]
        ?? { bin_width_hz: 50, frequency_min_hz: 50, frequency_max_hz: 1000, fit_half_width_hz: 400 },
      steps: Object.fromEntries(flow.analysis.map(stage => [stage.id, { status: "completed", error: "" }])),
      summary: { status: "completed" }, fingerprint: {} };
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary, ...extraRuns], total: 1 + extraRuns.length };
    if (url.endsWith("/summary")) return [summary, ...extraRuns].find(run => run.run_id === decodeURIComponent(url.split("/").at(-2)!));
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
  defaults.INJECT_CONTROLLED_NOISE = false;
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

it("从真实路由完整运行使用节点草稿，切换 Tab 后保留参数", async () => {
  renderPage();
  const scan = await openParameters("A14");
  fireEvent.change(scan.getByLabelText("运行标签"), { target: { value: "probe-positive" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect((await openParameters("A14")).getByLabelText("运行标签")).toHaveValue("probe-positive");
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(true));
  const parameters = (url: string) => JSON.parse(String(mockApi.mock.calls.find(([path, options]) => path === url && options?.method === "POST")![1]!.body)).parameters;
  expect(parameters(`${BASE}/preflight`).RUN_TAG).toBe("probe-positive");
  expect(parameters(`${BASE}/runs`).RUN_TAG).toBe("probe-positive");
  expect(await screen.findByRole("button", { name: "完成测试任务" })).toBeVisible();
});

it("成功执行所选节点、应用草稿后显式重跑，刷新恢复会话并结束", async () => {
  const mounted = renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "结束会话" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(screen.getByLabelText("A14 AM 噪声 ON/OFF 配对扫描").closest("li")).toHaveClass("completed"));
  const noise = await openParameters("A12");
  fireEvent.change(noise.getByLabelText("AM 噪声输出幅度 (Vpp)"), { target: { value: "0.5" } });
  expect(screen.getByRole("button", { name: "运行所选 / 重跑" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(screen.getByLabelText("A14 AM 噪声 ON/OFF 配对扫描").closest("li")).toHaveClass("stale"));
  expect(activeSession.parameters.NOISE_AMPLITUDE_VPP).toBe(0.5);
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(screen.getByLabelText("A14 AM 噪声 ON/OFF 配对扫描").closest("li")).toHaveClass("completed"));
  expect(mockApi.mock.calls.filter(([url]) => url === `${BASE}/sessions/probe-session/steps`)).toHaveLength(2);
  mounted.unmount();
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "结束会话" })).toBeEnabled());
  expect((await openParameters("A12")).getByLabelText("AM 噪声输出幅度 (Vpp)")).toHaveValue(0.5);
  expect(screen.getByLabelText("A14 AM 噪声 ON/OFF 配对扫描").closest("li")).toHaveClass("completed");
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  expect(await screen.findByText(/已结束会话的执行记录/)).toBeVisible();
});

it("预检失败不启动采集", async () => {
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

it("合并多个参数的重跑预览，还原草稿清除预览，应用失败保留草稿及执行记录", async () => {
  activeSession = makeSession();
  activeSession.steps = Object.fromEntries(flow.acquisition.map(stage => [stage.id,
    { status: stage.id === "A16" ? "pending" : stage.id === "A13" ? "stale" : "completed", error: "" }]));
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "结束会话" })).toBeEnabled());
  const noise = await openParameters("A12");
  const carrier = await openParameters("A08");
  const originalAmplitude = defaults.NOISE_AMPLITUDE_VPP;
  const affected = ["A08", "A09", "A10", "A11", "A12", "A13", "A14", "A15"];
  const expected = ["A08", "A09", "A10", "A11", "A12", "A14", "A15"];
  const previewIds = () => [...document.querySelectorAll(".xy-flow-node.draft-affected")]
    .map(node => node.querySelector("strong")!.textContent!.split(" ")[0]);
  const change = (am: string, rf: string) => {
    fireEvent.change(noise.getByLabelText("AM 噪声输出幅度 (Vpp)"), { target: { value: am } });
    fireEvent.change(carrier.getByLabelText("Probe AOM 载波幅度 (Vpp)"), { target: { value: rf } });
  };
  change("0.5", "0.04");
  expect(previewIds()).toEqual(expected);
  expect(screen.getByLabelText("A13 DAQ 准备").closest("li")).toHaveClass("stale");
  for (const id of expected) expect(screen.getByLabelText(`${id} ${flow.acquisition.find(stage => stage.id === id)!.title}`).closest("li")).toHaveClass("completed");
  expect(mockApi.mock.calls.some(([url]) => url.endsWith("/parameters"))).toBe(false);
  change(String(originalAmplitude), "0.05");
  expect(previewIds()).toEqual([]);
  expect(screen.queryByText(/参数草稿尚未应用，请先应用/)).not.toBeInTheDocument();
  change("0.5", "0.04");
  const normal = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url.endsWith("/parameters")) throw new Error("参数预检拒绝：测试故障");
    return normal(url, options);
  });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  expect(await screen.findByText("Error: 参数预检拒绝：测试故障")).toBeVisible();
  expect(noise.getByLabelText("AM 噪声输出幅度 (Vpp)")).toHaveValue(0.5);
  expect(carrier.getByLabelText("Probe AOM 载波幅度 (Vpp)")).toHaveValue(0.04);
  expect(previewIds()).toEqual(expected);
  expect(activeSession.parameters.NOISE_AMPLITUDE_VPP).toBe(originalAmplitude);
  expect(activeSession.steps.A14.status).toBe("completed");
  expect(mockApi.mock.calls.some(([url]) => url.endsWith("/steps"))).toBe(false);
  mockApi.mockImplementation(normal);
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(screen.getByLabelText("A14 AM 噪声 ON/OFF 配对扫描").closest("li")).toHaveClass("stale"));
  expect(Object.keys(activeSession.steps).filter(id => activeSession.steps[id].status === "stale")).toEqual(affected);
  expect(previewIds()).toEqual([]);
  expect(mockApi.mock.calls.some(([url]) => url.endsWith("/steps"))).toBe(false);
});

it("共享恢复等待跨真实节点同步，移动分类不改值，保存后回读恢复", async () => {
  const mounted = renderPage();
  const phase = await openParameters("A09");
  const scan = await openParameters("A14");
  for (const node of [phase, scan]) {
    const advanced = node.getByText(/^高级参数/, { selector: "summary" });
    expect(advanced.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(advanced);
  }
  expect(within(phase.getByLabelText("恢复温控等待时间 (s)").closest(".zaw-parameter-item") as HTMLElement)
    .getByText(/共用步骤：.*A14/)).toBeVisible();
  fireEvent.change(phase.getByLabelText("恢复温控等待时间 (s)"), { target: { value: "3.5" } });
  expect(scan.getByLabelText("恢复温控等待时间 (s)")).toHaveValue(3.5);
  const xy = await openParameters("A11");
  expect(xy.getByLabelText("恢复温控等待时间 (s)")).toHaveValue(3.5);
  fireEvent.click(phase.getByRole("button", { name: "恢复温控等待时间 移至基础参数" }));
  for (const node of [phase, scan, xy]) {
    expect(node.getByLabelText("恢复温控等待时间 (s)")).toHaveValue(3.5);
    expect(node.getByLabelText("恢复温控等待时间 (s)").closest(".zaw-parameter-advanced")).toBeNull();
    expect(node.getByRole("button", { name: "恢复温控等待时间 移至高级参数" })).toBeVisible();
  }
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  expect(await screen.findByText("参数与布局已保存")).toBeVisible();
  expect(defaults.TEMP_SWITCH_ON_SETTLE_S).toBe(3.5);
  expect(parameterLayout.basic.filter(name => name === "TEMP_SWITCH_ON_SETTLE_S")).toHaveLength(1);
  expect(parameterLayout.advanced).not.toContain("TEMP_SWITCH_ON_SETTLE_S");
  mounted.unmount();
  renderPage();
  for (const id of ["A09", "A11", "A14"]) {
    const node = await openParameters(id);
    expect(node.getByLabelText("恢复温控等待时间 (s)")).toHaveValue(3.5);
    expect(node.getByRole("button", { name: "恢复温控等待时间 移至高级参数" })).toBeVisible();
  }
});

it("下一次与两个历史目录的分析草稿隔离，切换 Tab 保留，保存默认值只保存下一次", async () => {
  const first = summary.run_id;
  const second = "0924_1300_probe_am";
  extraRuns = [{ ...summary, run_id: second, parameters: { ...summary.parameters, RUN_TAG: "second" } }];
  analysisOptions[first] = { bin_width_hz: 75, frequency_min_hz: 75, frequency_max_hz: 900, fit_half_width_hz: 300 };
  analysisOptions[second] = { bin_width_hz: 100, frequency_min_hz: 100, frequency_max_hz: 800, fit_half_width_hz: 200 };
  renderPage();
  const analysis = await openParameters("B02");
  const daq = await openParameters("A13");
  fireEvent.change(analysis.getByLabelText("频率格间距 (Hz)"), { target: { value: "60" } });
  expect(daq.getByLabelText("频率格间距 (Hz)")).toHaveValue(60);
  const choose = async (run: string, value: number) => {
    fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: run } });
    const node = await openParameters("B02");
    await waitFor(() => expect(node.getByLabelText("频率格间距 (Hz)")).toHaveValue(value));
    return node;
  };
  const a = await choose(first, 75);
  fireEvent.change(a.getByLabelText("频率格间距 (Hz)"), { target: { value: "80" } });
  expect(daq.getByLabelText("频率格间距 (Hz)")).toHaveValue(60);
  const b = await choose(second, 100);
  fireEvent.change(b.getByLabelText("频率格间距 (Hz)"), { target: { value: "110" } });
  await choose("", 60);
  await choose(second, 110);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect((await openParameters("B02")).getByLabelText("频率格间距 (Hz)")).toHaveValue(110);
  const revisited = await choose(first, 80);
  fireEvent.click(revisited.getByRole("button", { name: "频率格间距 移至高级参数" }));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  expect(await screen.findByText("参数与布局已保存")).toBeVisible();
  const body = JSON.parse(String(mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)![1]!.body));
  expect(body.parameters.ANALYSIS_BIN_WIDTH_HZ).toBe(60);
  expect(body.parameters.ANALYSIS_FREQUENCY_MAX_HZ).toBe(fields.find(field => field.name === "ANALYSIS_FREQUENCY_MAX_HZ")!.default);
  expect(body.parameter_layout.advanced).toContain("ANALYSIS_BIN_WIDTH_HZ");
  expect(defaults.ANALYSIS_BIN_WIDTH_HZ).toBe(60);
  await choose(second, 110);
  const next = await choose("", 60);
  expect(next.getByLabelText("频率格间距 (Hz)").closest(".zaw-parameter-advanced")).not.toBeNull();
  expect(mockApi.mock.calls.some(([url, options]) => url.endsWith("/analysis") && options?.method === "POST")).toBe(false);
});

it("当前全部 62 个可见参数在真实节点内可编辑，多个节点可同时展开", async () => {
  renderPage();
  expect(fields).toHaveLength(62);
  for (const stage of [...flow.acquisition, ...flow.analysis].filter(stage => stage.display_parameters.length)) {
    const node = await openParameters(stage.id);
    const advanced = node.queryByText(/^高级参数/, { selector: "summary" });
    if (advanced) fireEvent.click(advanced);
  }
  for (const field of fields) {
    const label = field.unit ? `${field.label} (${field.unit})` : field.label;
    const inputs = screen.getAllByLabelText(label);
    expect(inputs.some(input => input.closest(".xy-node-parameters"))).toBe(true);
    expect(inputs.some(input => !input.hasAttribute("disabled"))).toBe(true);
  }
  expect(screen.getByLabelText("A09 Demod0 配置与校相 参数").closest("details")).toHaveAttribute("open");
  expect(screen.getByLabelText("A14 AM 噪声 ON/OFF 配对扫描 参数").closest("details")).toHaveAttribute("open");
}, 15000);

it("校相质量失败显示原因并拒绝依赖步骤，旧历史填回缺省关闭 Z 注入", async () => {
  activeSession = makeSession();
  activeSession.steps.A11 = { status: "quality_failed", error: "XY 校相未收敛，禁止扫描；可以调整参数后重试" };
  summary.parameters = { RUN_TAG: "old-probe-history", NOISE_AMPLITUDE_VPP: .2 };
  renderPage();
  expect(await screen.findByText("XY 校相未收敛，禁止扫描；可以调整参数后重试")).toBeVisible();
  fireEvent.click(screen.getByLabelText(`A12 ${flow.acquisition.find(stage => stage.id === "A12")!.title}`));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  expect(await screen.findByText("Error: 缺少前置：A11")).toBeVisible();
  expect(activeSession.steps.A12.status).toBe("pending");
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await screen.findByText(/已结束会话的执行记录/);
  fireEvent.click(screen.getByRole("button", { name: "完成测试任务" }));
  const noise = (await openParameters("A12")).getByLabelText("注入 Z 已知可控噪声");
  await waitFor(() => expect(noise).toBeEnabled());
  fireEvent.change(noise, { target: { value: "true" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const fill = await screen.findByRole("button", { name: "填回参数" });
  expect(fill).toBeEnabled();
  fireEvent.click(fill);
  expect((await openParameters("A12")).getByLabelText("注入 Z 已知可控噪声")).toHaveValue("false");
  expect((await openParameters("A14")).getByLabelText("运行标签")).toHaveValue("old-probe-history");
  expect(mockApi.mock.calls.some(([url]) => url.endsWith("/defaults") || url === `${BASE}/runs`)).toBe(false);
});
