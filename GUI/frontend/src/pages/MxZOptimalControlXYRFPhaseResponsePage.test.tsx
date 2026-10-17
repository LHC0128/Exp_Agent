import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./MxZOptimalControlXYRFPhaseResponsePage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));

const ID = "mx-z-optimal-control-xy-rf-phase-response";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const { fields } = fixture.schema;
const { acquisition, analysis } = fixture.workflow;
const parameters = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const old = { run_id: "0730_history", timestamp: "0730", run_tag: "historical", completion_status: "completed" };
const other = { ...old, run_id: "0731_history", run_tag: "other" };
let current: any;
let preflightOk: boolean;
const makeSession = () => ({ session_id: "mxz-session", job_id: "mxz-job", revision: 0, status: "waiting",
  parameters: parameters(), pending_request: null, outputs: { Z: "最优控制ON", 共同触发: "保持当前状态" },
  steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: "pending", error: "" }])) });

beforeEach(() => {
  current = null; preflightOk = true;
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { id: ID, title: "Mx Z 最优控制 XY RF 相位响应", description: "非线性色散相位响应；不选最佳 XY", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return fixture.schema as never;
    if (url === `${BASE}/workflow`) return fixture.workflow as never;
    if (url === "/api/jobs") return [] as never;
    if (url === "/api/jobs/mxz-job") return { id: "mxz-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === `${BASE}/sessions/active`) return current as never;
    if (url === `${BASE}/sessions` && options?.method === "POST") return current = makeSession();
    if (url === `${BASE}/sessions/mxz-session/steps`) {
      if (body.step_ids.includes("A04") && !body.step_ids.includes("A03")) throw new Error("缺少前置：A03 控制工作点");
      return current = { ...current, revision: current.revision + 1,
        steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: body.step_ids.includes(stage.id) ? "completed" : "pending", error: "" }])) };
    }
    if (url === `${BASE}/sessions/mxz-session/parameters`) return current = { ...current, parameters: body.parameters,
      revision: current.revision + 1, steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: "stale", error: "" }])) };
    if (url === `${BASE}/sessions/mxz-session/finish`) return current = { ...current, revision: current.revision + 1, status: "closed",
      steps: { ...current.steps, A12: { status: "completed", error: "" } } };
    if (url === `${BASE}/sessions/mxz-session`) return current as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old, other], total: 2 } as never;
    for (const run of [old, other]) {
      if (url === `${BASE}/runs/${run.run_id}/summary`) return { ...run, analysis_status: "not_started", analysis_error: "",
        parameters: { ...parameters(), RUN_TAG: run.run_tag }, artifacts: {
          "results/analysis.yaml": "/old.yaml", "results/xy_rf_phase_response_nonlinear_maps.png": "/phase.png" } } as never;
      if (url === `${BASE}/runs/${run.run_id}/result`) return { nonlinear_fit_accepted_count: 1, nonlinear_parameter_maps: { residual_phase_deg: [[30.]] } } as never;
      if (url === `${BASE}/runs/${run.run_id}/analysis` && options?.method === "POST") return { id: "analysis-job", status: "running", events: [] } as never;
      if (url === `${BASE}/runs/${run.run_id}/analysis`) return { run_id: run.run_id, revision: 0,
        options: { PHASE_FIT_R_SQUARED_MIN: .1, PHASE_FIT_AMPLITUDE_SIGMA_MIN: 3. }, summary: {}, fingerprint: {},
        steps: Object.fromEntries(analysis.map(stage => [stage.id, { status: "pending", error: "" }])) } as never;
    }
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["冻结来源无效"] } as never;
    if (url === `${BASE}/derive`) return { values: {} } as never;
    if (url === `${BASE}/defaults`) return { message: "已保存", schema: { ...fixture.schema,
      parameter_layout_saved: true, parameter_layout: body.parameter_layout } } as never;
    throw new Error(`未模拟接口 ${url}`);
  });
});
afterEach(cleanup);

const page = () => render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}>
  <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
</MemoryRouter>);
const editor = async (id: string) => {
  const stage = [...acquisition, ...analysis].find(item => item.id === id)!;
  const region = await screen.findByRole("region", { name: `${id} ${stage.title} 参数`, hidden: true });
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  return within(region);
};
const amplitudeLabel = "RF 相位扫描幅度 (Vpp)";
const rSquaredLabel = "相位拟合最低 R²";

it("真实路由提供双 Tab、全部参数、质量门槛、顶部操作和本项可编辑原图", async () => {
  page();
  await screen.findByRole("checkbox", { name: /A04/ });
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "历史" })).toBeVisible();
  for (const stage of [...acquisition, ...analysis]) for (const gate of stage.quality_gates) expect(screen.getByText(`质量门槛：${gate}`)).toBeVisible();
  const owned = new Set([...acquisition, ...analysis].flatMap(stage => stage.display_parameters));
  expect(fields.every(field => owned.has(field.name))).toBe(true);
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  expect(screen.getByRole("button", { name: "保存参数与布局" }).closest(".xy-workflow-controls")).not.toBeNull();
});

it("依赖拒绝后可明确选择执行，结束不选择后续实验", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: /A04/ }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: 缺少前置：A03/);
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(current.revision).toBe(1));
  expect(screen.getByRole("checkbox", { name: /A12/ })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current.status).toBe("closed"));
});

it("恢复质量失败会话，显示拒绝原因并保留后继未运行状态", async () => {
  current = makeSession();
  for (const key of ["A01", "A02", "A03"]) current.steps[key].status = "completed";
  current.steps.A04 = { status: "quality_failed", error: "R 标准差拒绝：重试用尽" };
  page();
  await screen.findByText(/R 标准差拒绝：重试用尽/);
  const finish = screen.getByRole("checkbox", { name: /A05/ }).closest(".xy-flow-node")!;
  expect(within(finish as HTMLElement).getByText("未运行")).toBeVisible();
});

it("共享 RF 幅度同步、草稿还原清预览、应用后不自动运行并恢复会话", async () => {
  current = makeSession();
  for (const key of ["A01", "A02", "A03", "A04", "A05"]) current.steps[key].status = "completed";
  page();
  const workpoint = await editor("A03");
  const scan = await editor("A04");
  const initial = current.parameters.PHASE_CAL_RF_AMPLITUDE_VPP;
  fireEvent.change(workpoint.getByLabelText(amplitudeLabel), { target: { value: "0.02" } });
  expect(scan.getByLabelText(amplitudeLabel)).toHaveValue(.02);
  expect(screen.getByText(/预计需重跑的已完成步骤：A03/)).toBeVisible();
  fireEvent.change(scan.getByLabelText(amplitudeLabel), { target: { value: String(initial) } });
  expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument();
  fireEvent.change(scan.getByLabelText(amplitudeLabel), { target: { value: "0.03" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(current.parameters.PHASE_CAL_RF_AMPLITUDE_VPP).toBe(.03));
  expect(mockApi.mock.calls.some(([url]) => String(url).endsWith("/steps"))).toBe(false);
});

it("Tab 保留草稿，历史展示参数图和诊断，填回不自动保存或运行，预检失败阻断", async () => {
  page();
  const tag = await editor("A02");
  fireEvent.change(tag.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findAllByRole("button", { name: "填回参数" });
  const history = within(screen.getByRole("tabpanel", { name: "历史" }));
  expect(history.getByRole("img", { name: "results/xy_rf_phase_response_nonlinear_maps.png" })).toHaveAttribute("src", "/phase.png");
  await history.findByText(/nonlinear_fit_accepted_count/);
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(tag.getByLabelText("运行标签")).toHaveValue("draft");
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("冻结来源无效");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getAllByRole("button", { name: "填回参数" })[0]);
  expect(tag.getByLabelText("运行标签")).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("下一次实验分析门槛共享，两个历史目录覆盖草稿隔离，保存默认不混入历史覆盖", async () => {
  page();
  const scan = await editor("A04");
  const nextAnalysis = await editor("B02");
  fireEvent.change(scan.getByLabelText(rSquaredLabel), { target: { value: "0.7" } });
  expect(nextAnalysis.getByLabelText(rSquaredLabel)).toHaveValue(.7);
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await screen.findByText(/正在编辑 .* 的分析覆盖值/);
  const historical = await editor("B02");
  fireEvent.change(historical.getByLabelText(rSquaredLabel), { target: { value: "0.9" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: other.run_id } });
  const second = await editor("B02");
  await waitFor(() => expect(second.getByLabelText(rSquaredLabel)).toHaveValue(.1));
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  const restored = await editor("B02");
  await waitFor(() => expect(restored.getByLabelText(rSquaredLabel)).toHaveValue(.9));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(true));
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.PHASE_FIT_R_SQUARED_MIN).toBe(.7);
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url.endsWith(`/${old.run_id}/analysis`)
    && options?.method === "POST" && JSON.parse(String(options.body)).analysis_parameters.PHASE_FIT_R_SQUARED_MIN === .9)).toBe(true));
});

it("共享幅度分类同步且不改数值，保存布局无重复", async () => {
  page();
  const workpoint = await editor("A03");
  const scan = await editor("A04");
  const before = workpoint.getByLabelText(amplitudeLabel).getAttribute("value");
  fireEvent.click(workpoint.getByRole("button", { name: /RF 相位扫描幅度 移至高级参数/, hidden: true }));
  expect(scan.getByRole("button", { name: /RF 相位扫描幅度 移至基础参数/, hidden: true })).toBeInTheDocument();
  expect(workpoint.getByLabelText(amplitudeLabel)).toHaveAttribute("value", before);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(true));
  const body = JSON.parse(String(mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)![1]?.body));
  expect(body.parameter_layout.advanced).toContain("PHASE_CAL_RF_AMPLITUDE_VPP");
  expect(body.parameter_layout.basic).not.toContain("PHASE_CAL_RF_AMPLITUDE_VPP");
  expect(new Set([...body.parameter_layout.basic, ...body.parameter_layout.advanced]).size).toBe(fields.length);
});
