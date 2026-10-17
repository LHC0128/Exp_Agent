import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));
const mockApi = vi.mocked(api);
const BASE = "/api/experiments/z-aw-current-waveform-scope-check";
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "next", group: "basic", unit: "" },
  { name: "CONTROL_SCALE", label: "控制幅度比例", type: "number", default: 1, group: "basic", unit: "" },
  { name: "CONTROL_RESULTS_ROOT", label: "最优控制结果根目录", type: "string", default: "theory", group: "advanced", unit: "" },
  { name: "SCOPE_CYCLES", label: "每次采集周期数", type: "integer", default: 3, group: "basic", unit: "" },
  { name: "SCOPE_REPEATS", label: "重复采集次数", type: "integer", default: 2, group: "basic", unit: "" },
];
const layout = { basic: fields.filter(f => f.group === "basic").map(f => f.name), advanced: ["CONTROL_RESULTS_ROOT"] };
const params = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const acquisition = [
  ["A01", "参数与接线预检", "准备", []], ["A02", "连接设备与同步时钟", "准备", []],
  ["A03", "电流目标与任意波触发配置", "波形与 Scope", ["CONTROL_SCALE", "CONTROL_RESULTS_ROOT"]],
  ["A04", "Scope 配置与触发就绪", "波形与 Scope", ["SCOPE_CYCLES"]],
  ["A05", "重复采集与原始帧保存", "采集与结束", ["RUN_TAG", "SCOPE_REPEATS"]],
  ["A06", "安全收尾 / 结束会话", "采集与结束", []],
].map(([id, title, group, parameters], index, all) => ({ id: String(id), title: String(title), group: String(group),
  dependencies: index && id !== "A06" ? [String(all[index-1][0])] : [],
  parameters: parameters as string[], display_parameters: parameters as string[], artifacts: [] }));
const analysisStages = ["加载配置与电流原始帧", "电流对齐与相似度评估", "诊断绘图与报告导出"]
  .map((title, index) => ({ id: `B0${index+1}`, title, group: "离线分析",
    dependencies: index ? [`B0${index}`] : [], parameters: [], display_parameters: [], artifacts: [] }));
const impacts: Record<string, string[]> = { RUN_TAG: ["A05"], SCOPE_REPEATS: ["A05"],
  SCOPE_CYCLES: ["A04", "A05"], CONTROL_SCALE: ["A03", "A04", "A05"], CONTROL_RESULTS_ROOT: ["A03", "A04", "A05"] };
const flow = { acquisition, analysis: analysisStages,
  parameter_impacts: { acquisition: impacts, analysis: {} }, diagram_url: `${BASE}/workflow/diagram` };
const makeSession = (completed = false) => ({ session_id: "scope-session", job_id: "scope-job", revision: 0,
  status: "waiting", parameters: params(), current_step: null, pending_request: null,
  steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: completed && stage.id !== "A06" ? "completed" : "pending", error: "" }])),
  outputs: { "Z 任意波": "Burst ON", "共同触发": "ON", Scope: "STOP" } });
let activeSession: any;
let preflightOk: boolean;
let rejectApply: boolean;

beforeEach(() => {
  activeSession = null; preflightOk = true; rejectApply = false;
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    if (url === BASE) return { title: "Z 任意波实际电流波形验证", description: "SDS CH3 采样电阻电压与 CH4 触发", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: layout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === `${BASE}/workflow`) return flow as never;
    if (url === `${BASE}/sessions/active`) return activeSession as never;
    if (url === "/api/jobs/scope-job") return { id: "scope-job", status: "running", events: [] } as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [
      { run_id: "history-one", run_tag: "old", timestamp: "1014", completion_status: "completed" },
      { run_id: "history-two", run_tag: "old2", timestamp: "1013", completion_status: "failed" }], total: 2 } as never;
    if (String(url).endsWith("/summary")) return { run_id: "history-one", run_tag: "old", timestamp: "1014", completion_status: "completed",
      analysis_status: "completed", analysis_error: "", parameters: { ...params(), RUN_TAG: "historical", CONTROL_SCALE: .7 },
      artifacts: { "results/current_waveform_similarity.png": "/waveform.png" } } as never;
    if (url === `${BASE}/derive`) return { values: { CONTROL_REPEAT_FREQUENCY_HZ: 1000 } } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["控制波形超出安全范围"] } as never;
    if (url === `${BASE}/defaults`) return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: JSON.parse(String(options?.body)).parameter_layout } } as never;
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
        throw new Error("A05 缺少前置：A04 Scope 配置与触发就绪");
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
const page = () => render(<MemoryRouter initialEntries={["/experiments/z-aw-current-waveform-scope-check"]}>
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
  const edit = await openParameters("A05");
  expect(screen.getAllByRole("tab")).toHaveLength(2);
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  fireEvent.change(edit.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await within(screen.getByRole("tabpanel", { name: "历史" })).findByAltText("results/current_waveform_similarity.png")).toHaveAttribute("src", "/waveform.png");
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
  fireEvent.click(screen.getByRole("checkbox", { name: "A05 重复采集与原始帧保存" }));
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
