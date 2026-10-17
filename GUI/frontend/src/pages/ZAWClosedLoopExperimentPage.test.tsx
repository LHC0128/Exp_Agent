import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./ZAWClosedLoopExperimentPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));
const mockApi = vi.mocked(api);
const BASE = "/api/experiments/z-aw-closed-loop-waveform-correction";
const fields = fixture.schema.fields;
const layout = { basic: fields.filter(f => f.group === "basic").map(f => f.name),
  advanced: fields.filter(f => f.group !== "basic").map(f => f.name) };
const params = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const flow = fixture.workflow;
const acquisition = flow.acquisition;
const analysisStages = flow.analysis;
const impacts: Record<string, string[]> = flow.parameter_impacts.acquisition;
const makeSession = (completed = false) => ({ session_id: "scope-session", job_id: "scope-job", revision: 0,
  status: "waiting", parameters: params(), current_step: null, pending_request: null,
  steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: completed && stage.id !== "A06" ? "completed" : "pending", error: "" }])),
  outputs: { "Z 任意波": "Burst ON", "共同触发": "ON", Scope: "STOP" } });
let activeSession: any;
let savedLayout = layout;
let preflightOk: boolean;
let rejectApply: boolean;

beforeEach(() => {
  activeSession = null; savedLayout = layout; preflightOk = true; rejectApply = false;
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    if (url === BASE) return { title: "Z 任意波实际电流闭环校正", description: "SDS CH3 采样电阻电压与 CH4 触发", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: savedLayout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === `${BASE}/workflow`) return flow as never;
    if (url === `${BASE}/sessions/active`) return activeSession as never;
    if (url === "/api/jobs/scope-job") return { id: "scope-job", status: "running", events: [] } as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [
      { run_id: "history-one", run_tag: "old", timestamp: "1014", completion_status: "completed" },
      { run_id: "history-two", run_tag: "old2", timestamp: "1013", completion_status: "failed" }], total: 2 } as never;
    if (String(url).endsWith("/summary")) return { run_id: "history-one", run_tag: "old", timestamp: "1014", completion_status: "completed",
      analysis_status: "completed", analysis_error: "", parameters: { ...params(), RUN_TAG: "historical", CONTROL_SCALE: .7 },
      target_reached: true, best_iteration: 0, best_relative_rms_error: .02, stop_reason: "达标",
      iteration_count: 2, target_relative_rms: .03, static_calibration: { gain_a_per_v: .01 },
      artifacts: { "waveform_comparison.png": "/waveform.png" } } as never;
    if (url === `${BASE}/derive`) return { values: { CONTROL_REPEAT_FREQUENCY_HZ: 1000 } } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["控制波形超出安全范围"] } as never;
    if (url === `${BASE}/defaults`) { savedLayout = JSON.parse(String(options?.body)).parameter_layout; return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: savedLayout } } as never; }
    if (url === `${BASE}/sessions` && options?.method === "POST") return activeSession = makeSession();
    if (url === `${BASE}/sessions/scope-session/parameters`) {
      if (rejectApply) throw new Error("模拟应用失败");
      const body = JSON.parse(String(options?.body));
      const changed = Object.keys(body.parameters).filter(key => body.parameters[key] !== activeSession.parameters[key]);
      const stale = new Set(changed.flatMap(key => impacts[key] ?? []));
      return activeSession = { ...activeSession, revision: activeSession.revision+1, parameters: body.parameters,
        steps: Object.fromEntries(Object.entries(activeSession.steps).map(([id, value]) => [id, stale.has(id) ? { status: "stale", error: "" } : value])) };
    }
    if (url === `${BASE}/sessions/scope-session/steps`) {
      const body = JSON.parse(String(options?.body));
      if (body.step_ids.includes("A05") && !body.step_ids.includes("A04") && activeSession.steps.A04.status !== "completed")
        throw new Error("A05 缺少前置：A04 Scope 量程与触发配置");
      return activeSession = { ...activeSession, revision: activeSession.revision+1,
        steps: { ...activeSession.steps, ...Object.fromEntries(body.step_ids.map((id: string) => [id, { status: "completed", error: "" }])) } };
    }
    if (url === `${BASE}/sessions/scope-session/finish`) return activeSession = { ...activeSession, status: "closed", revision: activeSession.revision+1 };
    if (String(url).endsWith("/analysis") && options?.method === "POST") return { id: "analysis", status: "completed", events: [] } as never;
    if (String(url).endsWith("/analysis")) return { run_id: String(url).split("/").at(-2), revision: 0, options: {},
      steps: Object.fromEntries(analysisStages.map(stage => [stage.id, { status: "pending", error: "" }])), fingerprint: {}, summary: {} } as never;
    throw new Error(`未模拟 ${url}`);
  });
});
afterEach(cleanup);
const page = () => render(<MemoryRouter initialEntries={["/experiments/z-aw-closed-loop-waveform-correction"]}>
  <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);
async function openParameters(id: string) {
  const stage = acquisition.find(item => item.id === id)!;
  const region = await screen.findByLabelText(`${id} ${stage.title} 参数`);
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  region.querySelectorAll<HTMLDetailsElement>("details").forEach(item => {
    if (!item.open) fireEvent.click(item.querySelector("summary")!);
  });
  return within(region);
}

it("真实路由提供运行/历史、节点编辑保留、历史填回且预检失败不启动", async () => {
  page();
  const edit = await openParameters("A05");
  expect(screen.getAllByRole("tab")).toHaveLength(2);
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  fireEvent.change(edit.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await within(screen.getByRole("tabpanel", { name: "历史" })).findByAltText("波形对比")).toHaveAttribute("src", "/waveform.png");
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByText("静态标定信息")).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect((await openParameters("A05")).getByLabelText("运行标签")).toHaveValue("draft");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect((await openParameters("A05")).getByLabelText("运行标签")).toHaveValue("historical");
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("控制波形超出安全范围");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
  expect(within(screen.getByRole("region", { name: "交互实验流程图" })).getByRole("button", { name: "保存参数与布局" })).toBeVisible();
});

it("显式选择拒绝缺前置，选择全部后执行并能结束会话", async () => {
  page();
  const create = await screen.findByRole("button", { name: "创建分步会话" });
  await waitFor(() => expect(create).toBeEnabled());
  fireEvent.click(create);
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: "A05 闭环迭代与独立验证" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 \/ 重跑" }));
  await screen.findByText(/A05 缺少前置：A04 Scope/);
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 \/ 重跑" }));
  await waitFor(() => expect(activeSession.steps.A05.status).toBe("completed"));
  expect(activeSession.steps.A06.status).toBe("pending");
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await screen.findByText(/下方状态为已结束会话的执行记录/);
});

it("恢复会话后草稿预览与真实应用一致，失败保留草稿，还原清除预览", async () => {
  activeSession = makeSession(true); activeSession.parameters.CONTROL_SCALE = .8;
  page();
  const edit = await openParameters("A03");
  await waitFor(() => expect(edit.getByLabelText("控制幅度比例")).toHaveValue(.8));
  fireEvent.change(edit.getByLabelText("控制幅度比例"), { target: { value: ".6" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A03.*A04.*A05/)).toBeVisible();
  expect(activeSession.steps.A03.status).toBe("completed");
  fireEvent.change(edit.getByLabelText("控制幅度比例"), { target: { value: ".8" } });
  expect(screen.queryByText(/参数草稿尚未应用，请先应用/)).not.toBeInTheDocument();
  fireEvent.change(edit.getByLabelText("控制幅度比例"), { target: { value: ".6" } });
  rejectApply = true;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await screen.findByText(/模拟应用失败/);
  expect(edit.getByLabelText("控制幅度比例")).toHaveValue(.6);
  rejectApply = false;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(activeSession.steps.A03.status).toBe("stale"));
  expect(activeSession.steps.A02.status).toBe("completed");
  expect(mockApi.mock.calls.filter(([url]) => url === `${BASE}/sessions/scope-session/steps`)).toHaveLength(0);
});

it("历史分析只用目录状态，保存默认值仍保存下一次实验与布局", async () => {
  page();
  const edit = await openParameters("A03");
  fireEvent.change(edit.getByLabelText("控制幅度比例"), { target: { value: ".9" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "history-one" } });
  await screen.findByText(/正在编辑 history-one 的分析覆盖值/);
  expect(screen.getAllByLabelText("控制幅度比例")).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "history-two" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "" } });
  expect(edit.getByLabelText("控制幅度比例")).toHaveValue(.9);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`);
  expect(JSON.parse(String(saved?.[1]?.body)).parameters.CONTROL_SCALE).toBe(.9);
  expect(JSON.parse(String(saved?.[1]?.body)).parameter_layout).toEqual(layout);
});

it("真实 schema 的全部可见字段均有节点编辑入口，初始化条件只在配置节点", async () => {
  page();
  for (const stage of acquisition.filter(stage => stage.display_parameters.length)) await openParameters(stage.id);
  const displayed = acquisition.flatMap(stage => stage.display_parameters);
  expect(new Set(displayed)).toEqual(new Set(fields.map(field => field.name)));
  for (const field of fields) {
    expect(screen.getAllByLabelText(field.unit ? `${field.label} (${field.unit})` : field.label).length)
      .toBe(displayed.filter(name => name === field.name).length);
  }
  expect(screen.getAllByLabelText("控制幅度比例")).toHaveLength(1);
  expect(screen.getAllByLabelText("理论电压量程余量倍数")).toHaveLength(2);
});

it("共享过程参数的值和分类跨节点同步，保存完整布局后回读恢复", async () => {
  const rendered = page();
  const scope = await openParameters("A04");
  const loop = await openParameters("A05");
  fireEvent.change(scope.getByLabelText("理论电压量程余量倍数"), { target: { value: "1.8" } });
  expect(loop.getByLabelText("理论电压量程余量倍数")).toHaveValue(1.8);
  fireEvent.click(scope.getByRole("button", { name: "理论电压量程余量倍数 移至基础参数" }));
  expect(loop.getByRole("button", { name: "理论电压量程余量倍数 移至高级参数" })).toBeVisible();
  expect(loop.getByLabelText("理论电压量程余量倍数")).toHaveValue(1.8);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  const body = JSON.parse(String(saved[1]?.body));
  expect(body.parameters.SCOPE_HEADROOM_FACTOR).toBe(1.8);
  expect(new Set([...body.parameter_layout.basic, ...body.parameter_layout.advanced])).toEqual(new Set(fields.map(field => field.name)));
  rendered.unmount(); page();
  expect((await openParameters("A04")).getByRole("button", { name: "理论电压量程余量倍数 移至高级参数" })).toBeVisible();
});

it("运行中禁用参数分类移动，质量错误在恢复会话后可见", async () => {
  activeSession = makeSession(true);
  activeSession.status = "running";
  activeSession.current_step = "A05";
  activeSession.pending_request = "request";
  activeSession.steps.A05 = { status: "quality_failed", error: "最终重载未通过，保留原始数据" };
  page();
  const edit = await openParameters("A04");
  await screen.findByText("最终重载未通过，保留原始数据");
  expect(edit.getByRole("button", { name: "理论电压量程余量倍数 移至基础参数" })).toBeDisabled();
});

it("五个原高级参数保留模型分组，基础与高级移动不会丢失字段值", async () => {
  page();
  const source = await openParameters("A03");
  const advanced = fields.filter(field => field.group === "advanced").map(field => field.name);
  expect(advanced).toEqual(expect.arrayContaining(["CONTROL_SCALE", "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP", "SCOPE_CYCLES", "SCOPE_HEADROOM_FACTOR"]));
  fireEvent.change(source.getByLabelText("控制幅度比例"), { target: { value: ".8" } });
  fireEvent.click(source.getByRole("button", { name: "控制幅度比例 移至基础参数" }));
  expect(source.getByLabelText("控制幅度比例")).toHaveValue(.8);
  fireEvent.click(source.getByRole("button", { name: "控制幅度比例 移至高级参数" }));
  expect(source.getByLabelText("控制幅度比例")).toHaveValue(.8);
});

it("运行与历史支持键盘切换且保留下一次实验草稿", async () => {
  page();
  fireEvent.change((await openParameters("A05")).getByLabelText("运行标签"), { target: { value: "keyboard-draft" } });
  const run = screen.getByRole("tab", { name: "运行" });
  run.focus(); fireEvent.keyDown(run, { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: "历史" })).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(screen.getByRole("tab", { name: "历史" }), { key: "ArrowLeft" });
  expect((await openParameters("A05")).getByLabelText("运行标签")).toHaveValue("keyboard-draft");
});
