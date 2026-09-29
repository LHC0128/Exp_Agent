import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));

const mockApi = vi.mocked(api);
const BASE = "/api/experiments/noise-spectrum-xy-uncontrolled-probe-am";
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "probe_am", group: "basic", unit: "" },
  { name: "NOISE_AMPLITUDE_VPP", label: "AM 噪声输出幅度", type: "number", default: 2, group: "basic", unit: "Vpp" },
  { name: "NOISE_REPEAT_FREQ_HZ", label: "噪声波形重复频率", type: "number", default: 10, group: "basic", unit: "Hz" },
  { name: "INJECT_CONTROLLED_NOISE", label: "注入 Z 已知可控噪声", type: "boolean", default: false, group: "basic", unit: "" },
  { name: "Z_NOISE_AMPLITUDE_VPP", label: "Z 噪声输出幅度", type: "number", default: 0.003, group: "basic", unit: "Vpp" },
  { name: "PROBE_AOM_CARRIER_VPP", label: "Probe AOM 载波幅度", type: "number", default: 0.05, group: "basic", unit: "Vpp" },
];
const history = {
  run_id: "0923_1200_probe_am", timestamp: "0923_1200", run_tag: "history-probe",
  completion_status: "completed", analysis_status: "quality_failed",
  analysis_error: "移动峰自动标定未通过；已按运行参数 K/B 生成暂定控制轴，结果仅供暂时分析",
  parameters: { RUN_TAG: "history-probe", NOISE_AMPLITUDE_VPP: 2 },
  analysis_quality: {
    calibration: { k: 14270, b: -100, support_points: 200, source: "configured_K_B" },
    warnings: ["未检测到足够显著的移动峰；本次按运行参数 K/B 推算峰位，图和拟合仅供暂时分析。"],
    fit_quality: {
      valid_background_bins: 7, valid_controlled_bins: 8, total_bins: 10,
      negative_delta_bins: 3, positive_delta_bins: 4,
      negative_controlled_delta_bins: 2, positive_controlled_delta_bins: 6,
      background_method_on: { off_resonance_plateau: 4 },
      background_method_off: { off_resonance_plateau: 5 },
    },
  },
  artifacts: {
    "noise_spectrum_2d.png": "/average.png",
    "controlled_uncontrolled_comparison.png": "/states.png",
    "lorentzian_line_shapes.png": "/lorentzian.png",
    "signed_spectral_difference.png": "/difference.png",
  },
};

const renderPage = () => render(
  <MemoryRouter initialEntries={["/experiments/noise-spectrum-xy-uncontrolled-probe-am"]}>
    <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
  </MemoryRouter>,
);

beforeEach(() => {
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === BASE) return { title: "XY 控制测量已知不可控 Probe AM 噪声谱", description: "Probe AM", wiring_notes: ["Probe AOM CH1/CH2"] };
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: false };
    if (url === "/api/jobs") return [];
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [history], total: 1 };
    if (url.endsWith("/summary")) return history;
    if (url === `${BASE}/derive`) return { values: { sample_rate_sa_s: 163840, nyquist_hz: 81920, line_spacing_hz: 10, waveform_time_s: [], waveform_voltage_v: [], aligned_amplitude_vpp: 0.25, aligned_amplitude_safe: true, z_aligned_amplitude_vpp: 0.02, z_aligned_amplitude_safe: true } };
    if (url === `${BASE}/preflight`) return { ok: false, errors: ["AM 输入电压超限"] };
    if (url === `${BASE}/defaults`) return { message: "保存完成", schema: { fields, parameter_layout_saved: true, parameter_layout: JSON.parse(String(options?.body)).parameter_layout } };
    throw new Error(`未模拟 ${url}`);
  });
});

afterEach(cleanup);

it("通过真实实验路由显示运行/历史，保留参数并从历史填回", async () => {
  renderPage();
  const tabs = screen.getByRole("tablist", { name: "XY Probe AM 噪声实验视图" });
  expect(within(tabs).getAllByRole("tab")).toHaveLength(3);
  const runTab = screen.getByRole("tabpanel", { name: "运行" });
  const tag = await within(runTab).findByRole("textbox", { name: "运行标签" });
  fireEvent.change(tag, { target: { value: "unsaved-probe" } });
  fireEvent.click(screen.getByRole("button", { name: "Z 可控噪声：不注入" }));
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await screen.findByRole("button", { name: "填回参数" })).toBeEnabled();
  const historyPanel = screen.getByRole("tabpanel", { name: "历史" });
  expect(within(historyPanel).getByAltText("平均 PSD 二维谱")).toHaveAttribute("src", "/average.png");
  expect(within(historyPanel).getByText(/暂定控制轴 K = 14270.0 Hz\/V，B = -100.0 Hz/)).toBeVisible();
  expect(within(historyPanel).getByText(/图和拟合仅供暂时分析/)).toBeVisible();
  expect(within(historyPanel).getByText(/不可控背景独立验收；远端平台补充 ON 4 点、\s*OFF 5 点/)).toBeVisible();
  expect(within(historyPanel).getByAltText("受控响应与不可控背景的注入前后对比")).toHaveAttribute("src", "/states.png");
  expect(within(historyPanel).getByAltText("若干频率列的洛伦兹线形与拟合")).toHaveAttribute("src", "/lorentzian.png");
  expect(within(historyPanel).getByAltText("受控响应与不可控背景的基线和有符号差谱（双 Y 轴）")).toHaveAttribute("src", "/difference.png");
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByRole("textbox", { name: "运行标签" })).toHaveValue("unsaved-probe");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(screen.getByRole("textbox", { name: "运行标签" })).toHaveValue("history-probe");
  expect(screen.getByRole("button", { name: "Z 可控噪声：不注入" })).toHaveAttribute("aria-pressed", "false");
});

it("架构 Tab 加载 Probe AM 实验专属的架构图与子图", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "架构" }));
  const panel = screen.getByRole("tabpanel", { name: "架构" });
  expect(await within(panel).findByTitle("XY 已知不可控 Probe AM 噪声谱实验架构图")).toHaveAttribute(
    "src", "/architecture/noise-spectrum-xy-uncontrolled-probe-am.html");
  expect(within(panel).getByRole("link", { name: "在新页面打开" })).toHaveAttribute(
    "href", "/architecture/noise-spectrum-xy-uncontrolled-probe-am.html");
  fireEvent.click(within(panel).getByRole("tab", { name: "单点时序" }));
  expect(within(panel).getByTitle("XY 已知不可控 Probe AM 噪声谱实验单控制点时序图")).toHaveAttribute(
    "src", "/architecture/noise-spectrum-xy-uncontrolled-probe-am.timing.html");
  fireEvent.click(within(panel).getByRole("tab", { name: "数据分析" }));
  expect(within(panel).getByTitle("XY 已知不可控 Probe AM 噪声谱实验离线分析与差谱图")).toHaveAttribute(
    "src", "/architecture/noise-spectrum-xy-uncontrolled-probe-am.dataflow.html");
});

it("预检失败时不会启动采集", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("AM 输入电压超限")).toBeVisible();
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(`${BASE}/preflight`, expect.any(Object)));
});

it("Z 注入开关与两路幅度对齐分别写入运行参数", async () => {
  renderPage();
  const toggle = await screen.findByRole("button", { name: "Z 可控噪声：不注入" });
  fireEvent.click(toggle);
  expect(screen.getByRole("button", { name: "Z 可控噪声：注入" })).toHaveAttribute("aria-pressed", "true");
  const probeAlign = await screen.findByRole("button", { name: "对齐 Probe AM 幅度" });
  const zAlign = screen.getByRole("button", { name: "对齐 Z 噪声幅度" });
  await waitFor(() => expect(probeAlign).toBeEnabled());
  fireEvent.click(probeAlign);
  await waitFor(() => expect(zAlign).toBeEnabled());
  fireEvent.click(zAlign);
  expect(screen.getByRole("spinbutton", { name: "AM 噪声输出幅度 (Vpp)" })).toHaveValue(0.25);
  expect(screen.getByRole("spinbutton", { name: "Z 噪声输出幅度 (Vpp)" })).toHaveValue(0.02);
  fireEvent.click(screen.getByRole("button", { name: "仅预检" }));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(`${BASE}/preflight`, expect.objectContaining({
    body: expect.stringContaining('"INJECT_CONTROLLED_NOISE":true'),
  })));
});
