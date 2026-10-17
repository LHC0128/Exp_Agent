import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./ScopeCapturePage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));
vi.mock("../components/ScopePlot", () => ({ ScopePlot: (props: any) => <div data-testid="scope-plot"
  data-traces={JSON.stringify(props.traces)} data-mode={props.mode}
  data-x-scale={props.xScale} data-y-scale={props.yScale} /> }));
const mockApi = vi.mocked(api);
const BASE = "/api/experiments/scope-capture";
const fields = fixture.schema.fields;
const acquisition = fixture.flow.acquisition;
const impacts = fixture.flow.parameter_impacts.acquisition as Record<string, string[]>;
const params = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const layout = { basic: fields.filter(f => f.group === "basic").map(f => f.name),
  advanced: fields.filter(f => f.group === "advanced").map(f => f.name) };
const makeSession = (completed = false) => ({ session_id: "scope-session", job_id: "scope-job", revision: 0,
  status: "waiting", parameters: params(), current_step: null, pending_request: null,
  steps: Object.fromEntries(acquisition.map(stage => [stage.id,
    { status: completed && stage.id !== "A05" ? "completed" : "pending", error: "" }])),
  outputs: { "SDS": "已连接", "温控开关": "5 V ON" } });
const workflowRuns = { runs: [{ run_id: "1015_0900_scope", run_tag: "scope", timestamp: "1015_0900",
  completion_status: "completed" }], total: 1 };
const genericRuns = { total: 2, offset: 0, limit: 50, runs: [
  { id: "1015_0900_scope", experiment_id: "scope-capture", experiment_title: "示波器采集",
    path: "data/Scope_Capture/1015_0900_scope", artifacts: ["display.json", "psd.json", "psd.npz", "waveform_psd.png"],
    can_analyze: true, modified_at: 1 },
  { id: "1015_0930_scope", experiment_id: "scope-capture", experiment_title: "示波器采集",
    path: "data/Scope_Capture/1015_0930_scope", artifacts: [], can_analyze: true, modified_at: 2 }] };
const summary = () => ({ run_id: "1015_0900_scope", run_tag: "scope", timestamp: "1015_0900",
  completion_status: "completed", analysis_status: "completed", analysis_error: "",
  parameters: { ...params(), duration_s: 0.02, run_tag: "restored" },
  artifacts: { "results/display.json": "/display.json", "results/psd.npz": "/psd.npz",
    "results/waveform_psd.png": "/waveform_psd.png" } });
const display = () => ({ channel: 1, sample_count: 500, actual_rate_sa_s: 50000, actual_duration_s: 0.01,
  requested_rate_sa_s: 50000, requested_duration_s: 0.01, time_s: [0, 0.005, 0.01], voltage_v: [0.1, 0.9, 0.1],
  display_decimated: true, vertical_scale_v_div: 1, vertical_offset_v: 0,
  vertical_range_min_v: -4, vertical_range_max_v: 4, overrange: true, overrange_fraction: 0.012 });
const spectrum = () => ({ frequency_hz: [0, 1000], psd_v2_hz: [1e-6, 2e-6], frequency_resolution_hz: 1000 });
let activeSession: any;
let preflightOk: boolean;
let savedLayout: typeof layout;
let savedValues: ReturnType<typeof params>;
let remaining: typeof genericRuns.runs;

beforeEach(() => {
  localStorage.clear();
  activeSession = null; preflightOk = true;
  savedLayout = { basic: [...layout.basic], advanced: [...layout.advanced] }; savedValues = params();
  remaining = [...genericRuns.runs];
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    if (url === BASE) return { title: "示波器采集", description: "单通道时域采集与软件 Welch PSD。", wiring_notes: [], safety_notes: [] } as never;
    if (url === `${BASE}/schema`) return { schema_version: fixture.schema.schema_version,
      fields: fields.map(field => ({ ...field, default: savedValues[field.name] })),
      parameter_layout_saved: true, parameter_layout: savedLayout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === `${BASE}/workflow`) return fixture.flow as never;
    if (url === `${BASE}/sessions/active`) return activeSession as never;
    if (url === "/api/jobs/scope-job") return { id: "scope-job", kind: "session:scope-capture", status: activeSession?.status === "closed" ? "completed" : "running", events: [] } as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return workflowRuns as never;
    if (String(url).endsWith("/1015_0900_scope/summary")) return summary() as never;
    if (String(url).endsWith("/1015_0900_scope/analysis") && !options?.method) return {
      run_id: "1015_0900_scope", revision: 0, options: {}, summary: { status: "not_started" },
      steps: Object.fromEntries(fixture.flow.analysis.map(stage => [stage.id, { status: "pending", error: "" }])) } as never;
    if (String(url).endsWith("/1015_0900_scope/analysis") && options?.method === "POST") return {
      id: "analysis-job", kind: "analysis:scope-capture:1015_0900_scope", status: "completed", events: [] } as never;
    if (String(url).startsWith("/api/runs?experiment_id=scope-capture")) return { ...genericRuns, runs: remaining, total: remaining.length } as never;
    if (String(url).includes("/artifacts/display.json")) return display() as never;
    if (String(url).includes("/artifacts/psd.json")) return spectrum() as never;
    if (url === `${BASE}/derive`) return { values: {} } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["第一版单次记录须为 2 到 50,000,000 点，请调整采样率或采集时间"] } as never;
    if (url === `${BASE}/defaults`) {
      const body = JSON.parse(String(options?.body));
      savedLayout = body.parameter_layout; savedValues = body.parameters;
      return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: savedLayout } } as never;
    }
    if (url === `${BASE}/sessions` && options?.method === "POST") return activeSession = makeSession();
    if (url === `${BASE}/sessions/scope-session/parameters`) {
      const body = JSON.parse(String(options?.body));
      const changed = Object.keys(body.parameters).filter(key => body.parameters[key] !== activeSession.parameters[key]);
      const stale = new Set(changed.flatMap(key => impacts[key] ?? []));
      return activeSession = { ...activeSession, revision: activeSession.revision + 1, parameters: body.parameters,
        steps: Object.fromEntries(Object.entries(activeSession.steps).map(([id, value]) =>
          [id, stale.has(id) ? { status: "stale", error: "" } : value])) };
    }
    if (url === `${BASE}/sessions/scope-session/steps`) {
      const body = JSON.parse(String(options?.body));
      if (body.step_ids.includes("A04") && !body.step_ids.includes("A03") && activeSession.steps.A03.status !== "completed")
        throw new Error("A04 温控门控采集与波形保存 缺少前置：A03 示波器采集配置与回读校验");
      return activeSession = { ...activeSession, revision: activeSession.revision + 1,
        steps: { ...activeSession.steps, ...Object.fromEntries(body.step_ids.map((id: string) => [id, { status: "completed", error: "" }])) } };
    }
    if (url === `${BASE}/sessions/scope-session/finish`) return activeSession = { ...activeSession,
      status: "closed", revision: activeSession.revision + 1, current_step: null, pending_request: null,
      steps: { ...activeSession.steps, A05: { status: "completed", error: "" } },
      outputs: { "SDS": "STOP；已断开", "温控开关": "5 V ON；已断开" } };
    if (String(url).endsWith("/analyze") && options?.method === "POST") return { id: "analysis-job", status: "completed", events: [] } as never;
    if (options?.method === "DELETE") {
      remaining = remaining.filter(run => !String(url).endsWith(run.id));
      return { ok: true } as never;
    }
    throw new Error(`未模拟 ${url} ${options?.method ?? ""}`);
  });
});
afterEach(cleanup);
const page = () => render(<MemoryRouter initialEntries={["/experiments/scope-capture"]}>
  <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);
async function openParameters(id: string) {
  const stage = acquisition.find(item => item.id === id)!;
  const region = await screen.findByLabelText(`${id} ${stage.title} 参数`);
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  return within(region);
}
const historyPanel = () => within(screen.getByRole("tabpanel", { name: "历史" }));

it("真实路由提供运行/历史、节点编辑保留、历史叠加波形与超量程诊断、填回参数且预检失败不启动", async () => {
  page();
  const edit = await openParameters("A03");
  expect(screen.getAllByRole("tab")).toHaveLength(2);
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  fireEvent.change(edit.getByLabelText("采集时间 (s)"), { target: { value: "0.02" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const history = historyPanel();
  // 通用历史记录列表；勾选后加载真实 display/psd 产物叠加。
  fireEvent.click(history.getByRole("checkbox", { name: "选择 1015_0900_scope" }));
  expect(await history.findByText("超量程")).toBeVisible();
  expect(history.getByText(/1\.20%/)).toBeVisible();
  expect(history.getByText(/V\/div/)).toBeVisible();
  expect(history.getByText(/-4 ~ 4 V/)).toBeVisible();
  // 显示模式切换保留稳定参数键 mode（时域 → 频域）。
  fireEvent.click(history.getByRole("button", { name: "频域" }));
  expect(await history.findByText(/频率间隔/)).toBeVisible();
  // 填回参数走工作流 summary，历史叠加选择不写入运行参数。
  fireEvent.click(history.getByRole("button", { name: "填回参数到运行页" }));
  await waitFor(() => expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true"));
  expect((await openParameters("A03")).getByLabelText("采集时间 (s)")).toHaveValue(0.02);
  const restoreCalls = mockApi.mock.calls.filter(([url]) => String(url).endsWith("/1015_0900_scope/summary"));
  expect(restoreCalls.length).toBeGreaterThan(0);
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect((await screen.findAllByText(/2 到 50,000,000 点/)).length).toBeGreaterThan(0);
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("显式选择拒绝缺前置，选择全部后执行并能结束会话且不再等待", async () => {
  page();
  const create = await screen.findByRole("button", { name: "创建分步会话" });
  await waitFor(() => expect(create).toBeEnabled());
  fireEvent.click(create);
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: "A04 温控门控采集与波形保存" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 \/ 重跑" }));
  await screen.findByText(/A04 温控门控采集与波形保存 缺少前置：A03/);
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 \/ 重跑" }));
  await waitFor(() => expect(activeSession.steps.A04.status).toBe("completed"));
  expect(activeSession.steps.A05.status).toBe("pending");
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await screen.findByText(/下方状态为已结束会话的执行记录/);
  // 结束节点完成后会话关闭，不再处于等待。
  expect(activeSession.steps.A05.status).toBe("completed");
  expect(activeSession.status).toBe("closed");
  expect(activeSession.outputs["温控开关"]).toBe("5 V ON；已断开");
});

it("恢复采集器会话后草稿预览指出前置 stale，真实应用只失效不执行", async () => {
  activeSession = makeSession(true);
  activeSession.parameters.duration_s = 0.02;
  page();
  const edit = await openParameters("A03");
  await waitFor(() => expect(edit.getByLabelText("采集时间 (s)")).toHaveValue(0.02));
  fireEvent.change(edit.getByLabelText("采集时间 (s)"), { target: { value: "0.05" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A03.*A04/)).toBeVisible();
  expect(activeSession.steps.A03.status).toBe("completed");
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(activeSession.steps.A03.status).toBe("stale"));
  expect(activeSession.steps.A04.status).toBe("stale");
  expect(activeSession.steps.A02.status).toBe("completed");
  expect(mockApi.mock.calls.filter(([url]) => url === `${BASE}/sessions/scope-session/steps`)).toHaveLength(0);
});

it("采样率草稿在节点内编辑并按分类保存，重载后布局保持", async () => {
  page();
  const edit = await openParameters("A03");
  fireEvent.change(edit.getByLabelText("采样率 (Sa/s)"), { target: { value: "250000" } });
  expect(edit.getByLabelText("采样率 (Sa/s)")).toHaveValue(250000);
  fireEvent.click(edit.getByRole("button", { name: "采样率 移至高级参数" }));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`);
  expect(JSON.parse(String(saved?.[1]?.body)).parameters.sampling_rate_sa_s).toBe(250000);
  expect(JSON.parse(String(saved?.[1]?.body)).parameter_layout.advanced).toContain("sampling_rate_sa_s");
  cleanup();
  page();
  const reloaded = await openParameters("A03");
  fireEvent.click(reloaded.getByText(/高级参数（\d+）/));
  expect(reloaded.getByRole("button", { name: "采样率 移至基础参数" })).toBeVisible();
  expect(reloaded.getByLabelText("采样率 (Sa/s)")).toHaveValue(250000);
});

it("历史叠加支持多选隐藏、颜色保留与删除确认，重新分析仅限单条", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  page();
  await screen.findByRole("button", { name: "创建分步会话" });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const history = historyPanel();
  expect(history.getByRole("button", { name: "重新分析选定数据" })).toBeDisabled();
  fireEvent.click(history.getByRole("checkbox", { name: "选择 1015_0900_scope" }));
  expect(history.getByRole("button", { name: "重新分析选定数据" })).toBeEnabled();
  fireEvent.click(history.getByRole("button", { name: "隐藏 1015_0900_scope" }));
  await waitFor(() => expect(history.getByText("0 条曲线")).toBeInTheDocument());
  fireEvent.change(history.getByLabelText("颜色 1015_0900_scope"), { target: { value: "#ff0000" } });
  expect(localStorage.getItem("exp-agent:scope-capture:colors")).toContain("#ff0000");
  fireEvent.click(history.getByRole("button", { name: "删除选定历史数据" }));
  // 取消确认不删除。
  expect(mockApi.mock.calls.some(([, options]) => options?.method === "DELETE")).toBe(false);
  confirm.mockReturnValue(true);
  fireEvent.click(history.getByRole("button", { name: "删除选定历史数据" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) =>
    String(url).endsWith("/api/runs/scope-capture/1015_0900_scope") && options?.method === "DELETE")).toBe(true));
  await waitFor(() => expect(history.queryByRole("checkbox", { name: "选择 1015_0900_scope" })).not.toBeInTheDocument());
  expect(history.getByRole("checkbox", { name: "选择 1015_0930_scope" })).toBeInTheDocument();
});

it("全部可见参数均在节点内编辑，显示模式与历史按钮共享同一值且不使硬件失效", async () => {
  activeSession = makeSession(true);
  page();
  for (const stage of acquisition.filter(item => item.display_parameters.length)) {
    const edit = await openParameters(stage.id);
    const advanced = edit.queryByText(/高级参数（\d+）/);
    if (advanced) fireEvent.click(advanced);
    for (const name of stage.display_parameters) {
      const field = fields.find(item => item.name === name)!;
      expect(edit.getByLabelText(field.label + (field.unit ? ` (${field.unit})` : ""))).toBeVisible();
    }
  }
  const capture = await openParameters("A04");
  fireEvent.change(capture.getByLabelText("模式"), { target: { value: "frequency" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：暂无/)).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(historyPanel().getByRole("button", { name: "频域" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(historyPanel().getByRole("button", { name: "时间" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect((await openParameters("A04")).getByLabelText("模式")).toHaveValue("time");
  expect(screen.queryByText("参数草稿尚未应用，请先应用参数再执行模块。")).not.toBeInTheDocument();
  expect(mockApi.mock.calls.some(([url]) => String(url).endsWith("/parameters"))).toBe(false);
});

it("多参数草稿合并真实影响范围，全部还原后预览消失，应用失败保留草稿", async () => {
  activeSession = makeSession(true);
  page();
  const scope = await openParameters("A03");
  const capture = await openParameters("A04");
  fireEvent.change(scope.getByLabelText("采集时间 (s)"), { target: { value: "0.02" } });
  fireEvent.change(capture.getByLabelText("系统稳定时间 (s)"), { target: { value: "0.2" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A03.*A04/)).toBeVisible();
  fireEvent.change(scope.getByLabelText("采集时间 (s)"), { target: { value: String(params().duration_s) } });
  fireEvent.change(capture.getByLabelText("系统稳定时间 (s)"), { target: { value: String(params().SYSTEM_SETTLE_S) } });
  expect(screen.queryByText(/预计需重跑的已完成步骤/)).not.toBeInTheDocument();
  fireEvent.change(scope.getByLabelText("采集时间 (s)"), { target: { value: "0.02" } });
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (String(url).endsWith("/parameters")) throw new Error("修订冲突，请刷新");
    return original(url, options);
  });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await screen.findByText("Error: 修订冲突，请刷新");
  expect(scope.getByLabelText("采集时间 (s)")).toHaveValue(.02);
  expect(activeSession.steps.A03.status).toBe("completed");
  expect(screen.getByText(/参数草稿尚未应用/)).toBeVisible();
});

it("历史分析无覆盖字段，切换目录不污染下一次实验，保存默认不执行分析", async () => {
  page();
  const edit = await openParameters("A03");
  fireEvent.change(edit.getByLabelText("采集时间 (s)"), { target: { value: "0.03" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "1015_0900_scope" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "选择全部分析步骤" })).toBeEnabled());
  expect(screen.queryByLabelText("PSD 窗")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(savedValues.duration_s).toBe(.03);
  expect(mockApi.mock.calls.some(([url, options]) => String(url).endsWith("/analysis") && options?.method === "POST")).toBe(false);
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "" } });
  expect(edit.getByLabelText("采集时间 (s)")).toHaveValue(.03);
});

it("两个历史目录的真实曲线分别传入绘图组件，颜色隐藏和频谱坐标切换保持对象隔离", async () => {
  remaining = genericRuns.runs.map(run => ({ ...run, artifacts: ["display.json", "psd.json"] }));
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (String(url).includes("/1015_0930_scope/artifacts/display.json")) return {
      ...display(), channel: 2, voltage_v: [.2, .5, .7] } as never;
    if (String(url).includes("/1015_0930_scope/artifacts/psd.json")) return {
      frequency_hz: [0, 2000], psd_v2_hz: [3e-6, 4e-6], frequency_resolution_hz: 2000 } as never;
    return original(url, options);
  });
  page();
  await screen.findByRole("button", { name: "创建分步会话" });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const history = historyPanel();
  const traces = () => JSON.parse(screen.getByTestId("scope-plot").getAttribute("data-traces")!);
  fireEvent.click(history.getByRole("checkbox", { name: "选择 1015_0900_scope" }));
  fireEvent.click(history.getByRole("checkbox", { name: "选择 1015_0930_scope" }));
  await waitFor(() => expect(traces()).toHaveLength(2));
  expect(traces().map((trace: any) => trace.y)).toEqual([[.1, .9, .1], [.2, .5, .7]]);
  expect(traces().map((trace: any) => trace.rangeMin)).toEqual([-4, -4]);
  expect(history.getByRole("button", { name: "重新分析选定数据" })).toBeDisabled();
  fireEvent.change(history.getByLabelText("颜色 1015_0900_scope"), { target: { value: "#aa0000" } });
  expect(traces()[0].color).toBe("#aa0000");
  fireEvent.click(history.getByRole("button", { name: "隐藏 1015_0900_scope" }));
  expect(traces().map((trace: any) => trace.id)).toEqual(["1015_0930_scope"]);
  fireEvent.click(history.getByRole("button", { name: "显示 1015_0900_scope" }));
  fireEvent.click(history.getByRole("button", { name: "频域" }));
  expect(traces().map((trace: any) => trace.x)).toEqual([[0, 1000], [0, 2000]]);
  expect(traces().map((trace: any) => trace.y)).toEqual([[1e-6, 2e-6], [3e-6, 4e-6]]);
  expect(traces()[0]).not.toHaveProperty("rangeMin");
  fireEvent.click(history.getByLabelText("频率轴 log"));
  fireEvent.click(history.getByLabelText("PSD 轴 log"));
  expect(screen.getByTestId("scope-plot")).toHaveAttribute("data-x-scale", "log");
  expect(screen.getByTestId("scope-plot")).toHaveAttribute("data-y-scale", "log");
});

it("旧重新分析结束后丢弃旧显示缓存并刷新选定目录的实际曲线", async () => {
  let analyzed = false;
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (String(url).endsWith("/analyze") && options?.method === "POST") analyzed = true;
    if (String(url).includes("/1015_0900_scope/artifacts/display.json")) return {
      ...display(), voltage_v: analyzed ? [9, 8, 7] : [.1, .9, .1] } as never;
    return original(url, options);
  });
  page();
  await screen.findByRole("button", { name: "创建分步会话" });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const history = historyPanel();
  const values = () => JSON.parse(screen.getByTestId("scope-plot").getAttribute("data-traces")!)[0]?.y;
  fireEvent.click(history.getByRole("checkbox", { name: "选择 1015_0900_scope" }));
  await waitFor(() => expect(values()).toEqual([.1, .9, .1]));
  fireEvent.click(history.getByRole("button", { name: "重新分析选定数据" }));
  await waitFor(() => expect(values()).toEqual([9, 8, 7]));
  const request = mockApi.mock.calls.find(([url, options]) =>
    url === "/api/runs/1015_0900_scope/analyze" && options?.method === "POST");
  expect(JSON.parse(String(request?.[1]?.body))).toEqual({ experiment_id: "scope-capture" });
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults` || url === `${BASE}/runs`)).toBe(false);
});
