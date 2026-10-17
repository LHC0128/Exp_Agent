import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { MxYRFFrequencyResponsePage } from "./MxYRFFrequencyResponsePage";
import type { WorkflowRunSummary } from "./WorkflowExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
let summary: WorkflowRunSummary;
vi.mock("./WorkflowExperimentPage", () => ({
  WorkflowExperimentPage: ({ renderResult }: { renderResult: (value: WorkflowRunSummary) => unknown }) => renderResult(summary),
}));
const mockApi = vi.mocked(api);
const source = "/api/experiments/mx-y-rf-frequency-response/runs/0817_105712_mx_y_rf_freq_resp/analysis";
const diagnostics = (gamma: number, count = 1) => ({ summary: { per_amplitude: Array.from({ length: count }, (_, index) => ({
  amplitude_vpp: (index + 1) / 100, success: true, split: index >= 2, r_squared: 0.99,
  parameters: { gamma_hz: gamma, center_hz: 10000, saturation: index >= 2 ? 2 : 0 },
})), saturation_scaling: { success: false } } });

beforeEach(() => {
  summary = { run_id: "0817_105712_mx_y_rf_freq_resp", timestamp: "0817_105712", run_tag: "legacy",
    completion_status: "completed", analysis_status: "completed", analysis_error: "", parameters: {},
    artifacts: { "results/analysis.json": "/original-analysis.json?v=1", "results/fit_diagnostics.yaml": "/diagnostics.yaml?v=1" } };
  mockApi.mockReset();
});
afterEach(cleanup);

it.each(["results/analysis.json", "results/fit_diagnostics.yaml"])("仅 %s 版本变化也重新请求正规诊断", async (artifact) => {
  mockApi.mockResolvedValueOnce(diagnostics(300) as never).mockResolvedValueOnce(diagnostics(321) as never);
  const view = render(<MxYRFFrequencyResponsePage />);
  expect(await screen.findByText("300.00")).toBeVisible();
  summary = { ...summary, artifacts: { ...summary.artifacts, [artifact]: "/changed?v=2" } };
  view.rerender(<MxYRFFrequencyResponsePage />);
  expect(await screen.findByText("321.00")).toBeVisible();
  await waitFor(() => expect(mockApi).toHaveBeenCalledTimes(2));
  expect(mockApi.mock.calls.map(([url]) => url)).toEqual([source, source]);
});

it("已有五条旧诊断时区分尚未开始分步分析的文案", async () => {
  summary.analysis_status = "not_started";
  mockApi.mockResolvedValue(diagnostics(300, 5) as never);
  render(<MxYRFFrequencyResponsePage />);
  const table = await screen.findByRole("table", { name: "布洛赫拟合诊断" });
  expect(within(table).getAllByRole("row")).toHaveLength(6);
  expect(screen.getByText(/分析：未开始分步分析/)).toBeVisible();
});
