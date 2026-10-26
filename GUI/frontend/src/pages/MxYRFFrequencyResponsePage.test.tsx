import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./MxYRFFrequencyResponsePage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
const mockApi = vi.mocked(api);
const ID = "mx-y-rf-frequency-response";
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
const diagnostics = { per_amplitude: [{ amplitude_vpp: 0.01, success: true, split: true, r_squared: 0.99,
  parameters: { gamma_hz: 300, center_hz: 10000, saturation: 2 }, rejection_reasons: [] }],
  saturation_scaling: { success: false, n_points: 1 } };

const makeSession = (through = 0) => ({
  session_id: "freq-session", job_id: "freq-job", revision: 0, status: "waiting",
  parameters: Object.fromEntries(fields.map((field) => [field.name, field.default])),
  steps: Object.fromEntries(acquisition.map((stage, index) => [stage.id, { status: index < through ? "completed" : "pending", error: "" }])),
  outputs: { "Y RF": through >= 8 ? "ON" : through >= 4 ? "OFF" : "未知", "X": through >= 4 ? "OFF" : "未知",
    "Z": through >= 4 ? "OFF" : "未知", "温控": through >= 4 ? "ON" : "未知", "Pump": through >= 6 ? "ON" : "未知" },
  pending_request: null, demod_mode: through >= 7 ? "response" : "unknown",
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
    artifacts: { "results/frequency_response.png": "/plot.png", "results/s_vs_amplitude.png": "/saturation.png",
      "results/bloch_fit.npz": "/fit.npz" },
  };
  otherSummary = { ...summary, run_id: "0817_1157_mx_y_rf_freq_resp", run_tag: "other-tag",
    parameters: { ...savedValues, RUN_TAG: "other-history", FIT_R_SQUARED_MIN: 0.6 } };
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === BASE) return { id: ID, title: "Mx Y 向 RF 频率响应", description: "R(f)", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { ...fixture.schema, parameter_layout: savedLayout,
      parameter_layout_saved: true, fields: fields.map((field) => ({ ...field, default: savedValues[field.name] })) } as never;
    if (url === `${BASE}/derive`) return { values: { frequency_point_count: 201 } } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["RF_AMPLITUDE_POINTS 大于 1 时起点幅度必须小于终点幅度"] } as never;
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
      if (body.step_ids.includes("A08") && !body.step_ids.includes("A07")) {
        throw new Error("缺少前置：A07 Y RF 正弦与 R 解调准备");
      }
      const finishedRound = body.step_ids.includes("A09");
      return activeSession = { ...activeSession, revision: activeSession.revision + 1,
        demod_mode: body.step_ids.includes("A07") ? "response" : activeSession.demod_mode,
        outputs: finishedRound ? { "Y RF": "OFF", X: "OFF", Z: "OFF", "温控": "ON", Pump: "ON" } : activeSession.outputs,
        steps: { ...activeSession.steps, ...Object.fromEntries(body.step_ids.map((id: string) => [id,
          { status: finishedRound && ["A07", "A08"].includes(id) ? "stale" : "completed", error: "" }])) } };
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
      revision: activeSession.revision + 1, status: "closed", steps: { ...activeSession.steps, A10: { status: "completed", error: "" } } };
    if (url === `${BASE}/sessions/freq-session`) return activeSession as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [summary, otherSummary], total: 2 } as never;
    if (url === `${BASE}/runs/${summary.run_id}/summary`) return summary as never;
    if (url === `${BASE}/runs/${otherSummary.run_id}/summary`) return otherSummary as never;
    if (url === `${BASE}/runs/${summary.run_id}/analysis` && options?.method === "POST") {
      return { id: "analysis-job", kind: `analysis:${ID}:${summary.run_id}`, status: "running", events: [] } as never;
    }
    if (url === `${BASE}/runs/${summary.run_id}/analysis`) return { run_id: summary.run_id, revision: 3,
      options: { FIT_R_SQUARED_MIN: 0.2, FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX: 0.5, R_BAD_POINT_STD_THRESHOLD_V: 0.01 },
      steps: Object.fromEntries(analysisStages.map((stage) => [stage.id, { status: "completed", error: "" }])),
      summary: { status: "completed", ...diagnostics }, fingerprint: {} } as never;
    if (url === `${BASE}/runs/${otherSummary.run_id}/analysis`) return { run_id: otherSummary.run_id, revision: 1,
      options: { FIT_R_SQUARED_MIN: 0.6, FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX: 0.5, R_BAD_POINT_STD_THRESHOLD_V: 0.01 },
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

it("真实清单进入两列流程，质量门槛可见，缺前置拒绝，选择执行和结束使用本实验会话", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  expect(screen.getByText("A08 嵌套 RF 幅度-频率响应扫描")).toBeVisible();
  expect(screen.getByText("检查与质量说明：参数、映射、锁相采集能力与安全限值全部有效")).toBeVisible();
  expect(screen.getByText("检查与质量说明：每个 (幅度, 频率) 点 std(R) 合格；重采用尽后保存诊断并阻断后继")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: "A08 嵌套 RF 幅度-频率响应扫描" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: 缺少前置：A07/);
  expect(screen.getByRole("checkbox", { name: "A10 安全结束会话" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(activeSession.revision).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(activeSession.status).toBe("closed"));
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
});

it("共享扫描参数草稿同步、应用参数后显式重跑，不隐式执行", async () => {
  activeSession = makeSession(8);
  page();
  const sweep = await editor("A08");
  fireEvent.change(sweep.getByLabelText("系统稳定时间 (s)"), { target: { value: "0.7" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A08/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(activeSession.parameters.SYSTEM_SETTLE_S).toBe(0.7));
  expect(mockApi.mock.calls.filter(([url]) => String(url).endsWith("/steps"))).toHaveLength(0);
});

it("双 Tab 保留参数草稿，历史快照填回不启动，预检失败阻止完整运行", async () => {
  page();
  const tag = await editor("A02");
  fireEvent.change(tag.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "填回参数" });
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getAllByText("0817_1057_mx_y_rf_freq_resp")).toHaveLength(2);
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(tag.getByLabelText("运行标签")).toHaveValue("draft");
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("RF_AMPLITUDE_POINTS 大于 1 时起点幅度必须小于终点幅度");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(tag.getByLabelText("运行标签")).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("历史分析覆盖与下一次参数隔离，分析覆盖使用本实验三键", async () => {
  page();
  const next = await editor("B02");
  fireEvent.change(next.getByLabelText("拟合最低 R²"), { target: { value: "0.9" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: summary.run_id } });
  // 切换操作对象后分析节点先卸载再按历史覆盖重建，必须重新按文档查询。
  await waitFor(() => expect(screen.getByLabelText("拟合最低 R²")).toHaveValue(0.2));
  fireEvent.change(screen.getByLabelText("拟合最低 R²"), { target: { value: "0.4" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: otherSummary.run_id } });
  await waitFor(() => expect(screen.getByLabelText("拟合最低 R²")).toHaveValue(0.6));
  fireEvent.change(screen.getByLabelText("拟合最低 R²"), { target: { value: "0.55" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "" } });
  await waitFor(() => expect(screen.getByLabelText("拟合最低 R²")).toHaveValue(0.9));
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: summary.run_id } });
  await waitFor(() => expect(screen.getByLabelText("拟合最低 R²")).toHaveValue(0.4));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.FIT_R_SQUARED_MIN).toBe(0.9);
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs/${summary.run_id}/analysis`
    && options?.method === "POST" && JSON.parse(String(options.body)).analysis_parameters.FIT_R_SQUARED_MIN === 0.4)).toBe(true));
  expect(parameterImpacts.analysis.FIT_R_SQUARED_MIN).toEqual(["B02", "B03"]);
});

it("28 个真实可见字段均有节点编辑入口，初始化条件只在设置节点展示", async () => {
  page();
  expect(fields).toHaveLength(28);
  const exposed = new Set<string>();
  for (const stage of [...acquisition, ...analysisStages]) {
    if (!stage.display_parameters.length) continue;
    const node = await editor(stage.id);
    advanced(node);
    for (const key of stage.display_parameters) {
      const field = fields.find(item => item.name === key)!;
      const label = `${field.label}${field.unit ? ` (${field.unit})` : ""}`;
      expect(node.getByLabelText(label), key).toBeVisible();
      exposed.add(key);
    }
  }
  expect([...exposed].sort()).toEqual(fields.map(field => field.name).sort());
  expect(screen.getAllByLabelText("气室温度 (°C)")).toHaveLength(1);
  expect(screen.getAllByLabelText("Z 主磁场电流 (mA)")).toHaveLength(1);
  expect(screen.getAllByLabelText("Pump AOM 载波频率 (Hz)")).toHaveLength(1);
});

it("R 坏点阈值在采集和下一次分析共享，移动分类保存后回读且不改值", async () => {
  page();
  const capture = await editor("A08");
  const fit = await editor("B02");
  advanced(capture); advanced(fit);
  fireEvent.change(capture.getByLabelText("R 坏点标准差阈值 (V)"), { target: { value: "0.03" } });
  expect(fit.getByLabelText("R 坏点标准差阈值 (V)")).toHaveValue(0.03);
  fireEvent.click(capture.getByRole("button", { name: "R 坏点标准差阈值 移至基础参数" }));
  for (const node of [capture, fit]) {
    expect(node.getByRole("button", { name: "R 坏点标准差阈值 移至高级参数" })).toBeVisible();
    expect(node.getByLabelText("R 坏点标准差阈值 (V)")).toHaveValue(0.03);
  }
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(savedLayout.basic).toContain("R_BAD_POINT_STD_THRESHOLD_V");
  expect(savedLayout.advanced).not.toContain("R_BAD_POINT_STD_THRESHOLD_V");
  expect(new Set([...savedLayout.basic, ...savedLayout.advanced]).size).toBe(fields.length);
  cleanup(); page();
  const reopened = await editor("B02");
  expect(reopened.getByRole("button", { name: "R 坏点标准差阈值 移至高级参数" })).toBeVisible();
  expect(reopened.getByLabelText("R 坏点标准差阈值 (V)")).toHaveValue(0.03);
});

it("多参数草稿预览可撤销，应用失败保留草稿，成功按后端影响失效且不执行", async () => {
  activeSession = makeSession(8);
  const before = structuredClone(activeSession);
  page();
  const scan = await editor("A08");
  fireEvent.change(scan.getByLabelText("系统稳定时间 (s)"), { target: { value: "0.9" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A08/)).toBeVisible();
  fireEvent.change(scan.getByLabelText("系统稳定时间 (s)"), { target: { value: String(savedValues.SYSTEM_SETTLE_S) } });
  expect(screen.queryByText(/预计需重跑的已完成步骤/)).not.toBeInTheDocument();
  fireEvent.change(scan.getByLabelText("系统稳定时间 (s)"), { target: { value: "0.9" } });
  advanced(scan);
  fireEvent.change(scan.getByLabelText("R 坏点标准差阈值 (V)"), { target: { value: "0.03" } });
  rejectApply = true;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await screen.findByText(/模拟应用失败/);
  expect(activeSession).toEqual(before);
  expect(scan.getByLabelText("系统稳定时间 (s)")).toHaveValue(0.9);
  expect(screen.getByText(/参数草稿尚未应用/)).toBeVisible();
  rejectApply = false;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(activeSession.revision).toBe(1));
  const expected = new Set([...parameterImpacts.acquisition.SYSTEM_SETTLE_S,
    ...parameterImpacts.acquisition.R_BAD_POINT_STD_THRESHOLD_V]);
  expect(Object.entries(activeSession.steps).filter(([, value]) => (value as any).status === "stale").map(([id]) => id).sort())
    .toEqual([...expected].filter(id => before.steps[id].status === "completed").sort());
  expect(mockApi.mock.calls.some(([url]) => String(url).endsWith("/steps"))).toBe(false);
});

it("刷新恢复真实质量失败记录，后继缺前置可见并允许安全结束", async () => {
  activeSession = makeSession(7);
  activeSession.outputs["Y RF"] = "未知";
  activeSession.steps.A08 = { status: "quality_failed", error: "response_a000_f0000 连续 3 次 std(R) 超限" };
  page();
  expect(await screen.findByText(/response_a000_f0000 连续 3 次/)).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox", { name: "A09 本轮安全收尾与目录封存" }));
  expect(screen.getByText("缺少前置：A08")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(activeSession.status).toBe("closed"));
  expect(activeSession.steps.A10.status).toBe("completed");
});

it("最近结果与历史保留响应图、饱和度图、拟合诊断和产物下载", async () => {
  page();
  const run = within(screen.getByRole("tabpanel", { name: "运行" }));
  expect(await run.findByRole("table", { name: "布洛赫拟合诊断" })).toBeVisible();
  expect(run.getByText("Rabi 劈裂双峰")).toBeVisible();
  expect(run.getByRole("img", { name: "RF 频率响应与布洛赫拟合" })).toHaveAttribute("src", "/plot.png");
  expect(run.getByRole("img", { name: "饱和度与幅度平方" })).toHaveAttribute("src", "/saturation.png");
  expect(run.getByRole("link", { name: "results/bloch_fit.npz" })).toHaveAttribute("href", "/fit.npz");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await within(screen.getByRole("tabpanel", { name: "历史" })).findByRole("table", { name: "布洛赫拟合诊断" })).toBeVisible();
});

it("含 NaN 的旧报告经正规 API 展示五条诊断并保留两图与原文件下载", async () => {
  const artifact = `${BASE}/runs/${summary.run_id}/files/results/analysis.json?v=1786940929855757700`;
  summary.artifacts["results/analysis.json"] = artifact;
  const legacyJson = '{"per_amplitude": [{"uncertainties": {"gamma_hz": NaN}}]}';
  expect(() => JSON.parse(legacyJson)).toThrow();
  const curves = Array.from({ length: 5 }, (_, index) => ({ ...diagnostics.per_amplitude[0],
    amplitude_vpp: (index + 1) / 100, split: index >= 2,
    parameters: { gamma_hz: 373.4749916128946 + index, center_hz: 10984.7253789837, saturation: index < 2 ? 0 : index },
    uncertainties: { gamma_hz: index < 2 ? null : 17.499703036153363 },
  }));
  const currentApi = mockApi.getMockImplementation()!;
  const realApi = (await vi.importActual<typeof import("../api")>("../api")).api;
  const analysisUrl = `${BASE}/runs/${summary.run_id}/analysis`;
  const strictState = JSON.stringify({ run_id: summary.run_id, revision: 0, options: {}, fingerprint: {},
    steps: Object.fromEntries(analysisStages.map(stage => [stage.id, { status: "pending", error: "" }])),
    summary: { per_amplitude: curves, saturation_scaling: diagnostics.saturation_scaling },
  });
  const fetch = vi.fn(async (url: string) => new Response(url === artifact ? legacyJson : strictState,
    { headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetch);
  mockApi.mockImplementation((url, options) => [artifact, analysisUrl].includes(url)
    ? realApi(url, options) : currentApi(url, options));
  page();
  fireEvent.click(await screen.findByRole("tab", { name: "历史" }));
  const history = within(screen.getByRole("tabpanel", { name: "历史" }));
  const table = await history.findByRole("table", { name: "布洛赫拟合诊断" });
  expect(within(table).getAllByRole("row")).toHaveLength(6);
  expect(within(table).getAllByText("弱驱动单峰")).toHaveLength(2);
  expect(within(table).getAllByText("Rabi 劈裂双峰")).toHaveLength(3);
  expect(history.queryByRole("alert")).not.toBeInTheDocument();
  expect(history.getByRole("img", { name: "RF 频率响应与布洛赫拟合" })).toHaveAttribute("src", "/plot.png");
  expect(history.getByRole("img", { name: "饱和度与幅度平方" })).toHaveAttribute("src", "/saturation.png");
  expect(history.getByRole("link", { name: "results/analysis.json" })).toHaveAttribute("href", artifact);
  expect(fetch.mock.calls.some(([url]) => url === analysisUrl)).toBe(true);
  expect(fetch.mock.calls.some(([url]) => url === artifact)).toBe(false);
  expect(mockApi.mock.calls.some(([, options]) => options?.method === "POST")).toBe(false);
});

it("新运行无产物时显示空状态且不从旧报告伪造诊断", async () => {
  summary.artifacts = {};
  summary.analysis_status = "not_started";
  const currentApi = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    const value = await currentApi(url, options);
    return url === `${BASE}/runs/${summary.run_id}/analysis` ? { ...value as object, summary: {} } as never : value;
  });
  page();
  fireEvent.click(await screen.findByRole("tab", { name: "历史" }));
  const history = within(screen.getByRole("tabpanel", { name: "历史" }));
  expect(await history.findByText("本次运行暂无结果文件。")).toBeVisible();
  expect(history.queryByRole("table", { name: "布洛赫拟合诊断" })).not.toBeInTheDocument();
  expect(history.queryByRole("img")).not.toBeInTheDocument();
  expect(history.queryByRole("alert")).not.toBeInTheDocument();
});

it("质量失败继续显示当前拒绝原因与诊断下载，未生成的科学图保持缺失", async () => {
  summary.analysis_status = "quality_failed";
  summary.analysis_error = "没有任何幅度曲线通过布洛赫线形拟合质量门槛";
  summary.artifacts = { "results/fit_diagnostics.yaml": "/failed-diagnostics.yaml", "results/bloch_fit.npz": "/failed-fit.npz" };
  const currentApi = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    const value = await currentApi(url, options);
    return url === `${BASE}/runs/${summary.run_id}/analysis` ? { ...value as object,
      steps: { B01: { status: "completed" }, B02: { status: "quality_failed" }, B03: { status: "pending" } },
      summary: { status: "quality_failed", per_amplitude: [{ amplitude_vpp: 0.01, success: false,
        rejection_reasons: ["R² 低于拟合门槛"] }], saturation_scaling: { success: false, n_points: 0 } },
    } as never : value;
  });
  page();
  fireEvent.click(await screen.findByRole("tab", { name: "历史" }));
  const history = within(screen.getByRole("tabpanel", { name: "历史" }));
  const table = await history.findByRole("table", { name: "布洛赫拟合诊断" });
  expect(within(table).getByText("未通过")).toBeVisible();
  expect(within(table).getByText("R² 低于拟合门槛")).toBeVisible();
  expect(history.getByRole("alert")).toHaveTextContent(summary.analysis_error);
  expect(history.getByRole("link", { name: "results/fit_diagnostics.yaml" })).toHaveAttribute("href", "/failed-diagnostics.yaml");
  expect(history.queryByRole("img")).not.toBeInTheDocument();
});

it("流程顶部使用真实 JobView 订阅日志与安全停止，预检和完整运行沿真实接口", async () => {
  page();
  const save = await screen.findByRole("button", { name: "保存参数与布局" });
  const container = save.closest(".xy-workflow")!;
  expect(container).toContainElement(screen.getByRole("button", { name: "仅预检" }));
  expect(container).toContainElement(screen.getByRole("button", { name: "预检并运行实验" }));
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await waitFor(() => expect(TestEventSource.instances.find(item => item.url === "/api/jobs/full-job/events")?.onmessage).toBeTypeOf("function"));
  const source = TestEventSource.instances.find(item => item.url === "/api/jobs/full-job/events")!;
  act(() => source.onmessage!({ data: JSON.stringify({ index: 1, timestamp: "2026-10-15T12:00:00", stage: "A08",
    message: "嵌套扫描中", data: {} }) }));
  expect(within(container as HTMLElement).getByText("嵌套扫描中", { selector: "strong" })).toBeVisible();
  const stop = screen.getByRole("button", { name: "安全停止" });
  expect(container).toContainElement(stop);
  fireEvent.click(stop);
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === "/api/jobs/full-job/cancel" && options?.method === "POST")).toBe(true));
});
