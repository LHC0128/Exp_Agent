import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));
const mockApi = vi.mocked(api);
const BASE = "/api/experiments/z-coil-inductance-frequency-response";
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "next", group: "basic", unit: "" },
  { name: "DRIVE_AMPLITUDE_VPP", label: "Z 线圈正弦幅度", type: "number", default: 1, group: "basic", unit: "" },
  { name: "DRIVE_OFFSET_V", label: "Z 线圈正弦偏置", type: "number", default: 0, group: "advanced", unit: "" },
  { name: "SCOPE_CYCLES", label: "每次采集周期数", type: "integer", default: 3, group: "basic", unit: "" },
  { name: "SCOPE_REPEATS", label: "重复采集次数", type: "integer", default: 2, group: "basic", unit: "" },
];
const layout = { basic: fields.filter(f => f.group === "basic").map(f => f.name), advanced: ["DRIVE_OFFSET_V"] };
const params = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const qualityGates: Record<string, string[]> = {
  A01: ["参数、安全限值、接线映射及独立通道兼容检查全部通过"],
  A03: ["参考时钟写入后严格回读一致，失败先收尾再断开"],
  A06: ["Scope 完整帧与自动量程检查通过；读取或写盘失败保留已取得的帧并停止后续采集"],
  B01: ["实验身份、原始格式、必需波形、正频率与下降沿均有效"],
  B02: ["存在有效幅值供参考归一化；不足四个有效频率点时模型不可用，保留测量诊断供导出"],
};
const acquisition = [
  ["A01", "参数与接线预检", "启动准备", []], ["A02", "设备连接", "启动准备", []],
  ["A03", "参考时钟同步与回读", "启动准备", []],
  ["A04", "正弦与共同触发配置", "激励与采集", ["DRIVE_AMPLITUDE_VPP", "DRIVE_OFFSET_V"]],
  ["A05", "Scope 首频配置与回读", "激励与采集", ["SCOPE_CYCLES"]],
  ["A06", "线性扫频与重复帧保存", "激励与采集", ["RUN_TAG", "SCOPE_REPEATS", "SCOPE_CYCLES"]],
  ["A07", "安全收尾 / 结束会话", "结束", []],
].map(([id, title, group, parameters], index, all) => ({ id: String(id), title: String(title), group: String(group),
  dependencies: index && id !== "A07" ? [String(all[index-1][0])] : [],
  parameters: parameters as string[], display_parameters: parameters as string[], artifacts: [], quality_gates: qualityGates[String(id)] ?? [] }));
const analysisStages = ["配置与原始帧资格校验", "逐帧正弦与频响模型拟合", "频响诊断图与报告导出"]
  .map((title, index) => ({ id: `B0${index+1}`, title, group: "独立离线分析",
    dependencies: index ? [`B0${index}`] : [], parameters: [], display_parameters: [], artifacts: [], quality_gates: qualityGates[`B0${index+1}`] ?? [] }));
const impacts: Record<string, string[]> = { RUN_TAG: ["A06"], SCOPE_REPEATS: ["A06"],
  SCOPE_CYCLES: ["A05", "A06"], DRIVE_AMPLITUDE_VPP: ["A04", "A05", "A06"], DRIVE_OFFSET_V: ["A04", "A05", "A06"] };
const flow = { acquisition, analysis: analysisStages,
  parameter_impacts: { acquisition: impacts, analysis: {} }, diagram_url: `${BASE}/workflow/diagram` };
const makeSession = (completed = false) => ({ session_id: "frequency-session", job_id: "frequency-job", revision: 0,
  status: "waiting", parameters: params(), current_step: null, pending_request: null,
  steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: completed && stage.id !== "A07" ? "completed" : "pending", error: "" }])),
  outputs: completed ? { "Z 正弦": "4000 Hz / ON", "共同触发": "ON", Scope: "STOP" }
    : { "Z 正弦": "未知", "共同触发": "未知", Scope: "未知" } });
let activeSession: any;
let preflightOk: boolean;
let rejectApply: boolean;
let savedParameters: Record<string, unknown>;
let savedLayout: typeof layout;
let artifactVersion: number;

beforeEach(() => {
  activeSession = null; preflightOk = true; rejectApply = false;
  savedParameters = params(); savedLayout = structuredClone(layout); artifactVersion = 1;
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    if (url === BASE) return { title: "Z 线圈电感效应频率响应", description: "SDS CH3 线圈电压与 CH4 触发", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields: fields.map(field => ({ ...field, default: savedParameters[field.name] })),
      parameter_layout_saved: true, parameter_layout: savedLayout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === `${BASE}/workflow`) return flow as never;
    if (url === `${BASE}/sessions/active`) return activeSession as never;
    if (url === "/api/jobs/frequency-job") return { id: "frequency-job", status: "running", events: [] } as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [
      { run_id: "history-one", run_tag: "old", timestamp: "1014", completion_status: "completed" },
      { run_id: "history-two", run_tag: "old2", timestamp: "1013", completion_status: "failed" }], total: 2 } as never;
    if (String(url).endsWith("/summary")) return { run_id: String(url).split("/").at(-2), run_tag: "old", timestamp: "1014", completion_status: "completed",
      analysis_status: "completed", analysis_error: "", parameters: { ...params(), RUN_TAG: "historical", DRIVE_AMPLITUDE_VPP: .7 },
      artifacts: { "results/frequency_response.png": `/frequency.png?v=${artifactVersion}`,
        "results/waveform_overview.png": `/waveforms.png?v=${artifactVersion}`,
        "results/frequency_response.json": `/frequency-report.json?v=${artifactVersion}` } } as never;
    if (String(url).startsWith("/frequency-report.json?v=")) return { capture_count: 8, frequency_count: 4,
      theory_fit: String(url).endsWith("=1") ? { available: false, reason: "模拟有效点不足" }
        : { available: true, amplitude_zero_frequency_v: .45, amplitude_high_frequency_v: .1,
          corner_frequency_hz: 2100, r_squared: .9999 } } as never;
    if (url === `${BASE}/derive`) return { values: {  } } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["控制波形超出安全范围"] } as never;
    if (url === `${BASE}/defaults`) {
      const body = JSON.parse(String(options?.body));
      savedParameters = structuredClone(body.parameters); savedLayout = structuredClone(body.parameter_layout);
      return { message: "已保存", schema: { fields: fields.map(field => ({ ...field, default: savedParameters[field.name] })),
        parameter_layout_saved: true, parameter_layout: savedLayout } } as never;
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
        throw new Error("A06 缺少前置：A05 Scope 首频配置与回读");
      return activeSession = { ...activeSession, revision: activeSession.revision+1,
        steps: { ...activeSession.steps, ...Object.fromEntries(body.step_ids.map((id: string) => [id, { status: "completed", error: "" }])) } };
    }
    if (url === `${BASE}/sessions/frequency-session/finish`) return activeSession = {
      ...activeSession, status: "closed", revision: activeSession.revision+1,
      steps: { ...activeSession.steps, A07: { status: "completed", error: "" } },
      outputs: { "Z 正弦": "DC 0 V / OFF", "共同触发": "保持最后状态", Scope: "STOP" } };
    if (url === "/api/runs/history-one/analyze" && options?.method === "POST") {
      artifactVersion++;
      return { id: "reanalyze", status: "completed", events: [] } as never;
    }
    if (String(url).endsWith("/analysis") && options?.method === "POST") return { id: "analysis", status: "completed", events: [] } as never;
    if (String(url).endsWith("/analysis")) return { run_id: String(url).split("/").at(-2), revision: 0, options: {},
      steps: Object.fromEntries(analysisStages.map(stage => [stage.id, { status: "pending", error: "" }])), fingerprint: {}, summary: {} } as never;
    throw new Error(`未模拟 ${url}`);
  });
});
afterEach(cleanup);
const page = () => render(<MemoryRouter initialEntries={["/experiments/z-coil-inductance-frequency-response"]}>
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
  expect(screen.getByText(`质量门槛：${qualityGates.B02[0]}`)).toBeVisible();
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  fireEvent.change(edit.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await within(screen.getByRole("tabpanel", { name: "历史" })).findByAltText("results/frequency_response.png"))
    .toHaveAttribute("src", expect.stringMatching(/^\/frequency\.png\?v=\d+$/));
  expect(await within(screen.getByRole("tabpanel", { name: "历史" })).findByText("一阶幅值模型不可用：模拟有效点不足")).toBeVisible();
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
  fireEvent.click(screen.getByRole("checkbox", { name: "A06 线性扫频与重复帧保存" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 \/ 重跑" }));
  await screen.findByText(/A06 缺少前置：A05 Scope/);
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 \/ 重跑" }));
  await waitFor(() => expect(activeSession.steps.A06.status).toBe("completed"));
  expect(activeSession.steps.A07.status).toBe("pending");
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await screen.findByText(/下方状态为已结束会话的执行记录/);
});

it("恢复会话后草稿预览与真实应用一致，失败保留草稿，还原清除预览", async () => {
  activeSession = makeSession(true); activeSession.parameters.DRIVE_AMPLITUDE_VPP = .8;
  page();
  const edit = await openParameters("A04");
  await waitFor(() => expect(edit.getByLabelText("Z 线圈正弦幅度")).toHaveValue(.8));
  fireEvent.change(edit.getByLabelText("Z 线圈正弦幅度"), { target: { value: ".6" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A04.*A05.*A06/)).toBeVisible();
  expect(activeSession.steps.A04.status).toBe("completed");
  fireEvent.change(edit.getByLabelText("Z 线圈正弦幅度"), { target: { value: ".8" } });
  expect(screen.queryByText(/参数草稿尚未应用，请先应用/)).not.toBeInTheDocument();
  fireEvent.change(edit.getByLabelText("Z 线圈正弦幅度"), { target: { value: ".6" } });
  rejectApply = true;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await screen.findByText(/模拟应用失败/);
  expect(edit.getByLabelText("Z 线圈正弦幅度")).toHaveValue(.6);
  rejectApply = false;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(activeSession.steps.A04.status).toBe("stale"));
  expect(activeSession.steps.A02.status).toBe("completed");
  expect(mockApi.mock.calls.filter(([url]) => url === `${BASE}/sessions/frequency-session/steps`)).toHaveLength(0);
});

it("历史分析只用目录状态，保存默认值仍保存下一次实验与布局", async () => {
  page();
  const edit = await openParameters("A04");
  fireEvent.change(edit.getByLabelText("Z 线圈正弦幅度"), { target: { value: ".9" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "history-one" } });
  await screen.findByText(/正在编辑 history-one 的分析覆盖值/);
  expect(screen.getAllByLabelText("Z 线圈正弦幅度")).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "history-two" } });
  await screen.findByText(/正在编辑 history-two 的分析覆盖值/);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`);
  expect(JSON.parse(String(saved?.[1]?.body)).parameters.DRIVE_AMPLITUDE_VPP).toBe(.9);
  expect(JSON.parse(String(saved?.[1]?.body)).parameter_layout).toEqual(layout);
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "" } });
  expect(edit.getByLabelText("Z 线圈正弦幅度")).toHaveValue(.9);
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
  fireEvent.change(output.getByLabelText("Z 线圈正弦幅度"), { target: { value: ".8" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A04.*A05.*A06/)).toBeVisible();
  fireEvent.change(output.getByLabelText("Z 线圈正弦幅度"), { target: { value: "1" } });
  fireEvent.change(repeated.getByLabelText("每次采集周期数"), { target: { value: "3" } });
  expect(screen.queryByText(/预计需重跑的已完成步骤/)).not.toBeInTheDocument();
  expect(activeSession.steps.A05.status).toBe("completed");
  fireEvent.change(first.getByLabelText("每次采集周期数"), { target: { value: "4" } });
  fireEvent.change(output.getByLabelText("Z 线圈正弦幅度"), { target: { value: ".8" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(activeSession.steps.A04.status).toBe("stale"));
  expect(activeSession.steps.A03.status).toBe("completed");
  expect(activeSession.parameters.SCOPE_CYCLES).toBe(4);
  expect(screen.getAllByLabelText("Z 线圈正弦幅度")).toHaveLength(1);
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
  cleanup(); activeSession = null; page();
  const restored = await openParameters("A05");
  fireEvent.click(restored.getByText("高级参数（1）"));
  expect(restored.getByLabelText("每次采集周期数")).toHaveValue(4);
  expect(restored.getByRole("button", { name: "每次采集周期数 移至基础参数" })).toBeVisible();
  expect(screen.queryByText("布局有未保存修改")).not.toBeInTheDocument();
});

it("历史重新分析刷新两张专属图和JSON模型诊断，不覆盖下一次实验草稿", async () => {
  page();
  const edit = await openParameters("A04");
  fireEvent.change(edit.getByLabelText("Z 线圈正弦幅度"), { target: { value: ".9" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const history = within(screen.getByRole("tabpanel", { name: "历史" }));
  await history.findByText("一阶幅值模型不可用：模拟有效点不足");
  expect(history.getByAltText("results/waveform_overview.png")).toHaveAttribute("src", "/waveforms.png?v=1");
  fireEvent.click(history.getByRole("button", { name: "重新分析" }));
  await history.findByText(/一阶幅值模型：A0 = 0.45 V，Ainf = 0.1 V，fc = 2100 Hz，R² = 0.9999/);
  expect(history.getByAltText("results/frequency_response.png")).toHaveAttribute("src", "/frequency.png?v=2");
  expect(history.getByAltText("results/waveform_overview.png")).toHaveAttribute("src", "/waveforms.png?v=2");
  expect(mockApi.mock.calls.some(([url]) => url === "/frequency-report.json?v=2")).toBe(true);
  expect(mockApi.mock.calls.find(([url]) => url === "/api/runs/history-one/analyze")?.[1]?.body)
    .toBe(JSON.stringify({ experiment_id: "z-coil-inductance-frequency-response" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect((await openParameters("A04")).getByLabelText("Z 线圈正弦幅度")).toHaveValue(.9);
});

it("目录分析显示真实质量失败原因，不能跳过失效拟合导出", async () => {
  const standard = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/runs/history-one/analysis`) {
      if (options?.method === "POST") throw new Error("B03 缺少前置模块：B02 逐帧正弦与频响模型拟合");
      return { run_id: "history-one", revision: 2, options: {}, fingerprint: {},
        steps: { B01: { status: "completed", error: "" },
          B02: { status: "quality_failed", error: "归一化参考幅值必须为正" }, B03: { status: "pending", error: "" } },
        summary: { status: "quality_failed", error: "归一化参考幅值必须为正" } } as never;
    }
    return standard(url, options) as never;
  });
  page();
  await openParameters("A04");
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "history-one" } });
  expect(await screen.findByText("归一化参考幅值必须为正")).toBeVisible();
  expect(screen.getByText("质量未通过")).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox", { name: "B03 频响诊断图与报告导出" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await screen.findByText(/B03 缺少前置模块：B02/);
  const posted = mockApi.mock.calls.find(([url, options]) => url === `${BASE}/runs/history-one/analysis` && options?.method === "POST");
  expect(JSON.parse(String(posted?.[1]?.body))).toEqual({ revision: 2, step_ids: ["B03"], analysis_parameters: {} });
});
