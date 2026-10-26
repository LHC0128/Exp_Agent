import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./MxZCurrentCouplingCalibrationPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));

const ID = "mx-z-current-coupling-calibration";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const fields = fixture.schema.fields;
const { acquisition, analysis, parameter_impacts } = fixture.flow;
const gates = Object.fromEntries([...acquisition, ...analysis].filter(stage => stage.quality_gates.length)
  .map(stage => [stage.id, stage.quality_gates]));
const layout = { basic: fields.map(field => field.name), advanced: [] };
const old = { run_id: "1014_old", timestamp: "1014", run_tag: "old", completion_status: "completed" };
const other = { ...old, run_id: "1014_other", run_tag: "other" };
let current: any;
let preflightOk: boolean;
let applyError: boolean;
let reportSuccess: boolean;
let summaryStatus: string;
let acquisitionJob: any;
let resultReports: Record<string, Record<string, unknown>>;
class TestEventSource {
  static instances: TestEventSource[] = [];
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(public url: string) { TestEventSource.instances.push(this); }
}
let storedLayout: typeof layout;
let storedValues: ReturnType<typeof parameters>;
const parameters = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const makeSession = () => ({ session_id: "mx-session", job_id: "mx-job", status: "waiting", revision: 0,
  parameters: parameters(), pending_request: null, outputs: { "Y RF": "OFF", 温控: "ON" },
  steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: "pending", error: "" }])) });

beforeEach(() => {
  current = null; preflightOk = true; applyError = false;
  reportSuccess = true; summaryStatus = "not_started"; acquisitionJob = undefined;
  resultReports = {};
  TestEventSource.instances = [];
  vi.stubGlobal("EventSource", TestEventSource);
  storedLayout = structuredClone(layout); storedValues = parameters();
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { id: ID, title: "Mx Z 实际电流-耦合强度标定", description: "逐 Z 先采 SDS 电阻电压，再做 Y RF/R 扫频；保留电流标定和磁滞诊断。", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields: fields.map(field => ({ ...field, default: storedValues[field.name] })),
      parameter_layout_saved: true, parameter_layout: storedLayout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === "/api/jobs/mx-job") return { id: "mx-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === "/api/jobs/analysis-job") return { id: "analysis-job", kind: `analysis:${ID}:${old.run_id}`, status: "completed", events: [] } as never;
    if (url === "/api/jobs/full-job") return acquisitionJob as never;
    if (url === "/api/jobs/full-job/cancel") return acquisitionJob = { ...acquisitionJob, status: "cancelled" };
    if (url === "/api/jobs/mx-job/cancel") return { id: "mx-job", kind: `session:${ID}`, status: "cancelled", events: [] } as never;
    if (url === `${BASE}/workflow`) return { acquisition, analysis,
      parameter_impacts, run_step_id: "A09", finish_step_id: "A11", diagram_url: `${BASE}/workflow/diagram` } as never;
    if (url === `${BASE}/sessions/active`) return current as never;
    if (url === `${BASE}/sessions` && options?.method === "POST") return current = makeSession();
    if (url === `${BASE}/sessions/mx-session/steps`) {
      if (body.step_ids.includes("A09") && current.steps.A08.status !== "completed" && !body.step_ids.includes("A08")) throw new Error("缺少前置：A08 SDS CH3 固定采样率与自动量程准备");
      if (body.step_ids.includes("A10") && current.steps.A09.status === "quality_failed" && !body.step_ids.includes("A09"))
        throw new Error("A10 缺少前置模块：A09 质量未通过");
      const states = Object.fromEntries(acquisition.map(stage => [stage.id,
        { status: body.step_ids.includes(stage.id) ? "completed" : current.steps[stage.id].status, error: "" }]));
      if (body.step_ids.includes("A10")) for (const id of ["A07", "A08", "A09"]) states[id].status = "stale";
      return current = { ...current, revision: current.revision + 1,
        steps: states };
    }
    if (url === `${BASE}/sessions/mx-session/parameters`) {
      if (applyError) throw new Error("参数预检失败：扫频下限必须大于 0 Hz");
      return current = { ...current, parameters: body.parameters, revision: current.revision + 1,
        steps: Object.fromEntries(acquisition.map(stage => [stage.id,
          { status: ["A07", "A08", "A09", "A10"].includes(stage.id) && current.steps[stage.id].status !== "pending" ? "stale" : current.steps[stage.id].status, error: "" }])) };
    }
    if (url === `${BASE}/sessions/mx-session/finish`) return current = { ...current, revision: current.revision + 1, status: "closed",
      steps: { ...current.steps, A11: { status: "completed", error: "" } } };
    if (url === `${BASE}/sessions/mx-session`) return current as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old, other], total: 2 } as never;
    if (url === `${BASE}/runs` && options?.method === "POST") return acquisitionJob = {
      id: "full-job", kind: `experiment:${ID}`, status: "running", events: [], message: "实际电流联合扫描", percent: 0 };
    const historicalRun = [old, other].find(run => url === `${BASE}/runs/${run.run_id}/summary`);
    if (historicalRun) return { ...historicalRun, analysis_status: summaryStatus, analysis_error: "",
      parameters: { ...parameters(), RUN_TAG: "historical", Z_BIAS_STEP_V: 0.25 }, artifacts: Object.fromEntries(
        ["current_coupling_calibration.png", "frequency_response_fits.png", "analysis.json"].map(
          name => [`results/${name}`, `${BASE}/runs/${historicalRun.run_id}/files/results/${name}`])) } as never;
    if (url === `${BASE}/runs/${old.run_id}/analysis` && options?.method === "POST") return { id: "analysis-job", kind: `analysis:${ID}:${old.run_id}`, status: "running", events: [] } as never;
    if ([old, other].some(run => url === `${BASE}/runs/${run.run_id}/analysis`)) return { run_id: String(url).includes(other.run_id) ? other.run_id : old.run_id, revision: 0,
      options: { LINEAR_R_SQUARED_MIN: String(url).includes(other.run_id) ? 0.97 : 0.98 }, steps: Object.fromEntries(analysis.map(stage => [stage.id, { status: "completed", error: "" }])), summary: {}, fingerprint: {} } as never;
    const resultRun = [old, other].find(run => url === `${BASE}/runs/${run.run_id}/result`);
    if (resultRun) return resultReports[resultRun.run_id] ?? {
      success: reportSuccess, K_Z_Hz_per_A: reportSuccess ? 100000 : null, K_Z_Hz_per_Vsense: 10000, f_0_at_zero_current_hz: 89900,
      accepted_curve_count: 4, total_curve_count: 5, rejection_reasons: reportSuccess ? [] : ["电流线性 R2=0.9 < 0.99"], linearity: { r_squared: 1 },
      hysteresis: { available: true }, repeatability: { current_std_a_mean: 0 }, operating_point: {},
      curves: [{ fit: { success: false, rejection_reasons: ["曲线中心位于扫描边界"] } }],
    } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["预测扫频下限必须大于 0 Hz"] } as never;
    if (url === `${BASE}/derive`) return { values: { frequency_point_count: 9 } } as never;
    if (url === `${BASE}/defaults`) {
      storedLayout = structuredClone(body.parameter_layout); storedValues = { ...body.parameters };
      return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: storedLayout } } as never;
    }
    throw new Error(`未模拟接口 ${url}`);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const page = () => render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}>
  <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
</MemoryRouter>);
const editor = async (id: string) => {
  const stage = [...acquisition, ...analysis].find(stage => stage.id === id)!;
  const region = await screen.findByRole("region", { name: `${id} ${stage.title} 参数`, hidden: true });
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  return within(region);
};

it("真实路由进入两列流程，缺前置拒绝，选择执行和结束使用本实验会话", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: "A09 逐 Z 电流与局部 Y RF 联合扫描" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: 缺少前置：A08/);
  expect(screen.getByRole("checkbox", { name: "A07 Y RF 正弦与 R 解调准备" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(current.revision).toBe(1));
  expect(screen.getByRole("checkbox", { name: "A11 安全结束会话" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current.status).toBe("closed"));
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
});

it("共享过程参数草稿同步、还原清除预览、应用后显式重跑，恢复存活会话", async () => {
  current = makeSession();
  for (const stage of acquisition.slice(0, 9)) current.steps[stage.id].status = "completed";
  page();
  const sweep = await editor("A07");
  const amplitude = await editor("A09");
  fireEvent.change(sweep.getByLabelText("Z 零偏预测中心 (Hz)"), { target: { value: "90500" } });
  expect(amplitude.getByLabelText("Z 零偏预测中心 (Hz)")).toHaveValue(90500);
  expect(screen.getByText(/预计需重跑的已完成步骤：A07/)).toBeVisible();
  fireEvent.change(amplitude.getByLabelText("Z 零偏预测中心 (Hz)"), { target: { value: "90000" } });
  expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument();
  fireEvent.change(amplitude.getByLabelText("Z 零偏预测中心 (Hz)"), { target: { value: "91000" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(current.parameters.ZERO_BIAS_CENTER_FREQUENCY_HZ).toBe(91000));
  expect(mockApi.mock.calls.filter(([url]) => String(url).endsWith("/steps"))).toHaveLength(0);
});

it("Tab保留参数、历史快照填回不启动，预检失败阻止完整运行", async () => {
  page();
  const tag = await editor("A02");
  fireEvent.change(tag.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "填回参数" });
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByText("historical")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(tag.getByLabelText("运行标签")).toHaveValue("draft");
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("预测扫频下限必须大于 0 Hz");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(tag.getByLabelText("运行标签")).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("历史分析草稿与下一次参数隔离，保存默认值不包含历史覆盖", async () => {
  page();
  const next = await editor("B03");
  fireEvent.change(next.getByLabelText("线性标定最低 R²"), { target: { value: "0.9" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.98));
  fireEvent.change(screen.getByLabelText("线性标定最低 R²"), { target: { value: "0.95" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: other.run_id } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.97));
  fireEvent.change(screen.getByLabelText("线性标定最低 R²"), { target: { value: "0.96" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "" } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.9));
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.95));
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: other.run_id } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.96));
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.95));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.LINEAR_R_SQUARED_MIN).toBe(0.9);
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs/${old.run_id}/analysis`
    && options?.method === "POST" && JSON.parse(String(options.body)).analysis_parameters.LINEAR_R_SQUARED_MIN === 0.95)).toBe(true));
});

it("应用失败保留共享草稿和执行记录，修正后只应用不执行", async () => {
  current = makeSession();
  for (const stage of acquisition.slice(0, 8)) current.steps[stage.id].status = "completed";
  const before = structuredClone(current);
  applyError = true;
  page();
  const first = await editor("A07");
  const second = await editor("A09");
  fireEvent.change(first.getByLabelText("Z 零偏预测中心 (Hz)"), { target: { value: "0" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await screen.findByText(/参数预检失败：扫频下限/);
  expect(current).toEqual(before);
  expect(second.getByLabelText("Z 零偏预测中心 (Hz)")).toHaveValue(0);
  expect(screen.getByText(/参数草稿尚未应用/)).toBeVisible();
  applyError = false;
  fireEvent.change(second.getByLabelText("Z 零偏预测中心 (Hz)"), { target: { value: "90500" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(current.parameters.ZERO_BIAS_CENTER_FREQUENCY_HZ).toBe(90500));
  expect(current.steps.A07.status).toBe("stale");
  expect(current.steps.A06.status).toBe("completed");
  expect(mockApi.mock.calls.some(([url]) => String(url).endsWith("/steps"))).toBe(false);
});

it("共享分类移动同步全部节点，保存后重新进入回读分类和值", async () => {
  page();
  const first = await editor("A07");
  const second = await editor("A09");
  fireEvent.change(first.getByLabelText("Z 零偏预测中心 (Hz)"), { target: { value: "90500" } });
  fireEvent.click(first.getByRole("button", { name: "Z 零偏预测中心 移至高级参数" }));
  for (const node of [first, second]) {
    fireEvent.click(node.getByText("高级参数（1）"));
    expect(node.getByRole("button", { name: "Z 零偏预测中心 移至基础参数" })).toBeVisible();
    expect(node.getByLabelText("Z 零偏预测中心 (Hz)")).toHaveValue(90500);
  }
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(storedLayout.advanced).toEqual(["ZERO_BIAS_CENTER_FREQUENCY_HZ"]);
  expect(storedLayout.basic).not.toContain("ZERO_BIAS_CENTER_FREQUENCY_HZ");
  cleanup();
  page();
  const reloaded = await editor("A09");
  fireEvent.click(reloaded.getByText("高级参数（1）"));
  expect(reloaded.getByRole("button", { name: "Z 零偏预测中心 移至基础参数" })).toBeVisible();
  expect(reloaded.getByLabelText("Z 零偏预测中心 (Hz)")).toHaveValue(90500);
});

it("质量门槛和实际失败记录位于对应节点，失败后允许安全结束", async () => {
  current = makeSession();
  for (const stage of acquisition.slice(0, 8)) current.steps[stage.id].status = "completed";
  current.run_id = "1014_failed";
  current.steps.A09 = { status: "quality_failed", error: "frequency_0000 连续 3 次 std(R) > 0.01 V" };
  page();
  for (const [id, values] of Object.entries(gates)) {
    const stage = [...acquisition, ...analysis].find(item => item.id === id)!;
    const checkbox = await screen.findByRole("checkbox", { name: `${id} ${stage.title}` });
    expect(within(checkbox.closest("li")!).getByText(`检查与质量说明：${values[0]}`)).toBeVisible();
  }
  const failed = screen.getByRole("checkbox", { name: "A09 逐 Z 电流与局部 Y RF 联合扫描" }).closest("li")!;
  expect(within(failed).getByText("质量未通过")).toBeVisible();
  expect(within(failed).getByText(/连续 3 次 std/)).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox", { name: "A10 本轮安全收尾与封存" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: A10 缺少前置模块：A09 质量未通过/);
  expect(screen.getByRole("button", { name: "结束会话" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current.status).toBe("closed"));
});

it("使用真实摘要的 results 路径保留两张科学图与分析下载", async () => {
  page();
  expect(await screen.findByRole("img", { name: "实际电流与共振中心标定" })).toHaveAttribute("src",
    `${BASE}/runs/${old.run_id}/files/results/current_coupling_calibration.png`);
  expect(screen.getByRole("img", { name: "各 Z 工作点的 R 频扫拟合" })).toBeVisible();
  expect(screen.getByRole("link", { name: "results/analysis.json" })).toHaveAttribute("href",
    `${BASE}/runs/${old.run_id}/files/results/analysis.json`);
});

it("只读旧诊断保留质量失败、不可用数值和原图下载，切换历史不串用报告", async () => {
  summaryStatus = "completed";
  resultReports[old.run_id] = JSON.parse('{"success":false,"K_Z_Hz_per_A":null,"K_Z_Hz_per_Vsense":null,"f_0_at_zero_current_hz":null,"accepted_curve_count":3,"total_curve_count":5,"rejection_reasons":["历史频扫质量未通过"],"linearity":{"r_squared":null},"hysteresis":{"maximum_hz":null},"repeatability":{"std":null},"operating_point":{"overflow":null},"curves":[{"fit":{"parameters":[null,null]}}]}');
  resultReports[other.run_id] = { success: false, rejection_reasons: null, curves: null, total_curve_count: null };
  page();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const history = within(screen.getByRole("tabpanel", { name: "历史" }));
  expect(await history.findByText("历史频扫质量未通过")).toBeVisible();
  expect(history.getByText("标定质量未通过；诊断已保留，不能作为有效标定使用")).toBeVisible();
  expect(history.getAllByText("不可用")).toHaveLength(3);
  expect(history.getByText("3 / 5")).toBeVisible();
  fireEvent.click(history.getByText("线性度、磁滞、重复性与逐曲线诊断"));
  expect(history.getByText(/"maximum_hz": null/)).toBeVisible();
  for (const title of ["实际电流与共振中心标定", "各 Z 工作点的 R 频扫拟合"])
    expect(history.getByRole("img", { name: title })).toBeVisible();
  expect(history.getByRole("link", { name: "results/analysis.json" })).toHaveAttribute("href",
    `${BASE}/runs/${old.run_id}/files/results/analysis.json`);
  fireEvent.click(history.getByRole("button", { name: new RegExp(other.run_id) }));
  expect(await history.findByText("不可用 / 不可用")).toBeVisible();
  expect(history.getByText("标定拒绝原因：不可用")).toBeVisible();
  expect(history.queryByText("历史频扫质量未通过")).not.toBeInTheDocument();
  expect(history.getAllByText("不可用")).toHaveLength(3);
  fireEvent.click(history.getByText("线性度、磁滞、重复性与逐曲线诊断"));
  expect(history.getByText(/"linearity": "不可用"/)).toBeVisible();
  expect(history.getByRole("img", { name: "实际电流与共振中心标定" })).toHaveAttribute("src",
    `${BASE}/runs/${other.run_id}/files/results/current_coupling_calibration.png`);
  fireEvent.click(history.getByRole("button", { name: new RegExp(old.run_id) }));
  expect(await history.findByText("历史频扫质量未通过")).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(within(screen.getByRole("tabpanel", { name: "运行" })).getByText("历史频扫质量未通过")).toBeVisible();
  expect(mockApi.mock.calls.some(([url]) => String(url).includes("/files/results/analysis.json"))).toBe(false);
  expect(mockApi.mock.calls.some(([url, options]) => options?.method === "POST" && String(url).includes("/analy"))).toBe(false);
});

it("旧结果缺少科学质量和可选诊断时明确显示不可用", async () => {
  resultReports[old.run_id] = {};
  page();
  const run = within(screen.getByRole("tabpanel", { name: "运行" }));
  expect(await run.findByText("标定质量不可用")).toBeVisible();
  expect(run.getByText("不可用 / 不可用")).toBeVisible();
  expect(run.getByText("标定拒绝原因：不可用")).toBeVisible();
  expect(run.getAllByText("不可用")).toHaveLength(3);
  expect(run.queryByText("标定质量通过")).not.toBeInTheDocument();
  expect(run.queryByText(/标定诊断加载失败/)).not.toBeInTheDocument();
});

it("原模型全部可见参数都有真实节点入口，初始化条件只出现在设置节点", async () => {
  page();
  await screen.findByRole("checkbox", { name: "A11 安全结束会话" });
  const shown = new Set([...acquisition, ...analysis].flatMap(stage => stage.display_parameters));
  expect([...shown].sort()).toEqual(fields.map(field => field.name).sort());
  expect(acquisition.filter(stage => stage.display_parameters.includes("FIXED_PARAMS.temperature")).map(stage => stage.id)).toEqual(["A05"]);
  for (const stage of [...acquisition, ...analysis].filter(stage => stage.display_parameters.length)) {
    const region = await editor(stage.id);
    for (const key of stage.display_parameters) {
      const field = fields.find(item => item.name === key)!;
      expect(region.getByText(field.unit ? `${field.label} (${field.unit})` : field.label)).toBeInTheDocument();
    }
  }
});

it("47个可见字段均可实际编辑还原，共享入口同步且不执行硬件", async () => {
  page();
  expect(fields).toHaveLength(47);
  for (const field of fields) {
    const nodes = [...acquisition, ...analysis].filter(stage => stage.display_parameters.includes(field.name));
    const label = field.unit ? `${field.label} (${field.unit})` : field.label;
    const node = await editor(nodes[0].id);
    const input = node.getByLabelText(label);
    expect(input).toBeEnabled();
    let changed: string | number | boolean;
    if ("options" in field && field.options?.length) {
      changed = field.options.find(option => option.value !== field.default && option.value !== "unselected")!.value;
    } else if (field.type === "boolean") changed = !field.default;
    else if (field.type === "string") changed = "acceptance_draft";
    else {
      const value = Number(field.default);
      const step = field.type === "integer" ? 1 : Math.max(0.001, Math.abs(value) * 0.01);
      changed = field.maximum != null && value + step > field.maximum ? value - step : value + step;
    }
    fireEvent.change(input, { target: { value: String(changed) } });
    const displayed = input.tagName === "SELECT" || typeof changed === "boolean" ? String(changed) : changed;
    for (const shared of nodes) expect((await editor(shared.id)).getByLabelText(label)).toHaveValue(displayed);
    fireEvent.change(input, { target: { value: String(field.default) } });
    const restored = input.tagName === "SELECT" || typeof field.default === "boolean" ? String(field.default) : field.default;
    expect(input).toHaveValue(restored);
  }
  expect(mockApi.mock.calls.some(([url, options]) => options?.method === "POST" &&
    (String(url).endsWith("/steps") || String(url).endsWith("/runs") || String(url).includes("/analysis")))).toBe(false);
}, 20000);

it("旧目录结果迟到时不会覆盖当前历史对象的科学结论", async () => {
  const original = mockApi.getMockImplementation()!;
  const resolveOld: Array<(report: unknown) => void> = [];
  mockApi.mockImplementation((url, options) => url === `${BASE}/runs/${old.run_id}/result`
    ? new Promise(resolve => resolveOld.push(resolve)) as never : original(url, options));
  resultReports[other.run_id] = { success: false, rejection_reasons: ["当前对象诊断"], K_Z_Hz_per_A: -2500 };
  page();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const history = within(screen.getByRole("tabpanel", { name: "历史" }));
  await waitFor(() => expect(resolveOld.length).toBeGreaterThan(0));
  fireEvent.click(await history.findByRole("button", { name: new RegExp(other.run_id) }));
  expect(await history.findByText("当前对象诊断")).toBeVisible();
  await act(async () => {
    for (const resolve of resolveOld) resolve({ success: true, rejection_reasons: ["迟到旧对象"], K_Z_Hz_per_A: 9999 });
  });
  expect(history.getByText("当前对象诊断")).toBeVisible();
  expect(history.queryByText("迟到旧对象")).not.toBeInTheDocument();
  expect(history.queryByText("标定质量通过")).not.toBeInTheDocument();
});

it("已完成程序仍按科学success呈现质量失败，保留系数和磁滞逐曲线诊断", async () => {
  reportSuccess = false; summaryStatus = "completed";
  page();
  const run = within(screen.getByRole("tabpanel", { name: "运行" }));
  expect(await run.findByText("标定质量未通过；诊断已保留，不能作为有效标定使用")).toBeVisible();
  expect(run.getByText("电流线性 R2=0.9 < 0.99")).toBeVisible();
  expect(run.getByText("Z 电流耦合系数 (Hz/A)")).toBeVisible();
  expect(run.getByText("不可用")).toBeVisible();
  fireEvent.click(run.getByText("线性度、磁滞、重复性与逐曲线诊断"));
  expect(run.getByText(/current_std_a_mean/)).toBeVisible();
  expect(run.getByText(/曲线中心位于扫描边界/)).toBeVisible();
  expect(run.getByRole("img", { name: "实际电流与共振中心标定" })).toBeVisible();
  expect(run.getByRole("img", { name: "各 Z 工作点的 R 频扫拟合" })).toBeVisible();
});

it("流程顶部保存预览预检完整运行与真实JobView安全停止、SSE订阅保持可用", async () => {
  page();
  const save = await screen.findByRole("button", { name: "保存参数与布局" });
  const container = save.closest(".xy-workflow")!;
  expect(container).toContainElement(screen.getByRole("button", { name: "仅预检" }));
  expect(container).toContainElement(screen.getByRole("button", { name: "预检并运行实验" }));
  const preview = await screen.findByText("派生预览（只读）");
  fireEvent.click(preview);
  expect(within(container as HTMLElement).getByText("frequency_point_count")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "仅预检" }));
  await screen.findByText("参数预检通过");
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/runs`)).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await waitFor(() => expect(TestEventSource.instances.find(item => item.url === "/api/jobs/full-job/events")?.onmessage).toBeTypeOf("function"));
  const source = TestEventSource.instances.find(item => item.url === "/api/jobs/full-job/events")!;
  act(() => source.onmessage!({ data: JSON.stringify({ index: 1, timestamp: "2026-10-14T12:00:00", stage: "A09", message: "SDS 与 R 联合采集", data: {} }) }));
  expect(within(container as HTMLElement).getByText("SDS 与 R 联合采集", { selector: "strong" })).toBeVisible();
  const stop = screen.getByRole("button", { name: "安全停止" });
  expect(container).toContainElement(stop);
  fireEvent.click(stop);
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === "/api/jobs/full-job/cancel" && options?.method === "POST")).toBe(true));
});
