import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

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
];
let summary: any;

const renderPage = () => render(
  <MemoryRouter initialEntries={["/experiments/noise-spectrum-xy-known-noise"]}>
    <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
  </MemoryRouter>,
);

beforeEach(() => {
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
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: { basic: fields.map((field) => field.name), advanced: [] } };
    if (url === "/api/jobs") return [];
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary], total: 1 };
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
    if (url === `${BASE}/defaults`) return { message: "参数与布局已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: JSON.parse(String(options?.body)).parameter_layout } };
    if (url === `${BASE}/runs` && options?.method === "POST") return { id: "acquisition", kind: "experiment:noise-spectrum-xy-known-noise", status: "running", events: [] };
    if (url.endsWith("/analyze")) return { id: "analysis", status: "running", events: [] };
    throw new Error(`未模拟 ${url}`);
  });
});

afterEach(cleanup);

it("显示波形预算预览与真值链 K_Z，谱段数组按逗号分隔编辑", async () => {
  renderPage();
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
  expect(screen.getByLabelText("运行标签")).toHaveValue("known_noise");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("history-tag");
});

it("从真实路由进入双 Tab，预检失败不启动采集", async () => {
  renderPage();
  expect(within(screen.getByRole("tablist", { name: "XY 已知噪声注入实验视图" })).getAllByRole("tab")).toHaveLength(2);
  await waitFor(() => expect(screen.getByRole("button", { name: "预检并运行实验" })).toBeEnabled());
  const normal = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/preflight`) return { ok: false, errors: ["噪声段超出 Nyquist"] };
    return normal(url, options);
  });
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
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
