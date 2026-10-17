import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./DetectionChainFrequencyResponsePage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));
const mockApi = vi.mocked(api);
const base = "/api/experiments/detection-chain-frequency-response";

const fields = fixture.schema.fields;
const acquisition = fixture.flow.acquisition;
const analysisStages = fixture.flow.analysis;
const parameterImpacts = fixture.flow.parameter_impacts;
const flow = fixture.flow;
let activeSession: any;
const makeSession = () => ({ session_id: "session-test", job_id: "session-job", revision: 0,
  status: "waiting", parameters: Object.fromEntries(fields.map((field) => [field.name, field.default])),
  steps: Object.fromEntries(acquisition.map((stage) => [stage.id, { status: "pending", error: "" }])),
  outputs: { "AOM 载波": "AM·EXT", "AOM AM": "OFF", 温控: "ON", "Pump 光": "ON", "Probe 光": "ON" },
  pending_request: null });
let summary: any;
let preflightFailure: boolean;
let applyFailure: boolean;
let savedLayout: { basic: string[]; advanced: string[] };
let savedValues: Record<string, number | string>;

const renderPage = () => render(
  <MemoryRouter initialEntries={["/experiments/detection-chain-frequency-response"]}>
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
  preflightFailure = false;
  applyFailure = false;
  savedLayout = {
    basic: fields.filter((field) => field.group === "basic").map((field) => field.name),
    advanced: fields.filter((field) => field.group === "advanced").map((field) => field.name),
  };
  savedValues = Object.fromEntries(fields.map(field => [field.name, field.default]));
  summary = {
    run_id: "0924_1200_freq_resp", timestamp: "2026-09-24T12:00:00", run_tag: "saved-tag",
    completion_status: "completed", analysis_status: "completed", failure_reason: "", analysis_error: "",
    parameters: { RUN_TAG: "saved-tag" }, artifacts: { "frequency_response.png": "/plot.png" },
    temperature_control: { control_source: "external_software", actual_temperature_c: null },
  };
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === base) return { title: "探测链路频率响应标定", description: "探测链路标定", wiring_notes: [] };
    if (url === `${base}/schema`) return { ...fixture.schema,
      fields: fields.map(field => ({ ...field, default: savedValues[field.name] })), parameter_layout: savedLayout };
    if (url === `${base}/defaults` && options?.method === "PUT") {
      const body = JSON.parse(String(options.body));
      savedLayout = body.parameter_layout;
      savedValues = body.parameters;
      return { schema: { ...fixture.schema, fields: fields.map(field => ({ ...field,
        default: body.parameters[field.name] })), parameter_layout: savedLayout } };
    }
    if (url === "/api/jobs") return [];
    if (url === `${base}/workflow`) return flow;
    if (url === `${base}/sessions/active`) return activeSession;
    if (url === "/api/jobs/session-job") return { id: "session-job", status: "running", events: [] };
    if (url === `${base}/sessions` && options?.method === "POST") return activeSession = makeSession();
    if (url === `${base}/sessions/session-test/parameters`) {
      if (applyFailure) throw new Error("验收应用失败");
      const body = JSON.parse(String(options?.body));
      const changes = Object.keys(body.parameters).filter(key => body.parameters[key] !== activeSession.parameters[key]);
      const impacts = parameterImpacts.acquisition as Record<string, string[]>;
      const stale = new Set(changes.flatMap(key => impacts[key] ?? []));
      activeSession = { ...activeSession, revision: activeSession.revision + 1, parameters: body.parameters,
        steps: Object.fromEntries(Object.entries(activeSession.steps).map(([id, state]) => [id,
          stale.has(id) ? { status: "stale", error: "" } : state])) };
      return activeSession;
    }
    if (url === `${base}/sessions/session-test/steps`) {
      const body = JSON.parse(String(options?.body));
      for (const stage of acquisition.filter(stage => body.step_ids.includes(stage.id))) {
        const missing = stage.dependencies.filter(id => !body.step_ids.includes(id)
          && activeSession.steps[id].status !== "completed");
        if (missing.length) throw new Error(`缺少前置：${missing.map(id =>
          `${id} ${acquisition.find(item => item.id === id)?.title}`).join("、")}`);
      }
      activeSession = { ...activeSession, revision: activeSession.revision + 1,
        steps: { ...activeSession.steps, ...Object.fromEntries(body.step_ids.map((id: string) =>
          [id, { status: "completed", error: "" }])) } };
      return activeSession;
    }
    if (url === `${base}/sessions/session-test/finish`)
      return activeSession = { ...activeSession, status: "closed", revision: activeSession.revision + 1,
        steps: { ...activeSession.steps, A08: { status: "completed", error: "" } } };
    if (url.endsWith("/analysis") && options?.method === "POST") return { id: "analysis", status: "running", events: [] };
    if (url.endsWith("/analysis")) return { run_id: decodeURIComponent(url.split("/").at(-2)!), revision: 0,
      steps: Object.fromEntries(analysisStages.map((stage) => [stage.id, { status: "pending", error: "" }])),
      options: {}, summary: {}, fingerprint: {} };
    if (url.startsWith(`${base}/runs?`)) return { runs: [summary], total: 1 };
    if (url.endsWith("/summary")) return summary;
    if (url === `${base}/preflight`) return preflightFailure
      ? { ok: false, errors: ["频率范围无效"] } : { ok: true, errors: [] };
    if (url === `${base}/runs` && options?.method === "POST") return { id: "acquisition", status: "running", events: [] };
    if (url.endsWith("/analyze")) return { id: "analysis", status: "running", events: [] };
    throw new Error(`未模拟 ${url}`);
  });
});

afterEach(() => { cleanup(); mockApi.mockReset(); });

it("按真实路由显示双 Tab，节点内编辑保留并从历史填回，预检失败不启动", async () => {
  renderPage();
  const tabs = screen.getByRole("tablist", { name: "探测链路频率响应标定视图" });
  expect(within(tabs).getAllByRole("tab")).toHaveLength(2);
  const parameters = await openParameters("A07");
  fireEvent.change(parameters.getByLabelText("运行标签"), { target: { value: "edited" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await within(screen.getByRole("tabpanel", { name: "历史" })).findByAltText("频率响应曲线")).toHaveAttribute("src", "/plot.png");
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByText(/外部温控：程序未设置或检测温度/)).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect((await openParameters("A07")).getByLabelText("运行标签")).toHaveValue("edited");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect((await openParameters("A07")).getByLabelText("运行标签")).toHaveValue("saved-tag");
  preflightFailure = true;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("频率范围无效")).toBeVisible();
  await waitFor(() => expect(mockApi.mock.calls.some(([url, init]) => url === `${base}/runs` && init?.method === "POST")).toBe(false));
});

it("两列流程来自后端定义，缺少前置时拒绝且不自动补齐选择", async () => {
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  const nodes = screen.getByRole("region", { name: "交互实验流程图" });
  await waitFor(() => expect(within(nodes).getByLabelText("A03 参考时钟同步")).toBeEnabled());
  fireEvent.click(within(nodes).getByLabelText("A03 参考时钟同步"));
  await waitFor(() => expect(within(nodes).getByRole("button", { name: "运行所选 / 重跑" })).toBeEnabled());
  fireEvent.click(within(nodes).getByRole("button", { name: "运行所选 / 重跑" }));
  expect(await screen.findByText("Error: 缺少前置：A02 设备连接")).toBeVisible();
  expect(within(nodes).getByLabelText("A02 设备连接")).not.toBeChecked();
  const request = mockApi.mock.calls.find(([url]) => url.endsWith("session-test/steps"));
  expect(JSON.parse(String(request?.[1]?.body)).step_ids).toEqual(["A03"]);
  expect(within(nodes).getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${base}/workflow/diagram`);
  expect(within(nodes).getByRole("heading", { name: "采集流程" })).toBeVisible();
  expect(within(nodes).getByRole("heading", { name: "分析流程" })).toBeVisible();
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
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) =>
    url.endsWith(`${summary.run_id}/analysis`) && options?.method === "POST")).toBe(true));
  const request = mockApi.mock.calls.find(([url, options]) => url.endsWith(`${summary.run_id}/analysis`) && options?.method === "POST");
  expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({ revision: 0, step_ids: ["B01", "B02", "B03"], analysis_parameters: {} });
});

it("仅保留运行与历史，交互流程图保留且两个主 Tab 支持双向循环", async () => {
  renderPage();
  const tablist = await screen.findByRole("tablist", { name: "探测链路频率响应标定视图" });
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

it("真实schema全部可见参数均可节点编辑，初始化参数只出现一次且高级默认折叠", async () => {
  renderPage();
  const visited = new Set<string>();
  for (const stage of acquisition.filter(item => item.display_parameters.length)) {
    const parameters = await openParameters(stage.id);
    const region = screen.getByLabelText(`${stage.id} ${stage.title} 参数`);
    const advanced = region.querySelector<HTMLDetailsElement>(".zaw-parameter-advanced");
    if (advanced) {
      expect(advanced.open).toBe(false);
      fireEvent.click(advanced.querySelector("summary")!);
    }
    for (const name of stage.display_parameters) {
      const field = fields.find(item => item.name === name)!;
      const label = field.unit ? `${field.label} (${field.unit})` : field.label;
      expect(parameters.getByLabelText(label)).toBeEnabled();
      visited.add(name);
    }
  }
  expect([...visited].sort()).toEqual(fields.map(field => field.name).sort());
  expect(screen.getAllByLabelText("气室温度 (°C)")).toHaveLength(1);
  expect(screen.queryByLabelText(/旧版解调时间常数/)).not.toBeInTheDocument();
  expect(document.querySelectorAll(".xy-node-parameters[open]")).toHaveLength(4);
});

it("多参数草稿预览等于真实失效映射，还原清除预览，应用失败保留草稿并可重试", async () => {
  activeSession = makeSession();
  for (const stage of acquisition.filter(item => item.id !== "A08"))
    activeSession.steps[stage.id] = { status: "completed", error: "" };
  renderPage();
  await waitFor(() => expect(screen.getByText(/等待下一步/)).toBeVisible());
  const a07 = await openParameters("A07");
  const a05 = await openParameters("A05");
  const reference = "锁相放大器 解调参考频率 (Hz)";
  fireEvent.change(a07.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.change(a05.getByLabelText(reference), { target: { value: "91000" } });
  expect(await screen.findByText(/参数草稿尚未应用/)).toBeVisible();
  expect(document.querySelectorAll(".xy-flow-node.draft-affected")).toHaveLength(3);
  expect(activeSession.steps.A05.status).toBe("completed");
  fireEvent.change(a07.getByLabelText("运行标签"), { target: { value: fields.find(item => item.name === "RUN_TAG")!.default } });
  fireEvent.change(a05.getByLabelText(reference), { target: { value: "90000" } });
  await waitFor(() => expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument());
  expect(document.querySelectorAll(".xy-flow-node.draft-affected")).toHaveLength(0);
  fireEvent.change(a07.getByLabelText("运行标签"), { target: { value: "retry" } });
  applyFailure = true;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  expect(await screen.findByText("Error: 验收应用失败")).toBeVisible();
  expect(a07.getByLabelText("运行标签")).toHaveValue("retry");
  expect(activeSession.steps.A07.status).toBe("completed");
  applyFailure = false;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument());
  expect(activeSession.steps.A07.status).toBe("stale");
  expect(activeSession.steps.A05.status).toBe("completed");
  expect(mockApi.mock.calls.filter(([url]) => url.endsWith("session-test/steps"))).toHaveLength(0);
});

it("分类保存回读保留值，历史分析无覆盖参数且不会污染下一次默认值", async () => {
  renderPage();
  const parameters = await openParameters("A07");
  fireEvent.change(parameters.getByLabelText("运行标签"), { target: { value: "next-default" } });
  fireEvent.click(parameters.getByRole("button", { name: "运行标签 移至高级参数" }));
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: summary.run_id } });
  await waitFor(() => expect(screen.getByRole("button", { name: "选择全部分析步骤" })).toBeEnabled());
  for (const stage of analysisStages) expect(screen.queryByLabelText(`${stage.id} ${stage.title} 参数`)).not.toBeInTheDocument();
  const flowRegion = screen.getByRole("region", { name: "交互实验流程图" });
  fireEvent.click(within(flowRegion).getByRole("button", { name: "保存参数与布局" }));
  expect(await screen.findByText("参数与布局已保存")).toBeVisible();
  expect(savedValues.RUN_TAG).toBe("next-default");
  expect(summary.parameters.RUN_TAG).toBe("saved-tag");
  expect(savedLayout.advanced).toContain("RUN_TAG");
  expect(savedLayout.basic).not.toContain("RUN_TAG");
  cleanup();
  renderPage();
  const loaded = await openParameters("A07");
  const region = screen.getByLabelText("A07 频率扫描采集 参数");
  fireEvent.click(region.querySelector(".zaw-parameter-advanced summary")!);
  expect(loaded.getByLabelText("运行标签")).toHaveValue("next-default");
  expect(loaded.getByRole("button", { name: "运行标签 移至基础参数" })).toBeVisible();
});

it("刷新恢复真实会话，结束后保留完成记录，新草稿不会污染旧执行状态", async () => {
  activeSession = makeSession();
  activeSession.parameters.RUN_TAG = "recovered";
  activeSession.steps.A07 = { status: "completed", error: "" };
  renderPage();
  await waitFor(() => expect(screen.getByText(/会话 session-/)).toBeVisible());
  expect((await openParameters("A07")).getByLabelText("运行标签")).toHaveValue("recovered");
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  expect(await screen.findByText(/下方状态为已结束会话的执行记录/)).toBeVisible();
  expect(screen.queryByText(/等待下一步/)).not.toBeInTheDocument();
  expect(activeSession.steps.A08.status).toBe("completed");
  fireEvent.change((await openParameters("A07")).getByLabelText("运行标签"), { target: { value: "next-run" } });
  expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument();
  expect(document.querySelectorAll(".xy-flow-node.draft-affected")).toHaveLength(0);
  expect(activeSession.parameters.RUN_TAG).toBe("recovered");
});

it("历史旧等待键填回由模型元数据归一，只影响下一次表单且不保存或启动", async () => {
  summary.parameters = { RUN_TAG: "legacy", AM_SETTLE_TIME_S: 0.2, TEMP_SWITCH_OFF_LEAD_S: 2.5 };
  renderPage();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  const parameters = await openParameters("A07");
  expect(parameters.getByLabelText("运行标签")).toHaveValue("legacy");
  expect(parameters.getByLabelText("系统稳定时间 (s)")).toHaveValue(2.5);
  expect(mockApi.mock.calls.filter(([, init]) => init?.method)).toHaveLength(0);
});

it("真实执行中的会话禁用节点参数、保存和结束操作", async () => {
  activeSession = { ...makeSession(), status: "running", current_step: "A07", pending_request: "request" };
  activeSession.steps.A07 = { status: "running", error: "" };
  renderPage();
  const parameters = await openParameters("A07");
  await waitFor(() => expect(parameters.getByLabelText("运行标签")).toBeDisabled());
  expect(screen.getByRole("button", { name: "保存参数与布局" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "结束会话" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "应用参数到会话" })).toBeDisabled();
});

it("分析质量失败显示真实诊断，并拒绝单独执行缺少有效前置的绘图", async () => {
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/analysis")) {
      if (options?.method === "POST") throw new Error("缺少前置：B02 归一化响应与斜率对照");
      return { run_id: summary.run_id, revision: 2, options: {}, fingerprint: {},
        summary: { status: "quality_failed", error: "无有效谱峰点，无法归一化" },
        steps: { B01: { status: "completed", error: "" },
          B02: { status: "quality_failed", error: "无有效谱峰点，无法归一化" },
          B03: { status: "pending", error: "" } } };
    }
    return original(url, options);
  });
  renderPage();
  fireEvent.change(await screen.findByLabelText("分析操作对象"), { target: { value: summary.run_id } });
  expect(await screen.findByText("无有效谱峰点，无法归一化")).toBeVisible();
  expect(screen.getByText("质量未通过")).toBeVisible();
  fireEvent.click(screen.getByLabelText("B03 响应曲线绘图与汇总"));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  expect(await screen.findByText("Error: 缺少前置：B02 归一化响应与斜率对照")).toBeVisible();
  expect(mockApi.mock.calls.filter(([url]) => url.endsWith("session-test/steps"))).toHaveLength(0);
});
