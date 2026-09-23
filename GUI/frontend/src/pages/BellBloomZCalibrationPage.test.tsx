import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false }) }));
vi.mock("../components/JobView", () => ({ JobView: ({ job, onUpdate }: any) => job ? <button onClick={() => onUpdate({ ...job, status: "completed" })}>完成测试任务</button> : null }));
const mockApi = vi.mocked(api);
const BASE = "/api/experiments/bell-bloom-z-field-calibration";
let summary: any;
const renderPage = () => render(<MemoryRouter initialEntries={["/experiments/bell-bloom-z-field-calibration"]}><Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);

beforeEach(() => {
  summary = { run_id: "0920_latest", timestamp: "2026-09-20T12:00:00+08:00", run_tag: "test", completion_status: "completed", analysis_status: "completed", parameters: { RUN_TAG: "history-tag" }, failure_reason: "", analysis_error: "", safety_shutdown: {},
    artifacts: { "z_frequency_calibration.png": "/plot.png?v=1" }, analysis: { success: true, K_Z_Hz_per_V: -26000, f_0V_Hz: 90000, linear_fit: { uncertainties: [2, 1], r_squared: 0.999 }, rejection_reasons: [] } };
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === BASE) return { title: "Bell Bloom Z 磁场频率标定", description: "Z 标定", wiring_notes: [] };
    if (url === `${BASE}/schema`) return { fields: [{ name: "RUN_TAG", label: "运行标签", type: "string", default: "bb_z_cal", group: "basic" }], parameter_layout: { basic: ["RUN_TAG"], advanced: [] } };
    if (url === "/api/jobs") return [];
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary], total: 1 };
    if (url.endsWith("/summary")) return summary;
    if (url === `${BASE}/preflight`) return { ok: true, errors: [] };
    if (url === `${BASE}/defaults`) return { schema: { fields: [{ name: "RUN_TAG" }], parameter_layout: JSON.parse(String(options?.body)).parameter_layout } };
    if (url === `${BASE}/runs` && options?.method === "POST") return { id: "acquisition", kind: "experiment:bell-bloom-z-field-calibration", status: "running", events: [] };
    if (url.endsWith("/analyze")) return { id: "analysis", status: "running", events: [] };
    throw new Error(`未模拟 ${url}`);
  });
});
afterEach(cleanup);

it("恢复最近结果，只有运行/历史两个 Tab，切换与回填保留参数", async () => {
  renderPage();
  expect(within(screen.getByRole("tablist", { name: "Bell Bloom Z 标定视图" })).getAllByRole("tab")).toHaveLength(2);
  expect(await within(screen.getByRole("tabpanel", { name: "运行" })).findByAltText("Z 电压频率标定曲线")).toHaveAttribute("src", "/plot.png?v=1");
  fireEvent.change(screen.getByLabelText("运行标签"), { target: { value: "edited" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "填回参数" })).toBeEnabled());
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("edited");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("history-tag");
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  expect(await screen.findByText("参数与布局已保存")).toBeVisible();
});

it("最新失败运行不显示更早结果，重新分析完成后刷新图片", async () => {
  summary = { ...summary, completion_status: "failed", analysis_status: "not_started", failure_reason: "频点采集失败", analysis: null, artifacts: {} };
  renderPage();
  expect(await within(screen.getByRole("tabpanel", { name: "运行" })).findByText("频点采集失败")).toBeVisible();
  expect(screen.queryByAltText("Z 电压频率标定曲线")).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "重新分析" }));
  await screen.findByRole("button", { name: "完成测试任务" });
  summary = { ...summary, analysis_status: "completed", analysis: { success: false, K_Z_Hz_per_V: null, f_0V_Hz: null, linear_fit: { uncertainties: [null, null], r_squared: null }, rejection_reasons: ["有效中心不足 2 个"] }, artifacts: { "z_frequency_calibration.png": "/plot.png?v=2" } };
  fireEvent.click(screen.getByRole("button", { name: "完成测试任务" }));
  const history = screen.getByRole("tabpanel", { name: "历史" });
  await waitFor(() => expect(within(history).getByAltText("Z 电压频率标定曲线")).toHaveAttribute("src", "/plot.png?v=2"));
  expect(within(history).getByText("有效中心不足 2 个")).toBeVisible();
});

it("活动任务在切换 Tab 后仍可完成并刷新最近结果", async () => {
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "预检并运行实验" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByRole("button", { name: "完成测试任务" });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  fireEvent.click(screen.getByRole("button", { name: "完成测试任务" }));
  await waitFor(() => expect(mockApi.mock.calls.filter(([url]) => url === `${BASE}/runs?limit=1&offset=0`).length).toBeGreaterThan(2));
});

it("预检失败不会启动采集，空历史可浏览", async () => {
  const previous = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/preflight`) return { ok: false, errors: ["扫频下限必须为正"] };
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [], total: 0 };
    return previous(url, options);
  });
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "预检并运行实验" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("扫频下限必须为正")).toBeVisible();
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByText("暂无运行记录。")).toBeVisible();
});
