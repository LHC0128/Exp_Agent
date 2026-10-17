import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./MxYRFSensitivityDriftPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
const ID = "mx-y-rf-sensitivity-drift";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const fields = fixture.schema.fields;
const acquisition = fixture.flow.acquisition;
const analysisStages = fixture.flow.analysis;
const impacts = fixture.flow.parameter_impacts.acquisition as Record<string, string[]>;
let session: any, summary: any, other: any, fullJob: any;
let savedValues: Record<string, number | string | boolean>;
let savedLayout: { basic: string[]; advanced: string[] };
let preflightOk: boolean, rejectApply: boolean, rejectHistory: boolean;
class TestEventSource {
  static instances: TestEventSource[] = [];
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(public url: string) { TestEventSource.instances.push(this); }
}
const makeSession = (count = 0) => ({
  session_id: "drift-session", job_id: "drift-job", revision: 0, status: "waiting", pending_request: null,
  parameters: { ...savedValues }, current_step: null,
  steps: Object.fromEntries(acquisition.map((stage, index) => [stage.id, { status: index < count ? "completed" : "pending", error: "" }])),
  outputs: { "Y RF": count >= 4 ? "OFF" : "未知", 温控: count >= 4 ? "ON" : "未知", Pump: count >= 6 ? "ON" : "未知" },
  demod_mode: count >= 7 ? "response" : "unknown", drift_records: [],
});

beforeEach(() => {
  session = fullJob = null;
  preflightOk = true; rejectApply = rejectHistory = false;
  TestEventSource.instances = [];
  vi.stubGlobal("EventSource", TestEventSource);
  savedValues = Object.fromEntries(fields.map(field => [field.name, field.default]));
  savedLayout = { basic: fields.filter(field => field.group === "basic").map(field => field.name),
    advanced: fields.filter(field => field.group === "advanced").map(field => field.name) };
  summary = { run_id: "0807_164227_mx_y_rf_drift", timestamp: "0807_164227", run_tag: "old-drift",
    completion_status: "failed", analysis_status: "not_started", analysis_error: "",
    parameters: { ...savedValues, RUN_TAG: "historical", DRIFT_INTERVAL_S: 60 },
    artifacts: { "results/drift_trend.png": "/elapsed.png", "results/drift_trend_clock_time.png": "/clock.png",
      "results/drift_summary.csv": "/rows.csv", "results/analysis.json": "/legacy-with-NaN.json" },
    drift_summary: { rows: [
      { cycle_index: 0, status: "completed", valid: true, actual_start_utc: "2026-08-07T08:42:27+00:00", actual_elapsed_s: 0,
        sensitivity_flat_median_ft_per_sqrt_hz: 48, zero_point_sensitivity_flat_median_ft_per_sqrt_hz: 64,
        linewidth_hwhm_hz: 240, response_fit_r_squared: .99, excluded_point_count: 0, error: null },
      { cycle_index: 81, status: "invalid_quality", valid: false, actual_elapsed_s: 3600,
        sensitivity_flat_median_ft_per_sqrt_hz: null, linewidth_hwhm_hz: null, error: "R 点质量未通过" },
      { cycle_index: 88, status: "pending", valid: false, error: null },
    ], invalid_cycles: [{ cycle_index: 81, reason: "R 点质量未通过" }, { cycle_index: 88, reason: "尚无子分析摘要" }] },
  };
  other = { ...structuredClone(summary), run_id: "0806_165142_mx_y_rf_drift", run_tag: "other",
    parameters: { ...savedValues, RUN_TAG: "other-history", DRIFT_INTERVAL_S: 120 } };
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    if (url === BASE) return { id: ID, title: "Mx Y RF 灵敏度长飘", description: "逐轮采集与父趋势", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { ...fixture.schema, parameter_layout: savedLayout,
      parameter_layout_saved: true, fields: fields.map(field => ({ ...field, default: savedValues[field.name] })) } as never;
    if (url === `${BASE}/workflow`) return fixture.flow as never;
    if (url === `${BASE}/derive`) return { values: { planned_cycle_count: 48 } } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["当前长飘计划不会产生测量轮次"] } as never;
    if (url === `${BASE}/defaults`) {
      const body = JSON.parse(String(options?.body));
      savedValues = body.parameters; savedLayout = body.parameter_layout;
      return { message: "已保存", schema: { ...fixture.schema, parameter_layout: savedLayout,
        parameter_layout_saved: true, fields: fields.map(field => ({ ...field, default: savedValues[field.name] })) } } as never;
    }
    if (url === "/api/jobs") return fullJob ? [fullJob] : [] as never;
    if (url === "/api/jobs/drift-job") return { id: "drift-job", kind: `session:${ID}`,
      status: session?.status === "closed" ? "completed" : "running", events: [] } as never;
    if (url === "/api/jobs/drift-full") return fullJob as never;
    if (url === "/api/jobs/drift-analysis") return { id: "drift-analysis", kind: `analysis:${ID}:${summary.run_id}`, status: "running", events: [] } as never;
    if (url.endsWith("/cancel")) return { id: url.split("/").at(-2), status: "cancelled", events: [] } as never;
    if (url === `${BASE}/runs` && options?.method === "POST") return fullJob = {
      id: "drift-full", kind: `experiment:${ID}`, status: "running", message: "等待绝对时间", events: [] };
    if (url === `${BASE}/sessions/active` || url === `${BASE}/sessions/drift-session`) return session as never;
    if (url === `${BASE}/sessions`) return session = makeSession();
    if (url === `${BASE}/sessions/drift-session/steps`) {
      const body = JSON.parse(String(options?.body));
      const chosen = new Set<string>(body.step_ids);
      for (const stage of acquisition.filter(stage => chosen.has(stage.id))) {
        const missing = stage.dependencies.filter(dep => !chosen.has(dep) && session.steps[dep].status !== "completed");
        if (missing.length) throw new Error(`缺少前置：${missing.join("、")}`);
      }
      const states = { ...session.steps, ...Object.fromEntries(body.step_ids.map((id: string) => [id, { status: "completed", error: "" }])) };
      if (chosen.has("A10")) for (const id of ["A07", "A08", "A09"]) states[id] = { status: "stale", error: "" };
      return session = { ...session, revision: session.revision + 1, steps: states,
        status: chosen.has("A11") ? "closed" : "waiting" };
    }
    if (url === `${BASE}/sessions/drift-session/parameters`) {
      if (rejectApply) throw new Error("模拟应用失败");
      const body = JSON.parse(String(options?.body));
      const changed = Object.keys(body.parameters).filter(key => body.parameters[key] !== session.parameters[key]);
      const stale = new Set(changed.flatMap(key => impacts[key] ?? []));
      return session = { ...session, parameters: body.parameters, revision: session.revision + 1,
        steps: Object.fromEntries(acquisition.map(stage => [stage.id, stale.has(stage.id)
          ? { status: session.steps[stage.id].status === "pending" ? "pending" : "stale", error: "" } : session.steps[stage.id]])) };
    }
    if (url === `${BASE}/sessions/drift-session/finish`) return session = { ...session, status: "closed", revision: session.revision + 1,
      steps: { ...session.steps, A11: { status: "completed", error: "" } } };
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary, other], total: 2 } as never;
    const target = [summary, other].find(run => url.startsWith(`${BASE}/runs/${run.run_id}/`));
    if (url.endsWith("/summary") && target) {
      if (rejectHistory) throw new Error("父分析摘要损坏");
      return target as never;
    }
    if (url.endsWith("/analysis") && target) {
      if (options?.method === "POST") return { id: "drift-analysis", kind: `analysis:${ID}:${target.run_id}`, status: "running", events: [] } as never;
      return { run_id: target.run_id, revision: target === summary ? 3 : 9, options: {}, fingerprint: {}, summary: {},
        steps: Object.fromEntries(analysisStages.map(stage => [stage.id, { status: "pending", error: "" }])) } as never;
    }
    if (url === `/api/runs/${summary.run_id}/analyze`) return {
      id: "drift-analysis", kind: `analysis:${ID}:${summary.run_id}`, status: "running", events: [] } as never;
    throw new Error(`未模拟长飘接口：${url}`);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const page = () => render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}>
  <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
</MemoryRouter>);
const editor = async (id: string) => {
  const stage = acquisition.find(stage => stage.id === id)!;
  const region = await screen.findByRole("region", { name: `${id} ${stage.title} 参数`, hidden: true });
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  return within(region);
};
const advanced = (node: Awaited<ReturnType<typeof editor>>) => {
  const toggle = node.queryByText(/^高级参数（/);
  if (toggle && !toggle.closest("details")!.open) fireEvent.click(toggle);
};

it("真实路由双 Tab、11+3节点和真实门槛，缺前置拒绝后可显式执行并无前置结束", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  expect(screen.getAllByRole("tab")).toHaveLength(2);
  expect(screen.getByText(/全无效轮仍可导出诊断/)).toBeVisible();
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: "A09 逐轮标准采集、即时分析与趋势" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: 缺少前置：A08/);
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(session.steps.A10.status).toBe("completed"));
  expect(session.steps.A07.status).toBe("stale");
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(session.status).toBe("closed"));
  expect(session.steps.A11.status).toBe("completed");
});

it("全部38个真实可见字段可编辑并保存，初始化和调度只在真实配置节点出现", async () => {
  page();
  const edited: Record<string, unknown> = {};
  for (const stage of acquisition.filter(stage => stage.display_parameters.length)) {
    const node = await editor(stage.id);
    advanced(node);
    for (const key of stage.display_parameters) {
      const field = fields.find(field => field.name === key)!;
      const label = `${field.label}${field.unit ? ` (${field.unit})` : ""}`;
      const input = node.getByLabelText(label);
      expect(input, key).toBeVisible();
      const value = field.type === "boolean" ? !field.default : field.type === "string" ? `${field.default}-edited`
        : Number(field.default) + (field.type === "integer" ? 1 : .001);
      fireEvent.change(input, { target: { value: String(value) } });
      edited[key] = value;
    }
  }
  expect(Object.keys(edited)).toHaveLength(38);
  expect(Object.keys(edited).sort()).toEqual(fields.map(field => field.name).sort());
  for (const label of ["气室温度 (°C)", "Z 主磁场电流 (mA)", "Pump AOM 载波频率 (Hz)", "长飘轮次间隔 (s)"]) {
    expect(screen.getAllByLabelText(label)).toHaveLength(1);
  }
  expect(screen.queryByLabelText(/模式1扫频起点/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(savedValues).toEqual(edited);
});

it("A09即时子分析设置保留下一次值，父B列无覆盖参数，分类跨历史对象保存回读", async () => {
  page();
  const node = await editor("A09"); advanced(node);
  fireEvent.change(node.getByLabelText("拟合最低 R²"), { target: { value: "0.8" } });
  fireEvent.click(node.getByRole("button", { name: "拟合最低 R² 移至基础参数" }));
  for (const target of [summary.run_id, other.run_id, "", summary.run_id]) {
    fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: target } });
    if (target) await waitFor(() => expect(screen.getByRole("button", { name: "选择全部分析步骤" })).toBeEnabled());
    expect(node.getByLabelText("拟合最低 R²")).toHaveValue(.8);
    expect(screen.getAllByLabelText("拟合最低 R²")).toHaveLength(1);
  }
  expect(fixture.flow.parameter_impacts.analysis).toEqual({});
  expect(analysisStages.every(stage => !stage.display_parameters.length)).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(savedValues.FIT_R_SQUARED_MIN).toBe(.8);
  expect(savedLayout.basic).toContain("FIT_R_SQUARED_MIN");
  expect(savedLayout.advanced).not.toContain("FIT_R_SQUARED_MIN");
  expect(new Set([...savedLayout.basic, ...savedLayout.advanced]).size).toBe(fields.length);
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs/${summary.run_id}/analysis`
    && options?.method === "POST" && Object.keys(JSON.parse(String(options.body)).analysis_parameters).length === 0)).toBe(true));
  cleanup(); page();
  const reopened = await editor("A09");
  expect(reopened.getByRole("button", { name: "拟合最低 R² 移至高级参数" })).toBeVisible();
  expect(reopened.getByLabelText("拟合最低 R²")).toHaveValue(.8);
});

it("多参数草稿可还原预览，应用失败保留草稿，应用后按真实影响失效且不执行", async () => {
  session = makeSession(9);
  const before = structuredClone(session);
  page();
  const schedule = await editor("A08");
  const capture = await editor("A09");
  fireEvent.change(schedule.getByLabelText("长飘轮次间隔 (s)"), { target: { value: "120" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A08/)).toBeVisible();
  fireEvent.change(schedule.getByLabelText("长飘轮次间隔 (s)"), { target: { value: String(savedValues.DRIFT_INTERVAL_S) } });
  expect(screen.queryByText(/预计需重跑的已完成步骤/)).not.toBeInTheDocument();
  fireEvent.change(schedule.getByLabelText("长飘轮次间隔 (s)"), { target: { value: "120" } });
  fireEvent.change(capture.getByLabelText("系统稳定时间 (s)"), { target: { value: "0.7" } });
  expect(session).toEqual(before);
  rejectApply = true;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await screen.findByText(/模拟应用失败/);
  expect(session).toEqual(before);
  expect(capture.getByLabelText("系统稳定时间 (s)")).toHaveValue(.7);
  rejectApply = false;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(session.revision).toBe(1));
  const expected = new Set([...impacts.DRIFT_INTERVAL_S, ...impacts.SYSTEM_SETTLE_S]);
  expect(Object.entries(session.steps).filter(([, value]) => (value as any).status === "stale").map(([id]) => id).sort())
    .toEqual([...expected].filter(id => before.steps[id].status === "completed").sort());
  expect(mockApi.mock.calls.some(([url]) => url.endsWith("/steps"))).toBe(false);
});

it("运行草稿跨Tab保留，历史参数填回不保存或启动，预检失败阻止完整运行", async () => {
  page();
  const node = await editor("A02");
  fireEvent.change(node.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "填回参数" });
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(node.getByLabelText("运行标签")).toHaveValue("draft");
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("当前长飘计划不会产生测量轮次");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(node.getByLabelText("运行标签")).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("旧科学缓存与pending分步记录分开，两图表格、原索引、无效原因和下载保留", async () => {
  page();
  fireEvent.click(await screen.findByRole("tab", { name: "历史" }));
  const history = within(screen.getByRole("tabpanel", { name: "历史" }));
  const table = await history.findByRole("table", { name: "长飘轮次趋势" });
  expect(within(table).getAllByRole("row")).toHaveLength(4);
  expect(within(table).getByText("81")).toBeVisible();
  expect(within(table).getByText("88")).toBeVisible();
  expect(within(table).getByText("R 点质量未通过")).toBeVisible();
  expect(within(table).getByText("尚无子分析摘要")).toBeVisible();
  expect(history.getByText(/未开始分步分析/)).toBeVisible();
  expect(history.getByText(/已有趋势缓存 3 轮，其中科学有效 1 轮/)).toBeVisible();
  expect(history.getByRole("img", { name: "长飘趋势（实际经过时间）" })).toHaveAttribute("src", "/elapsed.png");
  expect(history.getByRole("img", { name: "长飘趋势（上海日期时间）" })).toHaveAttribute("src", "/clock.png");
  expect(history.getByRole("link", { name: "results/drift_summary.csv" })).toHaveAttribute("href", "/rows.csv");
  fireEvent.click(history.getByRole("button", { name: "重新分析" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `/api/runs/${summary.run_id}/analyze`
    && JSON.parse(String(options?.body)).experiment_id === ID)).toBe(true));
});

it("旧NaN摘要只从严格JSON专属summary读取，不直接fetch旧科学文件", async () => {
  const originalApi = mockApi.getMockImplementation()!;
  const realApi = (await vi.importActual<typeof import("../api")>("../api")).api;
  const url = `${BASE}/runs/${summary.run_id}/summary`;
  const fetch = vi.fn(async (path: string) => new Response(path === url ? JSON.stringify(summary) : '{"rows":[NaN]}',
    { headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetch);
  mockApi.mockImplementation((path, options) => path === url ? realApi(path, options) : originalApi(path, options));
  page();
  await screen.findByRole("table", { name: "长飘轮次趋势" });
  expect(fetch.mock.calls.length).toBeGreaterThan(0);
  expect(fetch.mock.calls.every(([path]) => path === url)).toBe(true);
  expect(mockApi.mock.calls.some(([, options]) => options?.method === "POST")).toBe(false);
});

it("全部无效仍展示诊断导出，空产物与加载失败明确反馈", async () => {
  summary.drift_summary.rows.forEach((row: any) => { row.valid = false; });
  summary.drift_summary.valid_cycle_count = 0;
  summary.analysis_status = "quality_failed";
  summary.analysis_error = "父清单引用子运行身份不匹配";
  page();
  const run = within(screen.getByRole("tabpanel", { name: "运行" }));
  expect(await run.findByText("当前全部轮次无效，仍可查看和导出诊断。")).toBeVisible();
  expect(run.getByText(/分析：质量未通过/)).toBeVisible();
  expect(run.getByText(summary.analysis_error)).toBeVisible();
  expect(run.getByRole("link", { name: "results/drift_summary.csv" })).toBeVisible();
  cleanup(); summary.artifacts = {}; summary.drift_summary = {}; summary.analysis_error = "";
  page(); expect(await screen.findByText("本次运行暂无结果文件。")).toBeVisible();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  cleanup(); rejectHistory = true; page();
  expect(await screen.findByRole("alert")).toHaveTextContent("父分析摘要损坏");
});

it("刷新恢复失败记录，SSE订阅后更新进度，安全停止在流程顶部", async () => {
  session = makeSession(8);
  session.steps.A09 = { status: "failed", error: "温控恢复失败，后继轮次停止" };
  page();
  expect(await screen.findByText("温控恢复失败，后继轮次停止")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(session.status).toBe("closed"));
  cleanup(); session = null; page();
  const button = await screen.findByRole("button", { name: "保存参数与布局" });
  const container = button.closest(".xy-workflow") as HTMLElement;
  expect(container).toContainElement(screen.getByRole("button", { name: "仅预检" }));
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await waitFor(() => expect(TestEventSource.instances.find(item => item.url === "/api/jobs/drift-full/events")?.onmessage).toBeTypeOf("function"));
  const source = TestEventSource.instances.find(item => item.url === "/api/jobs/drift-full/events")!;
  act(() => source.onmessage!({ data: JSON.stringify({ index: 1, timestamp: "2026-10-15T10:00:00", stage: "A09",
    message: "第二轮等待绝对开始时间", data: {} }) }));
  expect(within(container).getByText("第二轮等待绝对开始时间", { selector: "strong" })).toBeVisible();
  const stop = screen.getByRole("button", { name: "安全停止" });
  expect(container).toContainElement(stop); fireEvent.click(stop);
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url === "/api/jobs/drift-full/cancel")).toBe(true));
});
