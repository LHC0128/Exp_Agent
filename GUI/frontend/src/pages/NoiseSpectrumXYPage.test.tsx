import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./noiseSpectrumXY.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false }) }));
vi.mock("../components/JobView", () => ({
  JobView: ({ job, onUpdate }: any) => job
    ? <button onClick={() => onUpdate({ ...job, status: "completed" })}>完成测试任务</button>
    : null,
}));

const mockApi = vi.mocked(api);
const BASE = "/api/experiments/noise-spectrum-xy";
const fields = fixture.schema.fields;
const acquisition = fixture.flow.acquisition;
const analysisStages = fixture.flow.analysis;
const parameterImpacts = fixture.flow.parameter_impacts;
let summary: any;
let activeSession: any;
let extraRuns: any[];
let parameterLayout: { basic: string[]; advanced: string[] };
let defaults: Record<string, any>;
const makeSession = () => ({ session_id: "session-test", job_id: "session-job", revision: 0,
  status: "waiting", parameters: { ...defaults },
  steps: Object.fromEntries(acquisition.map((stage) => [stage.id, { status: "pending", error: "" }])),
  outputs: { X: "未知", Y: "未知", 温控: "未知", 共用触发: "未知" }, pending_request: null });

const renderPage = () => render(
  <MemoryRouter initialEntries={["/experiments/noise-spectrum-xy"]}>
    <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
  </MemoryRouter>,
);

const openParameters = async (id: string) => {
  const stage = [...acquisition, ...analysisStages].find((item) => item.id === id)!;
  const region = await screen.findByLabelText(`${id} ${stage.title} 参数`);
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  return within(region);
};

beforeEach(() => {
  activeSession = null;
  extraRuns = [];
  parameterLayout = { basic: fields.filter((field) => field.group === "basic").map((field) => field.name),
    advanced: fields.filter((field) => field.group === "advanced").map((field) => field.name) };
  defaults = Object.fromEntries(fields.map((field) => [field.name, field.default]));
  // 模拟用户已保存的分类和分析范围，不改真实 schema 夹具。
  parameterLayout.advanced = parameterLayout.advanced.filter((name) => name !== "TEMP_SWITCH_ON_SETTLE_S");
  parameterLayout.basic.push("TEMP_SWITCH_ON_SETTLE_S");
  defaults.TEMP_SWITCH_ON_SETTLE_S = 2;
  defaults.ANALYSIS_BIN_WIDTH_HZ = 12;
  defaults.ANALYSIS_FREQUENCY_MIN_HZ = 5;
  summary = {
    run_id: "0716_1132_noise",
    timestamp: "0716_1132",
    run_tag: "history-tag",
    completion_status: "completed",
    analysis_status: "completed",
    analysis_error: "",
    parameters: { RUN_TAG: "history-tag" },
    artifacts: { "noise_spectra_extracted.png": "/noise.png?v=1" },
  };
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === BASE) return { title: "XY 控制测量噪声谱（正弦）", description: "噪声谱", wiring_notes: ["三通触发"] };
    if (url === `${BASE}/schema`) return { fields: fields.map((field) => ({ ...field, default: defaults[field.name] })),
      parameter_layout_saved: true, parameter_layout: parameterLayout };
    if (url === "/api/jobs") return [];
    if (url === `${BASE}/workflow`) return { acquisition, analysis: analysisStages, parameter_impacts: parameterImpacts, diagram_url: `${BASE}/workflow/diagram` };
    if (url === `${BASE}/sessions/active`) return activeSession;
    if (url === "/api/jobs/session-job") return { id: "session-job", kind: "session:noise-spectrum-xy", status: "running", events: [] };
    if (url === `${BASE}/sessions` && options?.method === "POST") return activeSession = makeSession();
    if (url === `${BASE}/sessions/session-test/parameters`) {
      const parameters = JSON.parse(String(options?.body)).parameters;
      const impacted = new Set(Object.keys(parameters).filter((name) => parameters[name] !== activeSession.parameters[name])
        .flatMap((name) => (parameterImpacts.acquisition as Record<string, string[]>)[name] ?? []));
      activeSession = { ...activeSession, revision: activeSession.revision + 1, parameters,
        steps: Object.fromEntries(Object.entries(activeSession.steps).map(([id, state]: [string, any]) => [id,
          impacted.has(id) && state.status !== "pending" ? { status: "stale", error: "" } : state])) };
      return activeSession;
    }
    if (url === `${BASE}/sessions/session-test/steps`) {
      const body = JSON.parse(String(options?.body));
      const available = new Set(Object.keys(activeSession.steps).filter((id) => activeSession.steps[id].status === "completed"));
      for (const stage of acquisition.filter((item) => body.step_ids.includes(item.id))) {
        const missing = stage.dependencies.filter((id) => !available.has(id));
        if (missing.length) throw new Error(`缺少前置：${missing.map((id) => `${id} ${acquisition.find((item) => item.id === id)!.title}`).join("、")}`);
        available.add(stage.id);
      }
      activeSession = { ...activeSession, revision: activeSession.revision + 1,
        steps: { ...activeSession.steps, ...Object.fromEntries(body.step_ids.map((id: string) => [id, { status: "completed", error: "" }])) } };
      if (body.step_ids.includes("A13")) activeSession.steps.A11 = { status: "stale", error: "再次扫描需要 DAQ 准备" };
      if (body.step_ids.includes("A14")) activeSession.status = "closed";
      return activeSession;
    }
    if (url === `${BASE}/sessions/session-test/finish`) return activeSession = { ...activeSession, status: "closed", revision: activeSession.revision + 1 };
    if (url.endsWith("/analysis") && options?.method === "POST") return { id: "analysis", status: "running", events: [] };
    if (url.endsWith("/analysis")) return { run_id: decodeURIComponent(url.split("/").at(-2)!), revision: 0,
      steps: Object.fromEntries(analysisStages.map((stage) => [stage.id, { status: "pending", error: "" }])),
      options: { bin_width_hz: 12, frequency_min_hz: 5, frequency_max_hz: 50000, fit_half_width_hz: 3000 }, summary: {}, fingerprint: {} };
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary, ...extraRuns], total: 1 + extraRuns.length };
    if (url.endsWith("/summary")) return summary;
    if (url === `${BASE}/preflight`) return { ok: true, errors: [] };
    if (url === `${BASE}/derive`) {
      const values = JSON.parse(String(options?.body)).parameters;
      return { values: values.HF2_DAQ_DURATION < .1 ? { error: "每点采集时长不足一个 Welch 分段" } : {
        nperseg: 37500, segments_per_point: values.HF2_DAQ_DURATION === 5 ? 119 : 23,
        bin_width_hz: 12, scan_seconds: 1560,
      } };
    }
    if (url === `${BASE}/defaults`) {
      const body = JSON.parse(String(options?.body));
      parameterLayout = body.parameter_layout;
      defaults = body.parameters;
      return { message: "参数与布局已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: parameterLayout } };
    }
    if (url === `${BASE}/runs` && options?.method === "POST") return { id: "acquisition", kind: "experiment:noise-spectrum-xy", status: "running", events: [] };
    if (url.endsWith("/analyze")) return { id: "analysis", status: "running", events: [] };
    throw new Error(`未模拟 ${url}`);
  });
});

afterEach(cleanup);

it("两列流程来自后端定义，缺少前置时拒绝且不自动补齐选择", async () => {
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  const nodes = screen.getByRole("region", { name: "交互实验流程图" });
  expect(within(nodes).getByText(`质量门槛：${acquisition.find((stage) => stage.id === "A10")!.quality_gates[0]}`)).toBeVisible();
  await waitFor(() => expect(within(nodes).getByLabelText("A03 参考时钟同步")).toBeEnabled());
  fireEvent.click(within(nodes).getByLabelText("A03 参考时钟同步"));
  await waitFor(() => expect(within(nodes).getByRole("button", { name: "运行所选 / 重跑" })).toBeEnabled());
  fireEvent.click(within(nodes).getByRole("button", { name: "运行所选 / 重跑" }));
  expect(await screen.findByText("Error: 缺少前置：A02 设备连接")).toBeVisible();
  expect(within(nodes).getByLabelText("A02 设备连接")).not.toBeChecked();
  const request = mockApi.mock.calls.find(([url]) => url.endsWith("session-test/steps"));
  expect(JSON.parse(String(request?.[1]?.body)).step_ids).toEqual(["A03"]);
  expect(within(nodes).getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
});

it("刷新恢复会话与参数，空闲时可修改，应用后显示失效节点并携带修订", async () => {
  activeSession = { ...makeSession(), revision: 7 };
  activeSession.parameters.RUN_TAG = "existing";
  activeSession.steps.A12.status = "completed";
  renderPage();
  const parameters = await openParameters("A12");
  await waitFor(() => expect(parameters.getByLabelText("运行标签")).toHaveValue("existing"));
  expect(screen.getByText(/分步等待期间保持输出状态/)).toBeVisible();
  expect(screen.getByLabelText("运行标签")).toBeEnabled();
  fireEvent.change(screen.getByLabelText("运行标签"), { target: { value: "rerun" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  expect(await screen.findByText("需重跑")).toBeVisible();
  const request = mockApi.mock.calls.find(([url]) => url.endsWith("session-test/parameters"));
  expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({ revision: 7, parameters: { RUN_TAG: "rerun" } });
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url.endsWith("session-test/finish") && JSON.parse(String(options?.body)).revision === 8)).toBe(true));
});

it("分阶段分析绑定历史目录，独立于等待中的硬件会话", async () => {
  activeSession = makeSession();
  renderPage();
  fireEvent.change(await screen.findByLabelText("分析操作对象"), { target: { value: summary.run_id } });
  await waitFor(() => expect(screen.getByRole("button", { name: "选择全部分析步骤" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  const run = screen.getByRole("button", { name: "运行所选分析 / 重算" });
  await waitFor(() => expect(run).toBeEnabled());
  fireEvent.click(run);
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url.endsWith(`${summary.run_id}/analysis`) && options?.method === "POST")).toBe(true));
  const request = mockApi.mock.calls.find(([url, options]) => url.endsWith(`${summary.run_id}/analysis`) && options?.method === "POST");
  expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({ revision: 0, step_ids: ["B01", "B02", "B03", "B04", "B05"], analysis_parameters: { ANALYSIS_BIN_WIDTH_HZ: 12 } });
});

it("时长变化更新只读分段预览，过短时长明确报错，并显示实际分析段数", async () => {
  summary.analysis_quality = { psd: { actual_rate_sa_s: 460526.3158, nperseg: 38378,
    bin_width_hz: 11.9997, segments_per_point_range: [23, 23] } };
  renderPage();
  const parameters = await openParameters("A11");
  const preview = within(screen.getByRole("region", { name: "交互实验流程图" })).getByLabelText("Welch 分段预览");
  expect(screen.queryByLabelText("Welch 每段点数")).not.toBeInTheDocument();
  expect(await within(preview).findByText(/每点平均段数：23（50%/)).toBeVisible();
  fireEvent.change(parameters.getByLabelText("每点采集时长 (s)"), { target: { value: "5" } });
  expect(await within(preview).findByText(/每点平均段数：119（50%/)).toBeVisible();
  const panel = screen.getByRole("tabpanel", { name: "运行" });
  expect(within(panel).getByText(/实际 Welch 每段点数：38378/)).toBeVisible();
  fireEvent.change(parameters.getByLabelText("每点采集时长 (s)"), { target: { value: "0.01" } });
  expect(await screen.findByText("每点采集时长不足一个 Welch 分段")).toBeVisible();
});

it("质量验收失败时仍在运行和历史中显示二维谱、错误及有效点数", async () => {
  summary = { ...summary, analysis_status: "quality_failed", analysis_error: "移动峰不足",
    artifacts: { "noise_spectrum_2d.png": "/noise-2d.png?v=1" },
    analysis_quality: { fit: { valid_points: 0, total_points: 200, valid_frequency_extent_hz: null } } };
  renderPage();
  const panel = screen.getByRole("tabpanel", { name: "运行" });
  expect(await within(panel).findByText(/质量验收未通过/)).toBeVisible();
  expect(within(panel).getByText(/通过质量验收 0 \/ 200/)).toBeVisible();
  expect(within(panel).getByText("移动峰不足")).toBeVisible();
  const label = "二维噪声谱（未标定时纵轴为扫描电压）";
  expect(within(panel).getByAltText(label)).toHaveAttribute("src", "/noise-2d.png?v=1");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const history = screen.getByRole("tabpanel", { name: "历史" });
  expect(await within(history).findByAltText(label)).toHaveAttribute("src", "/noise-2d.png?v=1");
  expect(within(history).getByText("移动峰不足")).toBeVisible();
});

it("从真实路由进入双 Tab，切换保留参数并按当前 schema 填回历史参数", async () => {
  renderPage();
  expect(within(screen.getByRole("tablist", { name: "XY 正弦控制噪声谱实验视图" })).getAllByRole("tab")).toHaveLength(2);
  expect(await within(screen.getByRole("tabpanel", { name: "运行" })).findByAltText("提取的可控与不可控噪声谱")).toHaveAttribute("src", "/noise.png?v=1");
  await openParameters("A12");
  fireEvent.change(screen.getByLabelText("运行标签"), { target: { value: "edited" } });
  fireEvent.change(screen.getByLabelText("目标频率扫描点数"), { target: { value: "123" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "填回参数" })).toBeEnabled());
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("edited");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("history-tag");
  expect(screen.getByLabelText("目标频率扫描点数")).toHaveValue(123);
});

it("仅保留运行与历史，交互流程图保留且两个主 Tab 支持双向循环", async () => {
  renderPage();
  const tablist = await screen.findByRole("tablist", { name: "XY 正弦控制噪声谱实验视图" });
  expect(within(tablist).getAllByRole("tab")).toHaveLength(2);
  expect(within(tablist).queryByRole("tab", { name: "架构" })).not.toBeInTheDocument();
  expect(document.querySelector('iframe[src^="/architecture/"]')).toBeNull();
  expect(await screen.findByRole("region", { name: "交互实验流程图" })).toBeVisible();
  const runTab = within(tablist).getByRole("tab", { name: "运行" });
  const historyTab = within(tablist).getByRole("tab", { name: "历史" });
  runTab.focus();
  for (const key of ["ArrowLeft", "ArrowRight"]) {
    fireEvent.keyDown(tablist, { key });
    expect(historyTab).toHaveFocus();
    expect(historyTab).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(tablist, { key });
    expect(runTab).toHaveFocus();
    expect(runTab).toHaveAttribute("aria-selected", "true");
  }
});

it("预检失败不启动采集，历史重新分析完成后刷新实际结果图", async () => {
  const normal = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/preflight`) return { ok: false, errors: ["目标频率范围无效"] };
    return normal(url, options);
  });
  renderPage();
  const workflow = within(screen.getByRole("region", { name: "交互实验流程图" }));
  await waitFor(() => expect(workflow.getByRole("button", { name: "预检并运行实验" })).toBeEnabled());
  fireEvent.click(workflow.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("目标频率范围无效")).toBeVisible();
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);

  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "重新分析" }));
  summary = { ...summary, artifacts: { "noise_spectra_extracted.png": "/noise.png?v=2" } };
  fireEvent.click(await screen.findByRole("button", { name: "完成测试任务" }));
  await waitFor(() => expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByAltText("提取的可控与不可控噪声谱")).toHaveAttribute("src", "/noise.png?v=2"));
});

it("旧后端缺少节点展示或影响范围时提示重启，仍恢复存活会话供结束", async () => {
  activeSession = makeSession();
  const normal = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/workflow`) return {
      acquisition: acquisition.map(({ display_parameters, ...stage }) => stage),
      analysis: analysisStages, diagram_url: `${BASE}/workflow/diagram`,
    };
    return normal(url, options);
  });
  renderPage();
  expect(await screen.findByText(/流程接口版本过旧，请在任务结束后重启 GUI 后端并刷新页面/)).toBeVisible();
  expect(screen.getByRole("button", { name: "创建分步会话" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "结束会话" })).toBeEnabled();
  expect(screen.queryByLabelText("A12 扫描采集 参数")).not.toBeInTheDocument();
});

it("初始化条件仅在配置节点出现，基础名称常显而高级编辑默认折叠", async () => {
  renderPage();
  await screen.findByText(/参数：Pump 光功率.*主磁场电流/);
  expect(screen.getAllByLabelText("主磁场电流 (mA)")).toHaveLength(1);
  expect(screen.getByLabelText("主磁场电流 (mA)")).not.toBeVisible();
  const initial = await openParameters("A04");
  expect(initial.getByLabelText("主磁场电流 (mA)")).toBeVisible();
  const daq = await openParameters("A11");
  expect(daq.getByLabelText("每点采集时长 (s)")).toBeVisible();
  expect(daq.getByLabelText("噪声采集请求率（DAQ/Scope） (Sa/s)")).not.toBeVisible();
  fireEvent.click(daq.getByText(/^高级参数（/));
  const captureRate = daq.getByLabelText("噪声采集请求率（DAQ/Scope） (Sa/s)");
  expect(captureRate).toBeVisible();
  expect(captureRate).toHaveValue(450000);
  const demod = await openParameters("A08");
  expect(demod.getByLabelText("锁相传输率（校相/读数） (Sa/s)")).toHaveValue(10000);
});

it("共享值与基础高级分类同步，保存后刷新恢复统一偏好", async () => {
  const page = renderPage();
  const demod = await openParameters("A08");
  const phase = await openParameters("A10");
  const scan = await openParameters("A12");
  fireEvent.change(phase.getByLabelText("恢复温控等待时间 (s)"), { target: { value: "3" } });
  for (const node of [demod, phase, scan]) expect(node.getByLabelText("恢复温控等待时间 (s)")).toHaveValue(3);
  for (const note of phase.getAllByText(/共用步骤：A08.*A12/)) expect(note).toBeVisible();
  fireEvent.click(phase.getByRole("button", { name: "恢复温控等待时间 移至高级参数" }));
  for (const node of [demod, phase, scan]) expect(node.getByLabelText("恢复温控等待时间 (s)")).not.toBeVisible();
  fireEvent.click(phase.getByText(/^高级参数（/));
  expect(phase.getByLabelText("恢复温控等待时间 (s)")).toBeVisible();
  fireEvent.click(within(screen.getByRole("region", { name: "交互实验流程图" })).getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  expect(parameterLayout.advanced).toContain("TEMP_SWITCH_ON_SETTLE_S");
  expect(parameterLayout.basic).not.toContain("TEMP_SWITCH_ON_SETTLE_S");
  page.unmount();
  renderPage();
  const recovered = await openParameters("A08");
  expect(recovered.getByLabelText("恢复温控等待时间 (s)")).not.toBeVisible();
  fireEvent.click(recovered.getByText(/^高级参数（/));
  expect(recovered.getByLabelText("恢复温控等待时间 (s)")).toHaveValue(3);
});

it("草稿预览使用后端重跑范围，还原会清除预览，应用后再更新真实状态", async () => {
  activeSession = { ...makeSession(), revision: 4,
    steps: Object.fromEntries(acquisition.map((stage) => [stage.id, { status: stage.id === "A14" ? "pending" : "completed", error: "" }])) };
  renderPage();
  const scan = await openParameters("A12");
  fireEvent.click(screen.getByLabelText("A12 扫描采集"));
  const run = screen.getByRole("button", { name: "运行所选 / 重跑" });
  await waitFor(() => expect(run).toBeEnabled());
  fireEvent.change(scan.getByLabelText("恢复温控等待时间 (s)"), { target: { value: "3" } });
  expect(run).toBeDisabled();
  expect(screen.getByText(/^预计需重跑的已完成步骤/)).toHaveTextContent("A08 Demod0 配置与校相");
  expect(screen.getAllByText("应用草稿后预计需重跑；当前执行记录尚未改变。")).toHaveLength(6);
  expect(mockApi.mock.calls.some(([url]) => url.endsWith("session-test/parameters"))).toBe(false);
  fireEvent.change(scan.getByLabelText("恢复温控等待时间 (s)"), { target: { value: "2" } });
  expect(screen.queryByText(/^预计需重跑的已完成步骤/)).not.toBeInTheDocument();
  expect(run).toBeEnabled();
  fireEvent.change(scan.getByLabelText("目标频率扫描点数"), { target: { value: "123" } });
  expect(screen.getByText(/^预计需重跑的已完成步骤/)).toHaveTextContent("A11 DAQ 准备");
  expect(screen.getAllByText("应用草稿后预计需重跑；当前执行记录尚未改变。")).toHaveLength(3);
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(screen.queryByText(/^预计需重跑的已完成步骤/)).not.toBeInTheDocument());
  expect(screen.getAllByText("需重跑")).toHaveLength(3);
  expect(activeSession.steps.A10.status).toBe("completed");
});

it("新实验与各历史目录保留独立草稿，保存默认值和分析请求使用各自参数", async () => {
  extraRuns = [{ ...summary, run_id: "0717_1200_other" }];
  renderPage();
  let psd = await openParameters("B02");
  fireEvent.change(psd.getByLabelText("频率格间距 (Hz)"), { target: { value: "24" } });
  const daq = await openParameters("A11");
  expect(daq.getByLabelText("频率格间距 (Hz)")).toHaveValue(24);
  const target = screen.getByLabelText("分析操作对象");
  fireEvent.change(target, { target: { value: summary.run_id } });
  psd = await openParameters("B02");
  expect(psd.getByLabelText("频率格间距 (Hz)")).toHaveValue(12);
  fireEvent.change(psd.getByLabelText("频率格间距 (Hz)"), { target: { value: "30" } });
  expect(daq.getByLabelText("频率格间距 (Hz)")).toHaveValue(24);
  fireEvent.change(target, { target: { value: extraRuns[0].run_id } });
  psd = await openParameters("B02");
  expect(psd.getByLabelText("频率格间距 (Hz)")).toHaveValue(12);
  fireEvent.change(psd.getByLabelText("频率格间距 (Hz)"), { target: { value: "40" } });
  fireEvent.change(target, { target: { value: summary.run_id } });
  psd = await openParameters("B02");
  expect(psd.getByLabelText("频率格间距 (Hz)")).toHaveValue(30);
  fireEvent.change(target, { target: { value: "" } });
  psd = await openParameters("B02");
  expect(psd.getByLabelText("频率格间距 (Hz)")).toHaveValue(24);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  expect(defaults.ANALYSIS_BIN_WIDTH_HZ).toBe(24);
  fireEvent.change(target, { target: { value: summary.run_id } });
  await openParameters("B02");
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url.endsWith(`${summary.run_id}/analysis`) && options?.method === "POST")).toBe(true));
  const request = mockApi.mock.calls.find(([url, options]) => url.endsWith(`${summary.run_id}/analysis`) && options?.method === "POST");
  expect(JSON.parse(String(request?.[1]?.body)).analysis_parameters.ANALYSIS_BIN_WIDTH_HZ).toBe(30);
});

it("历史分析草稿按后端分析范围预览，恢复原值后清除提示", async () => {
  const normal = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    const response: any = await normal(url, options);
    if (url.endsWith("/analysis") && options?.method !== "POST") return { ...response,
      steps: Object.fromEntries(analysisStages.map((stage) => [stage.id, { status: "completed", error: "" }])) };
    return response;
  });
  renderPage();
  fireEvent.change(await screen.findByLabelText("分析操作对象"), { target: { value: summary.run_id } });
  const ridge = await openParameters("B03");
  fireEvent.click(ridge.getByText("高级参数（2）"));
  fireEvent.change(ridge.getByLabelText("分析频带下限 (Hz)"), { target: { value: "10" } });
  expect(screen.getByText(/^分析参数草稿尚未应用/)).toHaveTextContent("B02 计算 Welch PSD");
  expect(screen.getAllByText("应用草稿后预计需重跑；当前执行记录尚未改变。")).toHaveLength(4);
  fireEvent.change(ridge.getByLabelText("分析频带下限 (Hz)"), { target: { value: "5" } });
  expect(screen.queryByText(/^分析参数草稿尚未应用/)).not.toBeInTheDocument();
  const fit = await openParameters("B04");
  fireEvent.click(fit.getByText("高级参数（3）"));
  fireEvent.change(fit.getByLabelText("峰定位半窗口 (Hz)"), { target: { value: "3500" } });
  expect(screen.getByText(/^分析参数草稿尚未应用/)).not.toHaveTextContent("B02");
  expect(screen.getAllByText("应用草稿后预计需重跑；当前执行记录尚未改变。")).toHaveLength(2);
  expect(defaults.ANALYSIS_FIT_HALF_WIDTH_HZ).toBe(3000);
});

it("采集执行中锁定采集与新实验参数，历史分析仍可独立编辑", async () => {
  activeSession = { ...makeSession(), status: "running", current_step: "A12" };
  renderPage();
  const scan = await openParameters("A12");
  await waitFor(() => expect(scan.getByLabelText("运行标签")).toBeDisabled());
  const future = await openParameters("B02");
  expect(future.getByLabelText("频率格间距 (Hz)")).toBeDisabled();
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: summary.run_id } });
  const history = await openParameters("B02");
  expect(history.getByLabelText("频率格间距 (Hz)")).toBeEnabled();
  expect(screen.getByRole("button", { name: "选择全部分析步骤" })).toBeEnabled();
});

it("实际 schema 的 46 个可见字段均可从真实节点编辑并还原", async () => {
  renderPage();
  await screen.findByRole("button", { name: "创建分步会话" });
  expect(fields).toHaveLength(46);
  for (const field of fields) {
    const stage = [...acquisition, ...analysisStages].find((item) => item.display_parameters.includes(field.name));
    expect(stage, field.name).toBeDefined();
    const region = await openParameters(stage!.id);
    const label = field.label + (field.unit ? ` (${field.unit})` : "");
    const input = region.getByLabelText(label);
    const advanced = input.closest("details");
    if (advanced && !advanced.open) fireEvent.click(advanced.querySelector("summary")!);
    expect(input).toBeVisible();
    expect(input).toBeEnabled();
    const value = defaults[field.name];
    const edited = typeof value === "number" ? value + (field.type === "integer" ? 1 : .01) : `${value}-edited`;
    fireEvent.change(input, { target: { value: String(edited) } });
    expect(input).toHaveValue(edited);
    fireEvent.change(input, { target: { value: String(value) } });
    expect(input).toHaveValue(value);
  }
}, 15000);

it("校相质量失败仍保留诊断，DAQ 后继不能被勾选请求绕过", async () => {
  activeSession = makeSession();
  for (const stage of acquisition.slice(0, 9)) activeSession.steps[stage.id].status = "completed";
  activeSession.steps.A10 = { status: "quality_failed", error: "XY 校相未收敛，禁止扫描" };
  renderPage();
  expect(await screen.findByText("XY 校相未收敛，禁止扫描")).toBeVisible();
  fireEvent.click(screen.getByLabelText("A11 DAQ 准备"));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  expect(await screen.findByText("Error: 缺少前置：A10 XY 迭代校相")).toBeVisible();
  expect(activeSession.steps.A11.status).toBe("pending");
  expect(activeSession.steps.A10.status).toBe("quality_failed");
});

it("多参数应用失败保留草稿和原完成状态", async () => {
  activeSession = makeSession();
  for (const stage of acquisition.slice(0, 13)) activeSession.steps[stage.id].status = "completed";
  const normal = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url.endsWith("session-test/parameters")) throw new Error("参数保存失败");
    return normal(url, options);
  });
  renderPage();
  const scan = await openParameters("A12");
  fireEvent.change(scan.getByLabelText("恢复温控等待时间 (s)"), { target: { value: "3" } });
  fireEvent.change(scan.getByLabelText("每点采集时长 (s)"), { target: { value: "2" } });
  expect(screen.getAllByText("应用草稿后预计需重跑；当前执行记录尚未改变。")).toHaveLength(6);
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  expect(await screen.findByText("Error: 参数保存失败")).toBeVisible();
  expect(scan.getByLabelText("恢复温控等待时间 (s)")).toHaveValue(3);
  expect(scan.getByLabelText("每点采集时长 (s)")).toHaveValue(2);
  expect(activeSession.steps.A12.status).toBe("completed");
  expect(activeSession.parameters.TEMP_SWITCH_ON_SETTLE_S).toBe(2);
  expect(screen.getAllByText("应用草稿后预计需重跑；当前执行记录尚未改变。")).toHaveLength(6);
});
