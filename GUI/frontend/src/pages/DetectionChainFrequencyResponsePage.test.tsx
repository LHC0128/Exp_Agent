import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));
const mockApi = vi.mocked(api);
const base = "/api/experiments/detection-chain-frequency-response";

const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "freq_resp", group: "basic", unit: "" },
  { name: "AM_FREQ_START_HZ", label: "AM 调制起始频率", type: "number", default: 50000, group: "basic", unit: "Hz" },
  { name: "HF2_OSC_FREQ_HZ", label: "解调参考频率", type: "number", default: 90000, group: "basic", unit: "Hz" },
  { name: "FIXED_PARAMS.Pump_laser_power", label: "Pump 光功率", type: "number", default: 0, group: "basic", unit: "V" },
  { name: "PROBE_AOM_CARRIER_FREQ_HZ", label: "载波频率", type: "number", default: 100000000, group: "advanced", unit: "Hz" },
];
const displayA: Record<string, string[]> = {
  A04: ["FIXED_PARAMS.Pump_laser_power"],
  A05: ["HF2_OSC_FREQ_HZ"],
  A06: ["PROBE_AOM_CARRIER_FREQ_HZ"],
  A07: ["RUN_TAG", "AM_FREQ_START_HZ"],
};
const acquisition = [
  ["A01", "启动预检", "启动准备"], ["A02", "设备连接", "启动准备"], ["A03", "参考时钟同步", "启动准备"],
  ["A04", "初始输出与温控", "初始条件"], ["A05", "HF2 解调配置", "激励与解调"],
  ["A06", "AOM 载波与外部 AM", "激励与解调"], ["A07", "频率扫描采集", "扫描与结束"],
  ["A08", "安全收尾 / 结束会话", "扫描与结束"],
].map(([id, title, group], index, stages) => ({ id, title, group,
  dependencies: index && id !== "A08" ? [stages[index - 1][0]] : [], parameters: [],
  display_parameters: displayA[id] ?? [], artifacts: [] }));
const analysisStages = ["加载配置与准备分析", "归一化响应与斜率对照", "响应曲线绘图与汇总"]
  .map((title, index) => ({ id: `B0${index + 1}`, title, group: title, dependencies: index ? [`B0${index}`] : [],
    parameters: [], display_parameters: [], artifacts: [] }));
const parameterImpacts = {
  acquisition: {
    RUN_TAG: ["A07"], AM_FREQ_START_HZ: ["A07"], HF2_OSC_FREQ_HZ: ["A05", "A06", "A07"],
    "FIXED_PARAMS.Pump_laser_power": ["A04", "A05", "A06", "A07"],
  } as Record<string, string[]>,
  analysis: {},
};
const flow = { acquisition, analysis: analysisStages, parameter_impacts: parameterImpacts,
  diagram_url: `${base}/workflow/diagram` };
let activeSession: any;
const makeSession = () => ({ session_id: "session-test", job_id: "session-job", revision: 0,
  status: "waiting", parameters: Object.fromEntries(fields.map((field) => [field.name, field.default])),
  steps: Object.fromEntries(acquisition.map((stage) => [stage.id, { status: "pending", error: "" }])),
  outputs: { "AOM 载波": "AM·EXT", "AOM AM": "OFF", 温控: "ON", "Pump 光": "ON", "Probe 光": "ON" },
  pending_request: null });
let summary: any;
let preflightFailure: boolean;

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
  summary = {
    run_id: "0924_1200_freq_resp", timestamp: "2026-09-24T12:00:00", run_tag: "saved-tag",
    completion_status: "completed", analysis_status: "completed", failure_reason: "", analysis_error: "",
    parameters: { RUN_TAG: "saved-tag" }, artifacts: { "frequency_response.png": "/plot.png" },
    temperature_control: { control_source: "external_software", actual_temperature_c: null },
  };
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === base) return { title: "探测链路频率响应标定", description: "探测链路标定", wiring_notes: [] };
    if (url === `${base}/schema`) return { fields, parameter_layout: {
      basic: fields.filter((field) => field.group === "basic").map((field) => field.name),
      advanced: fields.filter((field) => field.group === "advanced").map((field) => field.name) } };
    if (url === "/api/jobs") return [];
    if (url === `${base}/workflow`) return flow;
    if (url === `${base}/sessions/active`) return activeSession;
    if (url === "/api/jobs/session-job") return { id: "session-job", status: "running", events: [] };
    if (url === `${base}/sessions` && options?.method === "POST") return activeSession = makeSession();
    if (url === `${base}/sessions/session-test/steps`) {
      const body = JSON.parse(String(options?.body));
      if (body.step_ids.includes("A03") && !body.step_ids.includes("A02")
          && activeSession.steps.A02.status !== "completed") throw new Error("缺少前置：A02 设备连接");
      activeSession = { ...activeSession, revision: activeSession.revision + 1,
        steps: { ...activeSession.steps, ...Object.fromEntries(body.step_ids.map((id: string) =>
          [id, { status: "completed", error: "" }])) } };
      return activeSession;
    }
    if (url === `${base}/sessions/session-test/finish`)
      return activeSession = { ...activeSession, status: "closed", revision: activeSession.revision + 1 };
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

it("按真实路由显示三 Tab，节点内编辑保留并从历史填回，预检失败不启动", async () => {
  renderPage();
  const tabs = screen.getByRole("tablist", { name: "探测链路频率响应标定视图" });
  expect(within(tabs).getAllByRole("tab")).toHaveLength(3);
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

it("架构 Tab 内嵌总览与子图切换，可在新页面打开", async () => {
  renderPage();
  const tablist = await screen.findByRole("tablist", { name: "探测链路频率响应标定视图" });
  fireEvent.click(within(tablist).getByRole("tab", { name: "架构" }));
  const panel = screen.getByRole("tabpanel", { name: "架构" });
  const frame = within(panel).getByTitle("探测链路频率响应标定实验总架构图");
  expect(frame).toHaveAttribute("src", "/architecture/detection-chain-frequency-response.html");
  expect(within(panel).getByRole("link", { name: "在新页面打开" })).toHaveAttribute("href", "/architecture/detection-chain-frequency-response.html");
  fireEvent.click(within(panel).getByRole("tab", { name: "采集流程" }));
  expect(within(panel).getByTitle("探测链路频率响应标定实验采集执行流程图")).toHaveAttribute("src", "/architecture/detection-chain-frequency-response.acquisition.html");
  fireEvent.click(within(panel).getByRole("tab", { name: "单点时序" }));
  expect(within(panel).getByTitle("探测链路频率响应标定实验单频点采集时序图")).toHaveAttribute("src", "/architecture/detection-chain-frequency-response.timing.html");
  fireEvent.click(within(panel).getByRole("tab", { name: "数据分析" }));
  expect(within(panel).getByTitle("探测链路频率响应标定实验数据与分析图")).toHaveAttribute("src", "/architecture/detection-chain-frequency-response.dataflow.html");
  // 键盘导航在三个 Tab 间循环：外层切回“运行”后左移回绕到架构，再右移回运行。
  const runTab = within(tablist).getByRole("tab", { name: "运行" });
  fireEvent.click(runTab);
  runTab.focus();
  fireEvent.keyDown(tablist, { key: "ArrowLeft" });
  expect(within(tablist).getByRole("tab", { name: "架构" })).toHaveFocus();
  fireEvent.keyDown(tablist, { key: "ArrowRight" });
  expect(runTab).toHaveFocus();
});
