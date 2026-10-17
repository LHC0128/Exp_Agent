import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "../api";
import type { SchemaField } from "../types/api";
import { WorkflowExperimentPage } from "./WorkflowExperimentPage";
import { WorkflowPanel } from "../components/NoiseSpectrumXY/WorkflowPanel";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));

const ID = "shared-workflow-test";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const fields: SchemaField[] = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "next", group: "basic", unit: "" },
  { name: "GAIN", label: "采集增益", type: "number", default: 2, group: "basic", unit: "" },
];
const layout = { basic: fields.map(field => field.name), advanced: [] };
const stage = (id: string, parameters: string[] = []) => ({
  id, title: id, group: "真实步骤", dependencies: [], parameters, display_parameters: parameters, artifacts: [],
});
const old = { run_id: "1014_old", timestamp: "1014", run_tag: "old", completion_status: "completed" };
let preflightOk: boolean;

beforeEach(() => {
  preflightOk = true;
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    if (url === BASE) return { id: ID, title: "公共流程测试", description: "说明", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: layout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === `${BASE}/sessions/active`) return null as never;
    if (url === `${BASE}/workflow`) return {
      acquisition: [stage("A01", fields.map(field => field.name)), stage("A02")], analysis: [],
      parameter_impacts: { acquisition: { RUN_TAG: ["A01"], GAIN: ["A01"] }, analysis: {} }, diagram_url: "/diagram",
    } as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old], total: 1 } as never;
    if (url === `${BASE}/runs/${old.run_id}/summary`) return {
      ...old, analysis_status: "not_started", analysis_error: "", parameters: { RUN_TAG: "historical", GAIN: 9 }, artifacts: {},
    } as never;
    if (url === `${BASE}/derive`) return { values: {} } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["参数超限"] } as never;
    if (url === `${BASE}/defaults`) return {
      message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: JSON.parse(String(options?.body)).parameter_layout },
    } as never;
    throw new Error(`未模拟接口 ${url}`);
  });
});
afterEach(cleanup);

function page() {
  return render(<WorkflowExperimentPage experimentId={ID} finishStepId="A02" runStepId="A01" hasAnalysis={false} />);
}

it("无分析器只显示真实采集节点，参数切换Tab后保留且历史只读取快照", async () => {
  page();
  await screen.findByRole("checkbox", { name: /A01/ });
  expect(screen.queryByRole("heading", { name: "分析流程" })).not.toBeInTheDocument();
  expect(screen.getByText("本实验没有独立离线分析器，运行结果通过历史记录查看。")).toBeInTheDocument();
  fireEvent.click(screen.getByText(/参数：运行标签、采集增益/));
  const input = screen.getByLabelText("运行标签");
  fireEvent.change(input, { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "填回参数" });
  const history = screen.getByRole("tabpanel", { name: "历史" });
  expect(within(history).getByText("historical")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "重新分析" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("draft");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
});

it("预检失败不启动，历史填回不保存或启动，保存使用下一次实验值", async () => {
  page();
  await screen.findByRole("checkbox", { name: /A01/ });
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("参数超限");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const request = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`);
  expect(JSON.parse(String(request?.[1]?.body))).toEqual({ parameters: { RUN_TAG: "historical", GAIN: 9 }, parameter_layout: layout });
});

it("两个主Tab支持键盘切换", async () => {
  page();
  await screen.findByRole("checkbox", { name: /A01/ });
  const tab = screen.getByRole("tab", { name: "运行" });
  tab.focus();
  fireEvent.keyDown(tab, { key: "ArrowRight" });
  await waitFor(() => expect(screen.getByRole("tab", { name: "历史" })).toHaveFocus());
});

it.each([
  { status: "quality_failed", artifacts: ["diagnostic.yaml"], expected: "diagnostic.yaml" },
  { status: "quality_failed", artifacts: [], expected: null },
  { status: "stale", artifacts: ["diagnostic.yaml"], expected: null },
  { status: "pending", artifacts: ["diagnostic.yaml"], expected: null },
  { status: "completed", artifacts: [], expected: "expected.npz" },
])("$status 节点只显示可复用状态中有依据的产物", async ({ status, artifacts, expected }) => {
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/workflow`) return {
      acquisition: [{ ...stage("A01"), artifacts: ["expected.npz"] }], analysis: [],
      parameter_impacts: { acquisition: {}, analysis: {} }, diagram_url: "/diagram",
    } as never;
    return original(url, options);
  });
  render(<WorkflowPanel experimentId={ID} enabled fields={fields} layout={layout} values={{}}
    setLayout={vi.fn()} setNotice={vi.fn()} parametersDisabled={false} setValues={vi.fn()}
    session={{ session_id: "closed", job_id: "", revision: 1, status: "closed", run_id: "saved",
      parameters: {}, output_source: "none", demod_mode: "none",
      steps: { A01: { status, artifacts, error: "" } } }}
    setSession={vi.fn()} setJob={vi.fn()} setAnalysisJob={vi.fn()} hardwareBusy={false} runs={[]}
    setError={vi.fn()} onRefresh={vi.fn()}><div /></WorkflowPanel>);
  await screen.findByRole("checkbox", { name: "A01 A01" });
  if (expected) {
    expect(screen.getByRole("link", { name: expected, hidden: true })).toHaveAttribute(
      "href", `${BASE}/runs/saved/files/${expected}`);
  } else {
    expect(screen.queryByText("产物")).toBeNull();
  }
});
