import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { StaticSensitivityPage } from "./StaticSensitivityPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));
const mockApi = vi.mocked(api);
const BASE = "/api/experiments/static-sensitivity";
const fields = [
  { name: "run_tag", label: "运行标签", type: "string", default: "sens", group: "basic" },
  { name: "dispersion_steps", label: "色散扫描点数", type: "integer", default: 41, group: "basic" },
  { name: "dispersion_step_settle_s", label: "色散每步稳定等待", type: "number", default: 0.1, unit: "s", group: "advanced" },
  { name: "hf2_demod_rate", label: "色散采样率", type: "number", default: 1000, unit: "Sa/s", group: "advanced", description: "用于 HF2 校相与色散连续采集" },
  { name: "dispersion_duration_s", label: "色散每点采集时间", type: "number", default: 0.1, unit: "s", group: "advanced" },
];
let summary: any;
const tab = (name: string) => within(screen.getByRole("tablist", { name: "静磁场灵敏度实验视图" })).getByRole("tab", { name });

beforeEach(() => {
  mockApi.mockReset();
  summary = { run_id: "test_dc", timestamp: "test", run_tag: "sens", completion_status: "completed",
    parameters: { run_tag: "history", dispersion_steps: 41, dispersion_step_settle_s: 0.1, hf2_demod_rate: 1000, dispersion_duration_s: 0.1 },
    analysis: { success: true, fit_valid: true, best_range_label: "10mA", sensitivity_flat_fT_per_sqrt_Hz: 100 },
    artifacts: { "full_analysiswithoutpump.png": "/analysis.png" } };
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === BASE) return { title: "静磁场灵敏度", description: "直流逐点色散", execution_mode: "typed_workflow", required_devices: ["HF2"], acquisition_program: "experiments/Static_Magnetic_Field_Sensitivity.py" };
    if (url === `${BASE}/schema`) return { schema_version: 3, fields };
    if (url === "/api/jobs") return [];
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary], total: 1 };
    if (url.endsWith("/summary")) return summary;
    if (url === `${BASE}/defaults`) return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: JSON.parse(String(options?.body)).parameter_layout } };
    if (url === `${BASE}/preflight`) return { ok: true, errors: [] };
    throw new Error(`未模拟 ${url}`);
  });
});
afterEach(cleanup);

it("新参数可以保存，切换视图不丢失，直流历史可以填回", async () => {
  render(<StaticSensitivityPage />);
  const points = await screen.findByLabelText("色散扫描点数");
  fireEvent.change(points, { target: { value: "61" } });
  fireEvent.click(screen.getByRole("tab", { name: /高级参数/ }));
  fireEvent.change(screen.getByLabelText("色散每点采集时间 (s)"), { target: { value: "0.2" } });
  expect(screen.getByLabelText("色散采样率 (Sa/s)")).toHaveValue(1000);
  expect(screen.getByText("用于 HF2 校相与色散连续采集")).toBeVisible();
  expect(screen.queryByLabelText("色散拟合起始剔除点数")).toBeNull();
  expect(screen.queryByLabelText("色散拟合终止剔除点数")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  expect(await screen.findByText("已保存")).toBeVisible();
  const request = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  const body = JSON.parse(String(request[1]?.body));
  expect(body.parameters.dispersion_steps).toBe(61);
  expect(body.parameters.dispersion_duration_s).toBe(0.2);
  expect(body.parameters.hf2_demod_rate).toBe(1000);
  expect(body.parameters).not.toHaveProperty("dispersion_samples_per_step");
  expect(body.parameters).not.toHaveProperty("dispersion_sample_interval_s");
  expect(body.parameters).not.toHaveProperty("dispersion_fit_skip_start");
  expect(body.parameters).not.toHaveProperty("dispersion_fit_skip_end");
  expect(body.parameter_layout.advanced.slice(-2)).toEqual(["hf2_demod_rate", "dispersion_duration_s"]);
  expect(body.parameter_layout.basic).toEqual(["run_tag", "dispersion_steps"]);
  fireEvent.click(tab("历史"));
  fireEvent.click(await screen.findByRole("button", { name: /test_dc/ }));
  expect(await screen.findByRole("button", { name: "重新分析" })).toBeEnabled();
  fireEvent.click(tab("运行"));
  fireEvent.click(screen.getByRole("tab", { name: /高级参数/ }));
  expect(screen.getByLabelText("色散每点采集时间 (s)")).toHaveValue(0.2);
  fireEvent.click(screen.getByRole("tab", { name: /基础参数/ }));
  expect(screen.getByLabelText("色散扫描点数")).toHaveValue(61);
  fireEvent.click(tab("历史"));
  fireEvent.click(await screen.findByRole("button", { name: /test_dc/ }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("history");
});

it("旧格式历史仅查看已有结果，不能回填或重分析", async () => {
  summary.parameters = { ramp_low: -0.1, ramp_high: 0.1, ramp_freq: 1 };
  render(<StaticSensitivityPage />);
  await screen.findByLabelText("运行标签");
  fireEvent.click(tab("历史"));
  fireEvent.click(await screen.findByRole("button", { name: /test_dc/ }));
  expect(await screen.findByText(/旧格式运行仅可查看/)).toBeVisible();
  expect(screen.getByRole("button", { name: "填回参数" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "重新分析" })).toBeDisabled();
  expect(screen.getByAltText("full_analysiswithoutpump.png")).toHaveAttribute("src", "/analysis.png");
});

it("拟合失败显示原因而不冒用旧图，架构链接保持且标注工作区版本", async () => {
  summary.analysis = { success: false, fit_valid: false, fit_rejection_reasons: ["色散中心斜率不可用"] };
  render(<StaticSensitivityPage />);
  expect(await screen.findByText("色散中心斜率不可用")).toBeVisible();
  expect(screen.queryByAltText("静磁场灵敏度分析结果")).toBeNull();
  fireEvent.click(tab("架构"));
  expect(screen.getByTitle("静磁场灵敏度实验采集流程架构图")).toHaveAttribute("src", "/architecture/static-sensitivity.html");
  await waitFor(() => expect(screen.getByText(/工作区版本/)).toBeVisible());
});

it("区分拟合有效与噪声分析失败，运行和历史都显示错误", async () => {
  summary.analysis = { success: false, fit_valid: true, error: "噪声平坦频段灵敏度不可用" };
  render(<StaticSensitivityPage />);
  expect(await screen.findByText("噪声平坦频段灵敏度不可用")).toBeVisible();
  expect(screen.getByText("有效")).toBeVisible();
  expect(screen.queryByAltText("静磁场灵敏度分析结果")).toBeNull();
  fireEvent.click(tab("历史"));
  fireEvent.click(await screen.findByRole("button", { name: /test_dc/ }));
  expect(await screen.findByText("噪声平坦频段灵敏度不可用")).toBeVisible();
  expect(screen.queryByAltText("full_analysiswithoutpump.png")).toBeNull();
});
