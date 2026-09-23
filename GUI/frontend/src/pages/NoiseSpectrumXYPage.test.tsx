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
const BASE = "/api/experiments/noise-spectrum-xy";
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "noise", group: "basic", unit: "" },
  { name: "TARGET_NOISE_FREQ_POINTS", label: "目标频率扫描点数", type: "integer", default: 500, group: "basic", unit: "" },
  { name: "HF2_DAQ_DURATION", label: "每点采集时长", type: "number", default: 1, group: "basic", unit: "s" },
  { name: "ANALYSIS_BIN_WIDTH_HZ", label: "频率格间距", type: "number", default: 12, group: "basic", unit: "Hz" },
];
let summary: any;

const renderPage = () => render(
  <MemoryRouter initialEntries={["/experiments/noise-spectrum-xy"]}>
    <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
  </MemoryRouter>,
);

beforeEach(() => {
  summary = {
    run_id: "0716_1132_noise",
    timestamp: "0716_1132",
    run_tag: "history-tag",
    completion_status: "completed",
    analysis_status: "completed",
    analysis_error: "",
    parameters: { RUN_TAG: "history-tag" },
    artifacts: { "noise_spectra_extracted.png": "/noise.png?v=1" },
  };
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === BASE) return { title: "XY 控制测量噪声谱（正弦）", description: "噪声谱", wiring_notes: ["三通触发"] };
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: { basic: fields.map((field) => field.name), advanced: [] } };
    if (url === "/api/jobs") return [];
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary], total: 1 };
    if (url.endsWith("/summary")) return summary;
    if (url === `${BASE}/preflight`) return { ok: true, errors: [] };
    if (url === `${BASE}/derive`) {
      const values = JSON.parse(String(options?.body)).parameters;
      return { values: values.HF2_DAQ_DURATION < .1 ? { error: "每点采集时长不足一个 Welch 分段" } : {
        nperseg: 37500, segments_per_point: values.HF2_DAQ_DURATION === 5 ? 119 : 23,
        bin_width_hz: 12, scan_seconds: 1560,
      } };
    }
    if (url === `${BASE}/defaults`) return { message: "参数与布局已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: JSON.parse(String(options?.body)).parameter_layout } };
    if (url === `${BASE}/runs` && options?.method === "POST") return { id: "acquisition", kind: "experiment:noise-spectrum-xy", status: "running", events: [] };
    if (url.endsWith("/analyze")) return { id: "analysis", status: "running", events: [] };
    throw new Error(`未模拟 ${url}`);
  });
});

afterEach(cleanup);

it("时长变化更新只读分段预览，过短时长明确报错，并显示实际分析段数", async () => {
  summary.analysis_quality = { psd: { actual_rate_sa_s: 460526.3158, nperseg: 38378,
    bin_width_hz: 11.9997, segments_per_point_range: [23, 23] } };
  renderPage();
  await screen.findByLabelText("每点采集时长 (s)");
  expect(screen.queryByLabelText("Welch 每段点数")).not.toBeInTheDocument();
  expect(await screen.findByText(/每点平均段数：23（50%/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("每点采集时长 (s)"), { target: { value: "5" } });
  expect(await screen.findByText(/每点平均段数：119（50%/)).toBeVisible();
  const panel = screen.getByRole("tabpanel", { name: "运行" });
  expect(within(panel).getByText(/实际 Welch 每段点数：38378/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("每点采集时长 (s)"), { target: { value: "0.01" } });
  expect(await screen.findByText("每点采集时长不足一个 Welch 分段")).toBeVisible();
});

it("明确显示质量验收失败与有效点数，不把执行完成视作全频段有效", async () => {
  summary = { ...summary, analysis_status: "quality_failed", analysis_error: "移动峰不足",
    analysis_quality: { fit: { valid_points: 0, total_points: 200, valid_frequency_extent_hz: null } } };
  renderPage();
  const panel = screen.getByRole("tabpanel", { name: "运行" });
  expect(await within(panel).findByText(/质量验收未通过/)).toBeVisible();
  expect(within(panel).getByText(/通过质量验收 0 \/ 200/)).toBeVisible();
  expect(within(panel).getByText("移动峰不足")).toBeVisible();
});

it("从真实路由进入双 Tab，切换保留参数并按当前 schema 填回历史参数", async () => {
  renderPage();
  expect(within(screen.getByRole("tablist", { name: "XY 正弦控制噪声谱实验视图" })).getAllByRole("tab")).toHaveLength(2);
  expect(await within(screen.getByRole("tabpanel", { name: "运行" })).findByAltText("提取的可控与不可控噪声谱")).toHaveAttribute("src", "/noise.png?v=1");
  fireEvent.change(screen.getByLabelText("运行标签"), { target: { value: "edited" } });
  fireEvent.change(screen.getByLabelText("目标频率扫描点数"), { target: { value: "123" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "填回参数" })).toBeEnabled());
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("edited");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("history-tag");
  expect(screen.getByLabelText("目标频率扫描点数")).toHaveValue(123);
});

it("预检失败不启动采集，历史重新分析完成后刷新实际结果图", async () => {
  const normal = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/preflight`) return { ok: false, errors: ["目标频率范围无效"] };
    return normal(url, options);
  });
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "预检并运行实验" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("目标频率范围无效")).toBeVisible();
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);

  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "重新分析" }));
  summary = { ...summary, artifacts: { "noise_spectra_extracted.png": "/noise.png?v=2" } };
  fireEvent.click(await screen.findByRole("button", { name: "完成测试任务" }));
  await waitFor(() => expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByAltText("提取的可控与不可控噪声谱")).toHaveAttribute("src", "/noise.png?v=2"));
});
