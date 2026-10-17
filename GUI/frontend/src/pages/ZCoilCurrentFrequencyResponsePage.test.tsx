import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));
const mockApi = vi.mocked(api);
const BASE = "/api/experiments/z-coil-current-frequency-response";
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "next", group: "basic", unit: "" },
  { name: "DRIVE_AMPLITUDE_VPP", label: "低幅度正弦驱动", type: "number", default: 1, group: "basic", unit: "" },
  { name: "DRIVE_OFFSET_V", label: "Z 线圈正弦偏置", type: "number", default: 0, group: "advanced", unit: "" },
  { name: "SCOPE_CYCLES", label: "每次采集周期数", type: "integer", default: 3, group: "basic", unit: "" },
  { name: "SCOPE_REPEATS", label: "每个频率重复次数", type: "integer", default: 2, group: "basic", unit: "" },
];
const layout = { basic: fields.filter(f => f.group === "basic").map(f => f.name), advanced: ["DRIVE_OFFSET_V"] };
const params = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const acquisition = [
  ["A01", "参数与低端接线预检", "启动准备", []], ["A02", "设备连接", "启动准备", []],
  ["A03", "参考时钟同步与回读", "启动准备", []],
  ["A04", "相干正弦与共同触发配置", "激励与采集", ["DRIVE_AMPLITUDE_VPP", "DRIVE_OFFSET_V"]],
  ["A05", "动态 Scope 首频配置与回读", "激励与采集", ["SCOPE_CYCLES"]],
  ["A06", "对数扫频、电流校验与重复帧保存", "激励与采集", ["RUN_TAG", "SCOPE_REPEATS", "SCOPE_CYCLES"]],
  ["A07", "安全收尾 / 结束会话", "结束", []],
].map(([id, title, group, parameters], index, all) => ({ id: String(id), title: String(title), group: String(group),
  dependencies: index && id !== "A07" ? [String(all[index-1][0])] : [],
  parameters: parameters as string[], display_parameters: parameters as string[], artifacts: [] }));
const analysisStages = ["配置、原始帧与触发资格", "电流相量、参考与可靠频带", "电流频响诊断图与报告"]
  .map((title, index) => ({ id: `B0${index+1}`, title, group: "独立离线分析",
    dependencies: index ? [`B0${index}`] : [], parameters: [], display_parameters: [], artifacts: [] }));
const impacts: Record<string, string[]> = { RUN_TAG: ["A06"], SCOPE_REPEATS: ["A06"],
  SCOPE_CYCLES: ["A05", "A06"], DRIVE_AMPLITUDE_VPP: ["A04", "A05", "A06"], DRIVE_OFFSET_V: ["A04", "A05", "A06"] };
const flow = { acquisition, analysis: analysisStages,
  parameter_impacts: { acquisition: impacts, analysis: {} }, diagram_url: `${BASE}/workflow/diagram` };
const makeSession = (completed = false) => ({ session_id: "frequency-session", job_id: "frequency-job", revision: 0,
  status: "waiting", parameters: params(), current_step: null, pending_request: null,
  steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: completed && stage.id !== "A07" ? "completed" : "pending", error: "" }])),
  outputs: { "Z 正弦": "Burst ON", "共同触发": "ON", Scope: "STOP" } });
let activeSession: any;
let preflightOk: boolean;
let rejectApply: boolean;
let omitArtifacts: boolean;
let legacyReport: boolean;
let reportOverride: Record<string, unknown> | undefined;
let savedLayout: typeof layout;
let savedValues: ReturnType<typeof params>;

beforeEach(() => {
  activeSession = null; preflightOk = true; rejectApply = false; omitArtifacts = false; legacyReport = false; reportOverride = undefined;
  savedLayout = { basic: [...layout.basic], advanced: [...layout.advanced] }; savedValues = params();
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    if (url === BASE) return { title: "Z 线圈实际电流频率响应", description: "SDS CH3 低端采样电阻电压与 CH4 触发", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields: fields.map(field => ({ ...field, default: savedValues[field.name] })),
      parameter_layout_saved: true, parameter_layout: savedLayout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === `${BASE}/workflow`) return flow as never;
    if (url === `${BASE}/sessions/active`) return activeSession as never;
    if (url === "/api/jobs/frequency-job") return { id: "frequency-job", status: activeSession?.status === "closed" ? "completed" : "running", events: [] } as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [
      { run_id: "history-one", run_tag: "old", timestamp: "1014", completion_status: "completed" },
      { run_id: "history-two", run_tag: "old2", timestamp: "1013", completion_status: "failed" }], total: 2 } as never;
    if (String(url).endsWith("/summary")) {
      const runId = String(url).split("/").at(-2)!;
      return { run_id: runId, run_tag: "old", timestamp: "1014", completion_status: "completed",
        analysis_status: "completed", analysis_error: "", parameters: { ...params(), RUN_TAG: runId === "history-two" ? "historical-two" : "historical", DRIVE_AMPLITUDE_VPP: .7 },
        ...(omitArtifacts ? {} : { artifacts: { "results/transfer_function.png": "/frequency.png", "results/frequency_response.json": "/frequency-report.json" } }) } as never;
    }
    if (url === "/frequency-report.json") return legacyReport ? {} as never : { capture_count: 8, frequency_count: 4,
      phase_reference: "commanded_amplitude", sense_resistor_ohm: 10, reliable_point_count: 4,
      valid_frame_count_rule: "exact_frequency_v1",
      amplitude_reliable_point_count: 4, phase_reliable_point_count: 0, closed_loop_usable: false,
      reliable_band_hz: { minimum: 1000, maximum: 4000 }, excluded_frequency_hz: [], ...reportOverride } as never;
    if (url === `${BASE}/derive`) return { values: {  } } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["控制波形超出安全范围"] } as never;
    if (url === `${BASE}/defaults`) {
      const body = JSON.parse(String(options?.body));
      savedLayout = body.parameter_layout; savedValues = body.parameters;
      return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: savedLayout } } as never;
    }
    if (url === `${BASE}/sessions` && options?.method === "POST") return activeSession = makeSession();
    if (url === `${BASE}/sessions/frequency-session/parameters`) {
      if (rejectApply) throw new Error("模拟应用失败");
      const body = JSON.parse(String(options?.body));
      const changed = Object.keys(body.parameters).filter(key => body.parameters[key] !== activeSession.parameters[key]);
      const stale = new Set(changed.flatMap(key => impacts[key] ?? []));
      return activeSession = { ...activeSession, revision: activeSession.revision+1, parameters: body.parameters,
        steps: Object.fromEntries(Object.entries(activeSession.steps).map(([id, value]) => [id, stale.has(id) ? { status: "stale", error: "" } : value])) };
    }
    if (url === `${BASE}/sessions/frequency-session/steps`) {
      const body = JSON.parse(String(options?.body));
      if (body.step_ids.includes("A06") && !body.step_ids.includes("A05") && activeSession.steps.A05.status !== "completed")
        throw new Error("A06 缺少前置：A05 动态 Scope 首频配置与回读");
      return activeSession = { ...activeSession, revision: activeSession.revision+1,
        steps: { ...activeSession.steps, ...Object.fromEntries(body.step_ids.map((id: string) => [id, { status: "completed", error: "" }])) } };
    }
    if (url === `${BASE}/sessions/frequency-session/finish`) return activeSession = { ...activeSession, status: "closed", revision: activeSession.revision+1,
      current_step: null, pending_request: null, steps: { ...activeSession.steps, A07: { status: "completed", error: "" } },
      outputs: { "Z 正弦": "DC 0 V / OFF", "共同触发": "保持最后状态", Scope: "STOP" } };
    if (String(url).endsWith("/analysis") && options?.method === "POST") return { id: "analysis", status: "completed", events: [] } as never;
    if (String(url).endsWith("/analysis")) return { run_id: String(url).split("/").at(-2), revision: 0, options: {},
      steps: Object.fromEntries(analysisStages.map(stage => [stage.id, { status: "pending", error: "" }])), fingerprint: {}, summary: {} } as never;
    throw new Error(`未模拟 ${url}`);
  });
});
afterEach(cleanup);
const page = () => render(<MemoryRouter initialEntries={["/experiments/z-coil-current-frequency-response"]}>
  <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);
async function openParameters(id: string) {
  const stage = acquisition.find(item => item.id === id)!;
  const region = await screen.findByLabelText(`${id} ${stage.title} 参数`);
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  return within(region);
}

it("真实路由提供运行/历史、节点编辑保留、历史填回且预检失败不启动", async () => {
  page();
  const edit = await openParameters("A06");
  expect(screen.getAllByRole("tab")).toHaveLength(2);
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  fireEvent.change(edit.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await within(screen.getByRole("tabpanel", { name: "历史" })).findByAltText("results/transfer_function.png")).toHaveAttribute("src", "/frequency.png");
  expect(await within(screen.getByRole("tabpanel", { name: "历史" })).findByText("采用 DG 命令幅值参考，相位不用于闭环标定。")).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect((await openParameters("A06")).getByLabelText("运行标签")).toHaveValue("draft");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect((await openParameters("A06")).getByLabelText("运行标签")).toHaveValue("historical");
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
  fireEvent.click(screen.getByRole("checkbox", { name: "A06 对数扫频、电流校验与重复帧保存" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 \/ 重跑" }));
  await screen.findByText(/A06 缺少前置：A05 动态 Scope/);
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 \/ 重跑" }));
  await waitFor(() => expect(activeSession.steps.A06.status).toBe("completed"));
  expect(activeSession.steps.A07.status).toBe("pending");
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await screen.findByText(/下方状态为已结束会话的执行记录/);
  expect(activeSession.steps.A07.status).toBe("completed");
  expect(activeSession.outputs["Z 正弦"]).toBe("DC 0 V / OFF");
});

it("恢复会话后草稿预览与真实应用一致，失败保留草稿，还原清除预览", async () => {
  activeSession = makeSession(true); activeSession.parameters.DRIVE_AMPLITUDE_VPP = .8;
  page();
  const edit = await openParameters("A04");
  await waitFor(() => expect(edit.getByLabelText("低幅度正弦驱动")).toHaveValue(.8));
  fireEvent.change(edit.getByLabelText("低幅度正弦驱动"), { target: { value: ".6" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A04.*A05.*A06/)).toBeVisible();
  expect(activeSession.steps.A04.status).toBe("completed");
  fireEvent.change(edit.getByLabelText("低幅度正弦驱动"), { target: { value: ".8" } });
  expect(screen.queryByText(/参数草稿尚未应用，请先应用/)).not.toBeInTheDocument();
  fireEvent.change(edit.getByLabelText("低幅度正弦驱动"), { target: { value: ".6" } });
  rejectApply = true;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await screen.findByText(/模拟应用失败/);
  expect(edit.getByLabelText("低幅度正弦驱动")).toHaveValue(.6);
  rejectApply = false;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(activeSession.steps.A04.status).toBe("stale"));
  expect(activeSession.steps.A02.status).toBe("completed");
  expect(mockApi.mock.calls.filter(([url]) => url === `${BASE}/sessions/frequency-session/steps`)).toHaveLength(0);
});

it("历史分析只用目录状态，保存默认值仍保存下一次实验与布局", async () => {
  page();
  const edit = await openParameters("A04");
  fireEvent.change(edit.getByLabelText("低幅度正弦驱动"), { target: { value: ".9" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "history-one" } });
  await screen.findByText(/正在编辑 history-one 的分析覆盖值/);
  expect(screen.getAllByLabelText("低幅度正弦驱动")).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "history-two" } });
  await screen.findByText(/正在编辑 history-two 的分析覆盖值/);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`);
  expect(JSON.parse(String(saved?.[1]?.body)).parameters.DRIVE_AMPLITUDE_VPP).toBe(.9);
  expect(JSON.parse(String(saved?.[1]?.body)).parameter_layout).toEqual(layout);
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "" } });
  expect(edit.getByLabelText("低幅度正弦驱动")).toHaveValue(.9);
});

it("共享周期数跨节点同步，多参数草稿合并真实影响且分类同步", async () => {
  activeSession = makeSession(true);
  page();
  const first = await openParameters("A05");
  const repeated = await openParameters("A06");
  const output = await openParameters("A04");
  expect(first.getByLabelText("每次采集周期数")).toHaveValue(3);
  fireEvent.change(first.getByLabelText("每次采集周期数"), { target: { value: "4" } });
  expect(repeated.getByLabelText("每次采集周期数")).toHaveValue(4);
  expect(screen.getByText(/预计需重跑的已完成步骤：A05.*A06/)).toBeVisible();
  fireEvent.change(output.getByLabelText("低幅度正弦驱动"), { target: { value: ".8" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A04.*A05.*A06/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(activeSession.steps.A04.status).toBe("stale"));
  expect(activeSession.steps.A03.status).toBe("completed");
  expect(activeSession.parameters.SCOPE_CYCLES).toBe(4);
  expect(screen.getAllByLabelText("低幅度正弦驱动")).toHaveLength(1);
  fireEvent.click(first.getByRole("button", { name: "每次采集周期数 移至高级参数" }));
  fireEvent.click(first.getByText("高级参数（1）"));
  fireEvent.click(repeated.getByText("高级参数（1）"));
  expect(first.getByRole("button", { name: "每次采集周期数 移至基础参数" })).toBeVisible();
  expect(repeated.getByRole("button", { name: "每次采集周期数 移至基础参数" })).toBeVisible();
  expect(first.getByLabelText("每次采集周期数")).toHaveValue(4);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`);
  expect(JSON.parse(String(saved?.[1]?.body)).parameter_layout.advanced).toContain("SCOPE_CYCLES");
  cleanup();
  page();
  const reloaded = await openParameters("A05");
  fireEvent.click(reloaded.getByText("高级参数（1）"));
  expect(reloaded.getByRole("button", { name: "每次采集周期数 移至基础参数" })).toBeVisible();
  expect(reloaded.getByLabelText("每次采集周期数")).toHaveValue(4);
});

it("真实路由兼容缺失结果与缺少参考元数据的历史报告", async () => {
  omitArtifacts = true;
  page();
  await screen.findByRole("button", { name: "创建分步会话" });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await within(screen.getByRole("tabpanel", { name: "历史" })).findByText("本次运行暂无结果文件。")).toBeVisible();
  cleanup();
  omitArtifacts = false; legacyReport = true;
  page();
  await screen.findByRole("button", { name: "创建分步会话" });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await within(screen.getByRole("tabpanel", { name: "历史" })).findByText("历史报告未记录相位参考，不能确认闭环相位资格。")).toBeVisible();
});

it.each([
  { label: "单次实测", values: { capture_count: 4, phase_reference: "measured_drive_voltage", reliable_point_count: 0,
    reliable_band_hz: { minimum: null, maximum: null }, closed_loop_usable: false },
    message: "当前报告不具备闭环资格；幅值与相位诊断已保留。" },
  { label: "两次有效重复", values: { phase_reference: "measured_drive_voltage", phase_reliable_point_count: 4, closed_loop_usable: true },
    message: "实测参考与有效重复资格已通过；闭环仍需校验目标谐波覆盖。" },
  { label: "旧实测报告缺少重复资格", values: { phase_reference: "measured_drive_voltage", closed_loop_usable: undefined },
    message: "历史报告未记录有效重复资格，请从原始帧重新分析后用于闭环。" },
  { label: "旧v2错误宣称闭环可用但缺少精确计数规则", values: { phase_reference: "measured_drive_voltage", closed_loop_usable: true,
    valid_frame_count_rule: undefined },
    message: "历史报告未记录有效重复资格，请从原始帧重新分析后用于闭环。" },
])("$label 通过真实历史路由展示诊断资格", async ({ values, message }) => {
  reportOverride = values;
  page();
  await screen.findByRole("button", { name: "创建分步会话" });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const history = within(screen.getByRole("tabpanel", { name: "历史" }));
  expect(await history.findByText(message)).toBeVisible();
  expect(history.getByText(/幅值可靠点：4/)).toBeVisible();
  expect(history.getByRole("link", { name: "results/frequency_response.json" })).toHaveAttribute("href", "/frequency-report.json");
  expect(history.getByAltText("results/transfer_function.png")).toBeVisible();
  if (values.closed_loop_usable === false) {
    expect(history.getByText(/可靠覆盖：无/)).toBeVisible();
    expect(history.queryByText(/可靠覆盖：.*Hz/)).not.toBeInTheDocument();
  }
});
