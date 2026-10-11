import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false }) }));
vi.mock("../components/JobView", () => ({
  JobView: ({ job, onUpdate }: any) => job
    ? <button onClick={() => onUpdate({ ...job, status: "completed" })}>完成测试任务</button>
    : null,
}));

const mockApi = vi.mocked(api);
const BASE = "/api/experiments/noise-spectrum-xy";
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "noise", group: "basic", unit: "" },
  { name: "TARGET_NOISE_FREQ_POINTS", label: "目标频率扫描点数", type: "integer", default: 500, group: "basic", unit: "" },
  { name: "HF2_DAQ_DURATION", label: "每点采集时长", type: "number", default: 1, group: "basic", unit: "s" },
  { name: "ANALYSIS_BIN_WIDTH_HZ", label: "频率格间距", type: "number", default: 12, group: "basic", unit: "Hz" },
];
let summary: any;
let activeSession: any;
const acquisition = [
  ["A01", "启动预检", "启动准备"], ["A02", "设备连接", "启动准备"],
  ["A03", "参考时钟同步", "启动准备"], ["A04", "初始输出设置", "初始条件"],
  ["A05", "温度稳定", "初始条件"], ["A06", "Pump 配置", "激励与触发"],
  ["A07", "共用触发配置", "激励与触发"], ["A08", "Demod0 配置与校相", "相位校准"],
  ["A09", "XY 正弦与 Burst 初始化", "相位校准"], ["A10", "XY 迭代校相", "相位校准"],
  ["A11", "DAQ 准备", "扫描与结束"], ["A12", "扫描采集", "扫描与结束"],
  ["A13", "解调器恢复", "扫描与结束"], ["A14", "安全收尾 / 结束会话", "扫描与结束"],
].map(([id, title, group], index, stages) => ({ id, title, group,
  dependencies: index && id !== "A14" ? [stages[index - 1][0]] : [], parameters: [], artifacts: [] }));
const analysisStages = ["加载配置与准备分析", "计算 Welch PSD", "移动脊线重标定", "全控制轴拟合与背景验收", "Mx 对照与分析汇总"]
  .map((title, index) => ({ id: `B0${index + 1}`, title, group: title, dependencies: index ? [`B0${index}`] : [], parameters: [], artifacts: [] }));
const makeSession = () => ({ session_id: "session-test", job_id: "session-job", revision: 0,
  status: "waiting", parameters: Object.fromEntries(fields.map((field) => [field.name, field.default])),
  steps: Object.fromEntries(acquisition.map((stage) => [stage.id, { status: "pending", error: "" }])),
  outputs: { X: "ON", Y: "ON", 温控: "ON", 共用触发: "ON" }, pending_request: null });

const renderPage = () => render(
  <MemoryRouter initialEntries={["/experiments/noise-spectrum-xy"]}>
    <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
  </MemoryRouter>,
);

beforeEach(() => {
  activeSession = null;
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
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: { basic: fields.map((field) => field.name), advanced: [] } };
    if (url === "/api/jobs") return [];
    if (url === `${BASE}/workflow`) return { acquisition, analysis: analysisStages, diagram_url: `${BASE}/workflow/diagram` };
    if (url === `${BASE}/sessions/active`) return activeSession;
    if (url === "/api/jobs/session-job") return { id: "session-job", kind: "session:noise-spectrum-xy", status: "running", events: [] };
    if (url === `${BASE}/sessions` && options?.method === "POST") return activeSession = makeSession();
    if (url === `${BASE}/sessions/session-test/parameters`) {
      activeSession = { ...activeSession, revision: activeSession.revision + 1, parameters: JSON.parse(String(options?.body)).parameters,
        steps: { ...activeSession.steps, A12: { status: "stale", error: "" } } };
      return activeSession;
    }
    if (url === `${BASE}/sessions/session-test/steps`) {
      const body = JSON.parse(String(options?.body));
      if (body.step_ids.includes("A03") && !body.step_ids.includes("A02") && activeSession.steps.A02.status !== "completed") throw new Error("缺少前置：A02 设备连接");
      activeSession = { ...activeSession, revision: activeSession.revision + 1,
        steps: { ...activeSession.steps, ...Object.fromEntries(body.step_ids.map((id: string) => [id, { status: "completed", error: "" }])) } };
      return activeSession;
    }
    if (url === `${BASE}/sessions/session-test/finish`) return activeSession = { ...activeSession, status: "closed", revision: activeSession.revision + 1 };
    if (url.endsWith("/analysis") && options?.method === "POST") return { id: "analysis", status: "running", events: [] };
    if (url.endsWith("/analysis")) return { run_id: summary.run_id, revision: 0, steps: Object.fromEntries(analysisStages.map((stage) => [stage.id, { status: "pending", error: "" }])), options: { bin_width_hz: 12, frequency_min_hz: 5, frequency_max_hz: 50000, fit_half_width_hz: 3000 }, summary: {}, fingerprint: {} };
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary], total: 1 };
    if (url.endsWith("/summary")) return summary;
    if (url === `${BASE}/preflight`) return { ok: true, errors: [] };
    if (url === `${BASE}/derive`) {
      const values = JSON.parse(String(options?.body)).parameters;
      return { values: values.HF2_DAQ_DURATION < .1 ? { error: "每点采集时长不足一个 Welch 分段" } : {
        nperseg: 37500, segments_per_point: values.HF2_DAQ_DURATION === 5 ? 119 : 23,
        bin_width_hz: 12, scan_seconds: 1560,
      } };
    }
    if (url === `${BASE}/defaults`) return { message: "参数与布局已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: JSON.parse(String(options?.body)).parameter_layout } };
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
  renderPage();
  await waitFor(() => expect(screen.getByLabelText("运行标签")).toHaveValue("existing"));
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
  await waitFor(() => expect(screen.getByRole("button", { name: "选择全部分析步骤" })).toBeEnabled());
  fireEvent.change(await screen.findByLabelText("分析运行目录"), { target: { value: summary.run_id } });
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
  await screen.findByLabelText("每点采集时长 (s)");
  expect(screen.queryByLabelText("Welch 每段点数")).not.toBeInTheDocument();
  expect(await screen.findByText(/每点平均段数：23（50%/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("每点采集时长 (s)"), { target: { value: "5" } });
  expect(await screen.findByText(/每点平均段数：119（50%/)).toBeVisible();
  const panel = screen.getByRole("tabpanel", { name: "运行" });
  expect(within(panel).getByText(/实际 Welch 每段点数：38378/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("每点采集时长 (s)"), { target: { value: "0.01" } });
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
  expect(within(screen.getByRole("tablist", { name: "XY 正弦控制噪声谱实验视图" })).getAllByRole("tab")).toHaveLength(3);
  expect(await within(screen.getByRole("tabpanel", { name: "运行" })).findByAltText("提取的可控与不可控噪声谱")).toHaveAttribute("src", "/noise.png?v=1");
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

it("架构 Tab 内嵌架构图 iframe，可在新页面打开，方向键在三个视图间循环", async () => {
  renderPage();
  const tablist = screen.getByRole("tablist", { name: "XY 正弦控制噪声谱实验视图" });
  fireEvent.click(within(tablist).getByRole("tab", { name: "架构" }));
  const panel = screen.getByRole("tabpanel", { name: "架构" });
  const frame = within(panel).getByTitle("XY 正弦控制噪声谱实验采集流程架构图");
  expect(frame).toHaveAttribute("src", "/architecture/noise-spectrum-xy.html");
  expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-same-origin allow-downloads allow-popups");
  expect(within(panel).getByRole("link", { name: "在新页面打开" })).toHaveAttribute("href", "/architecture/noise-spectrum-xy.html");

  const runTab = within(tablist).getByRole("tab", { name: "运行" });
  runTab.focus();
  fireEvent.keyDown(tablist, { key: "ArrowLeft" });
  const archTab = within(tablist).getByRole("tab", { name: "架构" });
  expect(archTab).toHaveFocus();
  expect(archTab).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(tablist, { key: "ArrowRight" });
  expect(within(tablist).getByRole("tab", { name: "运行" })).toHaveFocus();
});

it("预检失败不启动采集，历史重新分析完成后刷新实际结果图", async () => {
  const normal = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/preflight`) return { ok: false, errors: ["目标频率范围无效"] };
    return normal(url, options);
  });
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "预检并运行实验" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("目标频率范围无效")).toBeVisible();
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);

  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "重新分析" }));
  summary = { ...summary, artifacts: { "noise_spectra_extracted.png": "/noise.png?v=2" } };
  fireEvent.click(await screen.findByRole("button", { name: "完成测试任务" }));
  await waitFor(() => expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByAltText("提取的可控与不可控噪声谱")).toHaveAttribute("src", "/noise.png?v=2"));
});
