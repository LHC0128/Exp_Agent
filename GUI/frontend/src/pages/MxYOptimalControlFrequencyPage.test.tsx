import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./MxYOptimalControlFrequencyPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
const mockApi = vi.mocked(api);
const ID = "mx-y-optimal-control-rf-frequency-response";
const BASE = `/api/experiments/${ID}`;

const fields = fixture.schema.fields;
const acquisition = fixture.flow.acquisition;
const analysisStages = fixture.flow.analysis;
const parameterImpacts = fixture.flow.parameter_impacts;
const flow = fixture.flow;
let activeSession: any;
let summary: any;
let preflightOk: boolean;
let savedLayout: { basic: string[]; advanced: string[] };
let savedValues: Record<string, number | string>;
let rejectApply: boolean;
let fullJob: any;
let otherSummary: any;
class TestEventSource {
  static instances: TestEventSource[] = [];
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(public url: string) { TestEventSource.instances.push(this); }
}
const diagnostics = {};

const makeSession = (through = 0) => ({
  session_id: "freq-session", job_id: "freq-job", revision: 0, status: "waiting",
  parameters: Object.fromEntries(fields.map((field) => [field.name, field.default])),
  steps: Object.fromEntries(acquisition.map((stage, index) => [stage.id, { status: index < through ? "completed" : "pending", error: "" }])),
  outputs: { "Y RF": through >= 4 ? "ON" : through >= 3 ? "OFF" : "未知", "X": through >= 3 ? "OFF" : "未知",
    "Z": through >= 3 ? "OFF" : "未知", "温控": through >= 4 ? "ON" : "未知", "Pump": through >= 3 ? "ON" : "未知" },
  pending_request: null, demod_mode: through >= 3 ? "response" : "unknown",
});

beforeEach(() => {
  activeSession = null;
  preflightOk = true;
  rejectApply = false; fullJob = undefined;
  TestEventSource.instances = [];
  vi.stubGlobal("EventSource", TestEventSource);
  savedLayout = {
    basic: fields.filter((field) => field.group === "basic").map((field) => field.name),
    advanced: fields.filter((field) => field.group === "advanced").map((field) => field.name),
  };
  savedValues = Object.fromEntries(fields.map((field) => [field.name, field.default])) as never;
  summary = {
    run_id: "0817_1057_mx_y_rf_freq_resp", timestamp: "0817_1057", run_tag: "saved-tag",
    completion_status: "completed", analysis_status: "completed", analysis_error: "",
    parameters: { ...savedValues, RUN_TAG: "historical", FREQUENCY_POINTS: 201 },
    artifacts: { "r_phase_frequency.png": "/heatmap.png", "rf_frequency_response_comparison.png": "/comparison.png" },
    comparison_enabled: true, calibration: { extrapolated: true, target_voltage_v: 1.2, calibration_run: "coupling" },
    optimal_response_metadata: { valid_points: 9 }, constant_response_metadata: { valid_points: 3 },
    control_source: { run: "frozen", amplitude_vpp: .2, offset_v: 0 },
  };
  Object.assign(summary, { comparison_enabled: true, calibration: { extrapolated: true, target_voltage_v: 1.2, calibration_run: "coupling" }, optimal_response_metadata: { valid_points: 9 }, constant_response_metadata: { valid_points: 3 }, control_source: { run: "frozen", amplitude_vpp: .2, offset_v: 0 } });
  otherSummary = { ...summary, run_id: "0817_1157_mx_y_rf_freq_resp", run_tag: "other-tag",
    parameters: { ...savedValues, RUN_TAG: "other-history", FIT_R_SQUARED_MIN: 0.6 } };
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === BASE) return { id: ID, title: "Mx Y 最优控制 RF 频率响应", description: "相位中位数", required_devices: ["HF2"], wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { ...fixture.schema, parameter_layout: savedLayout,
      parameter_layout_saved: true, fields: fields.map((field) => ({ ...field, default: savedValues[field.name] })) } as never;
    if (url === `${BASE}/derive`) return { values: { frequency_point_count: 201 } } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["预检失败"] } as never;
    if (url === `${BASE}/defaults` && options?.method === "PUT") {
      const body = JSON.parse(String(options.body));
      savedLayout = body.parameter_layout;
      savedValues = { ...savedValues, ...body.parameters };
      return { message: "已保存", schema: { ...fixture.schema, parameter_layout: savedLayout,
        parameter_layout_saved: true, fields: fields.map((field) => ({ ...field, default: savedValues[field.name] })) } } as never;
    }
    if (url === "/api/jobs") return fullJob ? [fullJob] : [] as never;
    if (url === "/api/jobs/freq-job") return { id: "freq-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === "/api/jobs/full-job") return fullJob as never;
    if (url === "/api/jobs/analysis-job") return { id: "analysis-job", kind: `analysis:${ID}:${summary.run_id}`, status: "running", events: [] } as never;
    if (String(url).endsWith("/cancel")) return { id: String(url).split("/").at(-2), status: "cancelled", events: [] } as never;
    if (url === `${BASE}/runs` && options?.method === "POST") return fullJob = {
      id: "full-job", kind: `experiment:${ID}`, status: "running", events: [], message: "开始采集" };
    if (url === `${BASE}/workflow`) return flow as never;
    if (url === `${BASE}/workflow/diagram`) return { type: "excalidraw" } as never;
    if (url === `${BASE}/sessions/active`) return activeSession as never;
    if (url === `${BASE}/sessions` && options?.method === "POST") return activeSession = makeSession();
    if (url === `${BASE}/sessions/freq-session/steps`) {
      const body = JSON.parse(String(options?.body));
      if (body.step_ids.includes("A04") && activeSession.steps.A03.status !== "completed" && !body.step_ids.includes("A03")) {
        throw new Error("缺少前置：A03");
      }
      const finishedRound = body.step_ids.includes("A06");
      return activeSession = { ...activeSession, revision: activeSession.revision + 1,
        demod_mode: body.step_ids.includes("A03") ? "response" : activeSession.demod_mode,
        outputs: finishedRound ? { "Y RF": "OFF", X: "OFF", Z: "OFF", "温控": "ON", Pump: "ON" } : activeSession.outputs,
        steps: { ...activeSession.steps, ...Object.fromEntries(body.step_ids.map((id: string) => [id,
          { status: finishedRound && ["A03", "A04", "A05"].includes(id) ? "stale" : "completed", error: "" }])) } };
    }
    if (url === `${BASE}/sessions/freq-session/parameters`) {
      const body = JSON.parse(String(options?.body));
      if (rejectApply) throw new Error("模拟应用失败");
      const changed = Object.keys(body.parameters).filter(key => body.parameters[key] !== activeSession.parameters[key]);
      const impacts = parameterImpacts.acquisition as Record<string, string[]>;
      const stale = new Set(changed.flatMap(key => impacts[key] ?? []));
      return activeSession = { ...activeSession, parameters: body.parameters,
        revision: activeSession.revision + 1, steps: Object.fromEntries(acquisition.map((stage) => [stage.id,
          stale.has(stage.id) ? { status: activeSession.steps[stage.id].status === "pending" ? "pending" : "stale", error: "" }
            : activeSession.steps[stage.id]])) };
    }
    if (url === `${BASE}/sessions/freq-session/finish`) return activeSession = { ...activeSession,
      revision: activeSession.revision + 1, status: "closed", steps: { ...activeSession.steps, A12: { status: "completed", error: "" } } };
    if (url === `${BASE}/sessions/freq-session`) return activeSession as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [summary, otherSummary], total: 2 } as never;
    if (url === `${BASE}/runs/${summary.run_id}/summary`) return summary as never;
    if (url === `${BASE}/runs/${otherSummary.run_id}/summary`) return otherSummary as never;
    if (url === `${BASE}/runs/${summary.run_id}/analysis` && options?.method === "POST") {
      return { id: "analysis-job", kind: `analysis:${ID}:${summary.run_id}`, status: "running", events: [] } as never;
    }
    if (url === `${BASE}/runs/${summary.run_id}/analysis`) return { run_id: summary.run_id, revision: 3,
      options: {},
      steps: Object.fromEntries(analysisStages.map((stage) => [stage.id, { status: "completed", error: "" }])),
      summary: { status: "completed", ...diagnostics }, fingerprint: {} } as never;
    if (url === `${BASE}/runs/${otherSummary.run_id}/analysis`) return { run_id: otherSummary.run_id, revision: 1,
      options: {},
      steps: Object.fromEntries(analysisStages.map(stage => [stage.id, { status: "pending", error: "" }])),
      summary: {}, fingerprint: {} } as never;
    throw new Error(`未模拟接口 ${url}`);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const page = () => render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}>
  <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
</MemoryRouter>);
const editor = async (id: string) => {
  const stage = [...acquisition, ...analysisStages].find((item) => item.id === id)!;
  const region = await screen.findByRole("region", { name: `${id} ${stage.title} 参数`, hidden: true });
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  return within(region);
};
const advanced = (node: Awaited<ReturnType<typeof editor>>) => {
  const summary = node.queryByText(/^高级参数（/);
  if (summary && !summary.closest("details")!.open) fireEvent.click(summary);
};

it("真实路由双Tab、全字段节点归属和质量门槛，保留专属图与冻结来源", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "历史" })).toBeInTheDocument();
  for (const field of fields) expect(acquisition.some(stage => stage.display_parameters.includes(field.name))).toBe(true);
  expect(screen.getByText(/检查与质量说明：每点至少8个有效样本/)).toBeVisible();
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  expect(await screen.findByAltText("相位-频率热图")).toBeInTheDocument();
  expect(screen.getByText(/闭环冻结波形：frozen/)).toBeInTheDocument();
});

it("创建与依赖拒绝、执行步骤和无前置安全结束", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: "A04 最优控制频率×Burst相位扫描" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: 缺少前置：A03/);
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(activeSession.revision).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(activeSession.status).toBe("closed"));
  expect(screen.getByText(/下方状态为已结束会话/)).toBeInTheDocument();
});

it("共享参数草稿同步与撤回，应用后失效且不执行，分类同步保存", async () => {
  activeSession = makeSession(5);
  page();
  const optimal = await editor("A04");
  const constant = await editor("A05");
  const label = "系统稳定时间 (s)";
  const original = savedValues.SYSTEM_SETTLE_S;
  fireEvent.change(optimal.getByLabelText(label), { target: { value: ".7" } });
  expect(constant.getByLabelText(label)).toHaveValue(.7);
  expect(screen.getByText(/预计需重跑的已完成步骤：A04/)).toBeVisible();
  fireEvent.change(constant.getByLabelText(label), { target: { value: String(original) } });
  expect(screen.queryByText(/参数草稿尚未应用，请先/)).not.toBeInTheDocument();
  fireEvent.change(optimal.getByLabelText(label), { target: { value: ".6" } });
  rejectApply = true;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await screen.findByText("Error: 模拟应用失败");
  expect(optimal.getByLabelText(label)).toHaveValue(.6);
  rejectApply = false;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(activeSession.parameters.SYSTEM_SETTLE_S).toBe(.6));
  expect(activeSession.steps.A04.status).toBe("stale");
  expect(mockApi.mock.calls.some(([url]) => String(url).endsWith("/steps"))).toBe(false);
  fireEvent.click(optimal.getByRole("button", { name: "系统稳定时间 移至高级参数" }));
  advanced(constant);
  expect(constant.getByLabelText(label)).toHaveValue(.6);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(savedLayout.advanced).toContain("SYSTEM_SETTLE_S");
  expect(savedValues.SYSTEM_SETTLE_S).toBe(.6);
});

it("对照条件字段保值；历史独立分析无覆盖参数；原图与填回保持", async () => {
  page();
  const source = await editor("A01");
  fireEvent.change(source.getByLabelText("启用常数 Z 控制对照"), { target: { value: "true" } });
  fireEvent.change(source.getByLabelText("常数控制目标 Larmor 频率 (Hz)"), { target: { value: "1234" } });
  fireEvent.change(source.getByLabelText("启用常数 Z 控制对照"), { target: { value: "false" } });
  expect(source.queryByLabelText("常数控制目标 Larmor 频率 (Hz)")).not.toBeInTheDocument();
  fireEvent.change(source.getByLabelText("启用常数 Z 控制对照"), { target: { value: "true" } });
  expect(source.getByLabelText("常数控制目标 Larmor 频率 (Hz)")).toHaveValue(1234);
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: summary.run_id } });
  await waitFor(() => expect(screen.getByRole("button", { name: "选择全部分析步骤" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url.endsWith("/analysis") && options?.method === "POST")).toBe(true));
  const call = mockApi.mock.calls.find(([url, options]) => url.endsWith("/analysis") && options?.method === "POST")!;
  expect(JSON.parse(String(call[1]?.body)).analysis_parameters).toEqual({});
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(summary.run_id) }));
  expect(await screen.findByText(/目标电压超出标定支撑范围/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  const tag = await editor("A02");
  expect(tag.getByLabelText("运行标签")).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("SSE质量失败状态跨Tab保留，刷新恢复与安全结束可用", async () => {
  activeSession = makeSession(3);
  page();
  await screen.findByText(/分步等待期间保持输出状态/);
  await waitFor(() => expect(TestEventSource.instances.some(source => source.url.includes("freq-job"))).toBe(true));
  const stream = TestEventSource.instances.find(source => source.url.includes("freq-job"))!;
  await act(async () => stream.onmessage?.({ data: JSON.stringify({ index: 1, timestamp: "2026-10-15", stage: "A04", message: "质量失败",
    data: { workflow_session: { ...activeSession, revision: 1, steps: { ...activeSession.steps, A04: { status: "quality_failed", error: "重采用尽" } } } } }) }));
  expect(await screen.findByText("重采用尽")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "结束会话" })).toBeEnabled();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByText("重采用尽")).toBeInTheDocument();
});

it("预检失败不完整运行，切换Tab保留下一次实验草稿", async () => {
  page();
  const tag = await editor("A02");
  fireEvent.change(tag.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(tag.getByLabelText("运行标签")).toHaveValue("draft");
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("预检失败");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
});
