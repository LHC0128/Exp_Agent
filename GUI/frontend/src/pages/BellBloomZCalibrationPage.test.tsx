import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false }) }));
vi.mock("../components/JobView", () => ({ JobView: ({ job, onUpdate }: any) => job ? <button onClick={() => onUpdate({ ...job, status: "completed" })}>完成测试任务</button> : null }));
const mockApi = vi.mocked(api);
const BASE = "/api/experiments/bell-bloom-z-field-calibration";
let summary: any;
let currentSession: any;
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "bb_z_cal", group: "basic", unit: "" },
  { name: "PUMP_MOD_DUTY", label: "Pump 占空比", type: "number", default: 5, group: "basic", unit: "%" },
  { name: "SYSTEM_SETTLE_S", label: "系统稳定时间", type: "number", default: 0.1, group: "basic", unit: "s" },
  { name: "LINEAR_R_SQUARED_MIN", label: "线性标定最低 R²", type: "number", default: 0.8, group: "basic", unit: "" },
];
const flow = {"acquisition": [{"id": "A01", "title": "启动预检", "group": "准备", "dependencies": [], "parameters": [], "artifacts": [], "display_parameters": [], "quality_gates": ["参数、映射占用、DG4000 门控方案与锁相扫频能力全部通过"]}, {"id": "A02", "title": "设备连接与输入初态", "group": "准备", "dependencies": ["A01"], "parameters": ["RUN_TAG"], "artifacts": [], "display_parameters": ["RUN_TAG"], "quality_gates": []}, {"id": "A03", "title": "参考时钟同步与严格回读", "group": "准备", "dependencies": ["A02"], "parameters": [], "artifacts": [], "display_parameters": [], "quality_gates": ["全部已连接时钟设备设置后严格回读一致"]}, {"id": "A04", "title": "辅助场归零、固定主场与光功率", "group": "初始条件", "dependencies": ["A03"], "parameters": ["FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power", "FIXED_PARAMS.main_magnetic_field"], "artifacts": [], "display_parameters": ["FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power", "FIXED_PARAMS.main_magnetic_field"], "quality_gates": []}, {"id": "A05", "title": "TEC 目标温度", "group": "初始条件", "dependencies": ["A04"], "parameters": ["FIXED_PARAMS.temperature"], "artifacts": [], "display_parameters": ["FIXED_PARAMS.temperature"], "quality_gates": []}, {"id": "A06", "title": "100 MHz 外部门控载波与 Pump 脉冲", "group": "激励", "dependencies": ["A05"], "parameters": ["ZERO_BIAS_CENTER_FREQUENCY_HZ", "Z_INITIAL_HZ_PER_V", "Z_BIAS_START_V", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_MOD_DUTY"], "artifacts": [], "display_parameters": ["ZERO_BIAS_CENTER_FREQUENCY_HZ", "Z_INITIAL_HZ_PER_V", "Z_BIAS_START_V", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_MOD_DUTY"], "quality_gates": ["外部门控脉冲频率和脉宽回读匹配，保持占空比"]}, {"id": "A07", "title": "Demod0 R 解调与实际采样率", "group": "激励", "dependencies": ["A06"], "parameters": ["ZERO_BIAS_CENTER_FREQUENCY_HZ", "Z_INITIAL_HZ_PER_V", "Z_BIAS_START_V", "HF2_SIGNAL_RANGE_V", "RESPONSE_RATE_SA_S", "FREQUENCY_DURATION_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER"], "artifacts": [], "display_parameters": ["ZERO_BIAS_CENTER_FREQUENCY_HZ", "Z_INITIAL_HZ_PER_V", "Z_BIAS_START_V", "HF2_SIGNAL_RANGE_V", "RESPONSE_RATE_SA_S", "FREQUENCY_DURATION_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER"], "quality_gates": ["Demod0 R 实际采样率每频点至少八个样本，解调频率回读一致"]}, {"id": "A08", "title": "Z DC × Pump 调制与解调扫频", "group": "采集", "dependencies": ["A07"], "parameters": ["ZERO_BIAS_CENTER_FREQUENCY_HZ", "Z_INITIAL_HZ_PER_V", "Z_BIAS_START_V", "Z_BIAS_STOP_V", "Z_BIAS_STEP_V", "FREQUENCY_HALF_WIDTH_HZ", "FREQUENCY_STEP_HZ", "PUMP_MOD_DUTY", "FREQUENCY_DURATION_S", "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S", "R_BAD_POINT_STD_THRESHOLD_V", "R_POINT_MAX_ATTEMPTS"], "artifacts": ["raw/z_scan_index.npz", "raw/z_*/*.npz"], "display_parameters": ["ZERO_BIAS_CENTER_FREQUENCY_HZ", "Z_INITIAL_HZ_PER_V", "Z_BIAS_START_V", "Z_BIAS_STOP_V", "Z_BIAS_STEP_V", "FREQUENCY_HALF_WIDTH_HZ", "FREQUENCY_STEP_HZ", "PUMP_MOD_DUTY", "FREQUENCY_DURATION_S", "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S", "R_BAD_POINT_STD_THRESHOLD_V", "R_POINT_MAX_ATTEMPTS"], "quality_gates": ["Z 为 DC 且输出开启；门控与解调同步改频并回读", "每次 R 标准差不超过阈值；重采仍失败立即阻断后继并保留全部尝试", "温控先恢复，随后保存频点；索引只纳入完整 Z 频扫"]}, {"id": "A09", "title": "零偏参考恢复与本轮封存", "group": "结束", "dependencies": ["A08"], "parameters": [], "artifacts": [], "display_parameters": [], "quality_gates": ["逐项安全恢复成功后才能标记本轮完成"]}, {"id": "A10", "title": "安全结束会话", "group": "结束", "dependencies": [], "parameters": [], "artifacts": [], "display_parameters": [], "quality_gates": []}], "analysis": [{"id": "B01", "title": "配置与原始频扫资格", "group": "独立分析", "dependencies": [], "parameters": [], "artifacts": [], "display_parameters": [], "quality_gates": ["本实验身份与兼容格式；已结束写入；索引、路径与频扫数组有效"]}, {"id": "B02", "title": "逐 Z 电压 Lorentzian 共振中心", "group": "独立分析", "dependencies": ["B01"], "parameters": [], "artifacts": ["curve_fits.json"], "display_parameters": [], "quality_gates": ["中心须收敛、线宽有效且距边缘至少一个 HWHM；失败曲线保留诊断并排除"]}, {"id": "B03", "title": "加权线性 Z 电压标定", "group": "独立分析", "dependencies": ["B02"], "parameters": ["LINEAR_R_SQUARED_MIN"], "artifacts": ["calibration_evaluation.json"], "display_parameters": ["LINEAR_R_SQUARED_MIN"], "quality_gates": ["至少两个不同电压的有效中心，加权线性 R² 达到保存门槛；不通过仍出具诊断"]}, {"id": "B04", "title": "标定报告与结果图", "group": "独立分析", "dependencies": ["B03"], "parameters": [], "artifacts": ["analysis.yaml", "analysis.json", "calibration_results.npz", "frequency_response_fits.png", "z_frequency_calibration.png", "z_frequency_residuals.png"], "display_parameters": [], "quality_gates": []}], "parameter_impacts": {"acquisition": {"RUN_TAG": ["A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09"], "FIXED_PARAMS.Pump_laser_power": ["A04", "A05", "A06", "A07", "A08", "A09"], "FIXED_PARAMS.Probe_laser_power": ["A04", "A05", "A06", "A07", "A08", "A09"], "FIXED_PARAMS.main_magnetic_field": ["A04", "A05", "A06", "A07", "A08", "A09"], "FIXED_PARAMS.temperature": ["A05", "A06", "A07", "A08", "A09"], "ZERO_BIAS_CENTER_FREQUENCY_HZ": ["A06", "A07", "A08", "A09"], "Z_INITIAL_HZ_PER_V": ["A06", "A07", "A08", "A09"], "Z_BIAS_START_V": ["A06", "A07", "A08", "A09"], "PUMP_CARRIER_AMPLITUDE_VPP": ["A06", "A07", "A08", "A09"], "PUMP_MOD_DUTY": ["A06", "A07", "A08", "A09"], "HF2_SIGNAL_RANGE_V": ["A07", "A08", "A09"], "RESPONSE_RATE_SA_S": ["A07", "A08", "A09"], "FREQUENCY_DURATION_S": ["A07", "A08", "A09"], "RESPONSE_TIME_CONSTANT_S": ["A07", "A08", "A09"], "RESPONSE_DEMOD_ORDER": ["A07", "A08", "A09"], "Z_BIAS_STOP_V": ["A08", "A09"], "Z_BIAS_STEP_V": ["A08", "A09"], "FREQUENCY_HALF_WIDTH_HZ": ["A08", "A09"], "FREQUENCY_STEP_HZ": ["A08", "A09"], "SYSTEM_SETTLE_S": ["A08", "A09"], "TEMP_SWITCH_ON_LAG_S": ["A08", "A09"], "R_BAD_POINT_STD_THRESHOLD_V": ["A08", "A09"], "R_POINT_MAX_ATTEMPTS": ["A08", "A09"]}, "analysis": {"LINEAR_R_SQUARED_MIN": ["B03", "B04"]}}, "diagram_url": "/api/experiments/bell-bloom-z-field-calibration/workflow/diagram"};
const parameters = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const makeSession = () => ({ session_id: "bb-session", job_id: "bb-job", status: "waiting", revision: 0,
  parameters: parameters(), pending_request: null, outputs: { "Pump 脉冲": "ON", 温控: "ON" },
  steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: "pending", error: "" }])) });
const editor = async (id: string) => {
  const stage = [...flow.acquisition, ...flow.analysis].find(stage => stage.id === id)!;
  const region = await screen.findByRole("region", { name: `${id} ${stage.title} 参数`, hidden: true });
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  return within(region);
};
const renderPage = () => render(<MemoryRouter initialEntries={["/experiments/bell-bloom-z-field-calibration"]}><Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);

beforeEach(() => {
  currentSession = null;
  summary = { run_id: "0920_latest", timestamp: "2026-09-20T12:00:00+08:00", run_tag: "test", completion_status: "completed", analysis_status: "completed", parameters: { RUN_TAG: "history-tag" }, failure_reason: "", analysis_error: "", safety_shutdown: {},
    artifacts: { "z_frequency_calibration.png": "/plot.png?v=1" }, analysis: { success: true, K_Z_Hz_per_V: -26000, f_0V_Hz: 90000, linear_fit: { uncertainties: [2, 1], r_squared: 0.999 }, rejection_reasons: [] } };
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === BASE) return { title: "Bell Bloom Z 磁场频率标定", description: "Z 标定", wiring_notes: [] };
    if (url === `${BASE}/schema`) return { fields, parameter_layout: { basic: fields.map(field => field.name), advanced: [] } };
    if (url === "/api/jobs") return [];
    if (url === `${BASE}/workflow`) return flow;
    if (url === `${BASE}/derive`) return { values: {} };
    if (url === `${BASE}/sessions/active`) return currentSession;
    if (url === `${BASE}/sessions` && options?.method === "POST") return currentSession = makeSession();
    if (url === "/api/jobs/bb-job") return { id: "bb-job", kind: "session:bell-bloom-z-field-calibration", status: "running", events: [] };
    if (url === `${BASE}/sessions/bb-session`) return currentSession;
    if (url === `${BASE}/sessions/bb-session/steps`) {
      const body = JSON.parse(String(options?.body));
      const available = new Set(flow.acquisition.filter(stage => currentSession.steps[stage.id].status === "completed").map(stage => stage.id));
      for (const stage of flow.acquisition.filter(stage => body.step_ids.includes(stage.id))) {
        if (stage.dependencies.some(key => !available.has(key))) throw new Error(`缺少前置：${stage.dependencies.join("、")}`);
        available.add(stage.id);
      }
      currentSession = { ...currentSession, revision: currentSession.revision + 1,
        steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id,
          { status: body.step_ids.includes(stage.id) ? "completed" : currentSession.steps[stage.id].status, error: "" }])) };
      if (body.step_ids.includes("A09")) for (const id of ["A07", "A08"]) currentSession.steps[id].status = "stale";
      return currentSession;
    }
    if (url === `${BASE}/sessions/bb-session/parameters`) {
      const values = JSON.parse(String(options?.body)).parameters;
      const changed = Object.keys(values).filter(key => values[key] !== currentSession.parameters[key]);
      const impacts = new Set(changed.flatMap(key => (flow.parameter_impacts.acquisition as Record<string, string[]>)[key] ?? []));
      currentSession = { ...currentSession, revision: currentSession.revision + 1, parameters: values,
        steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: impacts.has(stage.id)
          ? currentSession.steps[stage.id].status === "pending" ? "pending" : "stale" : currentSession.steps[stage.id].status, error: "" }])) };
      return currentSession;
    }
    if (url === `${BASE}/sessions/bb-session/finish`) return currentSession = { ...currentSession, status: "closed", revision: currentSession.revision + 1,
      steps: { ...currentSession.steps, A10: { status: "completed", error: "" } } };
    if (url.endsWith("/analysis") && options?.method === "POST") return { id: "analysis", status: "running", events: [] };
    if (url.endsWith("/analysis")) return { run_id: url.split("/").at(-2), revision: 0, options: { LINEAR_R_SQUARED_MIN: 0.95 },
      steps: Object.fromEntries(flow.analysis.map(stage => [stage.id, { status: "completed", error: "" }])), fingerprint: {}, summary: {} };
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary], total: 1 };
    if (url.endsWith("/summary")) return summary;
    if (url === `${BASE}/preflight`) return { ok: true, errors: [] };
    if (url === `${BASE}/defaults`) return { schema: { fields, parameter_layout: JSON.parse(String(options?.body)).parameter_layout } };
    if (url === `${BASE}/runs` && options?.method === "POST") return { id: "acquisition", kind: "experiment:bell-bloom-z-field-calibration", status: "running", events: [] };
    if (url.endsWith("/analyze")) return { id: "analysis", status: "running", events: [] };
    throw new Error(`未模拟 ${url}`);
  });
});
afterEach(cleanup);

it("恢复最近结果，只有运行/历史两个 Tab，切换与回填保留参数", async () => {
  renderPage();
  expect(within(screen.getByRole("tablist", { name: "Bell Bloom Z 标定视图" })).getAllByRole("tab")).toHaveLength(2);
  expect(await within(screen.getByRole("tabpanel", { name: "运行" })).findByAltText("Z 电压频率标定曲线")).toHaveAttribute("src", "/plot.png?v=1");
  await editor("A02");
  fireEvent.change(screen.getByLabelText("运行标签"), { target: { value: "edited" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "填回参数" })).toBeEnabled());
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("edited");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("history-tag");
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  expect(await screen.findByText("参数与布局已保存")).toBeVisible();
});

it("最新失败运行不显示更早结果，重新分析完成后刷新图片", async () => {
  summary = { ...summary, completion_status: "failed", analysis_status: "not_started", failure_reason: "频点采集失败", analysis: null, artifacts: {} };
  renderPage();
  expect(await within(screen.getByRole("tabpanel", { name: "运行" })).findByText("频点采集失败")).toBeVisible();
  expect(screen.queryByAltText("Z 电压频率标定曲线")).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "重新分析" }));
  await screen.findByRole("button", { name: "完成测试任务" });
  summary = { ...summary, analysis_status: "completed", analysis: { success: false, K_Z_Hz_per_V: null, f_0V_Hz: null, linear_fit: { uncertainties: [null, null], r_squared: null }, rejection_reasons: ["有效中心不足 2 个"] }, artifacts: { "z_frequency_calibration.png": "/plot.png?v=2" } };
  fireEvent.click(screen.getByRole("button", { name: "完成测试任务" }));
  const history = screen.getByRole("tabpanel", { name: "历史" });
  await waitFor(() => expect(within(history).getByAltText("Z 电压频率标定曲线")).toHaveAttribute("src", "/plot.png?v=2"));
  expect(within(history).getByText("有效中心不足 2 个")).toBeVisible();
  expect(within(history).getByText("未通过")).toBeVisible();
  expect(within(history).queryByText("通过")).not.toBeInTheDocument();
});

it("活动任务在切换 Tab 后仍可完成并刷新最近结果", async () => {
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "预检并运行实验" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByRole("button", { name: "完成测试任务" });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  fireEvent.click(screen.getByRole("button", { name: "完成测试任务" }));
  await waitFor(() => expect(mockApi.mock.calls.filter(([url]) => url === `${BASE}/runs?limit=1&offset=0`).length).toBeGreaterThan(2));
});

it("预检失败不会启动采集，空历史可浏览", async () => {
  const previous = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/preflight`) return { ok: false, errors: ["扫频下限必须为正"] };
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [], total: 0 };
    return previous(url, options);
  });
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "预检并运行实验" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("扫频下限必须为正")).toBeVisible();
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByText("暂无运行记录。")).toBeVisible();
});


it("真实路由选择本实验步骤，缺前置拒绝，完整节点执行后可无前置结束", async () => {
  renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: "A08 Z DC × Pump 调制与解调扫频" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  expect(await screen.findByText("Error: 缺少前置：A07")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(currentSession.revision).toBe(1));
  expect(currentSession.steps.A07.status).toBe("stale");
  expect(screen.getByRole("checkbox", { name: "A10 安全结束会话" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(currentSession.status).toBe("closed"));
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
});

it("恢复会话，Pump占空比共享草稿同步和还原，应用只失效不自动执行", async () => {
  currentSession = makeSession();
  for (const stage of flow.acquisition.filter(stage => stage.id <= "A07")) currentSession.steps[stage.id].status = "completed";
  renderPage();
  const pump = await editor("A06");
  const scan = await editor("A08");
  fireEvent.change(pump.getByLabelText("Pump 占空比 (%)"), { target: { value: "10" } });
  expect(scan.getByLabelText("Pump 占空比 (%)")).toHaveValue(10);
  expect(screen.getByText(/预计需重跑的已完成步骤：A06/)).toBeVisible();
  fireEvent.change(scan.getByLabelText("Pump 占空比 (%)"), { target: { value: "5" } });
  expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument();
  fireEvent.change(scan.getByLabelText("Pump 占空比 (%)"), { target: { value: "10" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(currentSession.parameters.PUMP_MOD_DUTY).toBe(10));
  expect(currentSession.steps.A06.status).toBe("stale");
  expect(currentSession.steps.A05.status).toBe("completed");
  expect(mockApi.mock.calls.filter(([url]) => String(url).endsWith("/steps"))).toHaveLength(0);
});

it("历史分析覆盖独立保留，保存默认值与历史填回不串改下一次实验", async () => {
  renderPage();
  const next = await editor("B03");
  fireEvent.change(next.getByLabelText("线性标定最低 R²"), { target: { value: "0.9" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: summary.run_id } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.95));
  fireEvent.change(screen.getByLabelText("线性标定最低 R²"), { target: { value: "0.98" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "" } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.9));
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: summary.run_id } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.98));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.LINEAR_R_SQUARED_MIN).toBe(0.9);
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url.endsWith("/analysis") && options?.method === "POST"
    && JSON.parse(String(options.body)).analysis_parameters.LINEAR_R_SQUARED_MIN === 0.98)).toBe(true));
});

it("保留带符号指标、三图和安全诊断，顶部操作实际位于流程容器", async () => {
  summary.artifacts = { ...summary.artifacts, "frequency_response_fits.png": "/response.png", "z_frequency_residuals.png": "/residual.png", "analysis.json": "/analysis.json" };
  summary.safety_shutdown = { errors: ["模拟恢复诊断"] };
  renderPage();
  const run = screen.getByRole("tabpanel", { name: "运行" });
  await within(run).findByText("-26,000 ± 2");
  expect(within(run).getByText("安全恢复：模拟恢复诊断")).toBeVisible();
  fireEvent.click(within(run).getByText("查看各频扫拟合与残差"));
  expect(within(run).getByAltText("各 Z 电压的 R 频扫拟合")).toHaveAttribute("src", "/response.png");
  expect(within(run).getByAltText("线性拟合残差")).toHaveAttribute("src", "/residual.png");
  expect(within(run).getByRole("link", { name: "analysis.json" })).toHaveAttribute("href", "/analysis.json");
  expect(screen.getByRole("button", { name: "保存参数与布局" }).closest(".xy-workflow")).not.toBeNull();
  expect(screen.queryByRole("heading", { name: "控制与状态" })).not.toBeInTheDocument();
});


it("共享参数分类同步并保存回读，移动不改变数值", async () => {
  const original = mockApi.getMockImplementation()!;
  let savedLayout: any;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/schema` && savedLayout) return { fields, parameter_layout_saved: true, parameter_layout: savedLayout } as never;
    if (url === `${BASE}/defaults`) savedLayout = JSON.parse(String(options?.body)).parameter_layout;
    return original(url, options);
  });
  const view = renderPage();
  const pump = await editor("A06");
  await editor("A08");
  fireEvent.click(pump.getByRole("button", { name: "Pump 占空比 移至高级参数" }));
  expect(screen.getAllByRole("button", { name: "Pump 占空比 移至基础参数", hidden: true })).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.PUMP_MOD_DUTY).toBe(5);
  expect(savedLayout.advanced).toContain("PUMP_MOD_DUTY");
  view.unmount();
  renderPage();
  await editor("A06");
  await editor("A08");
  expect(screen.getAllByRole("button", { name: "Pump 占空比 移至基础参数", hidden: true })).toHaveLength(2);
});

it("两个历史目录的分析草稿独立，切换主Tab仍保留", async () => {
  const original = mockApi.getMockImplementation()!;
  const second = { ...summary, run_id: "0919_older", parameters: { ...parameters(), RUN_TAG: "older" } };
  mockApi.mockImplementation(async (url, options) => {
    if (url.startsWith(`${BASE}/runs?`)) return { runs: [summary, second], total: 2 } as never;
    if (url === `${BASE}/runs/${second.run_id}/summary`) return second;
    return original(url, options);
  });
  renderPage();
  await editor("B03");
  const target = screen.getByLabelText("分析操作对象");
  fireEvent.change(target, { target: { value: summary.run_id } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.95));
  fireEvent.change(screen.getByLabelText("线性标定最低 R²"), { target: { value: "0.96" } });
  fireEvent.change(target, { target: { value: second.run_id } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.95));
  fireEvent.change(screen.getByLabelText("线性标定最低 R²"), { target: { value: "0.97" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  fireEvent.change(target, { target: { value: summary.run_id } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.96));
  fireEvent.change(target, { target: { value: second.run_id } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.97));
  fireEvent.change(target, { target: { value: "" } });
  await waitFor(() => expect(screen.getByLabelText("线性标定最低 R²")).toHaveValue(0.8));
});

it("恢复真实质量失败状态，参数应用失败保留草稿并可安全结束", async () => {
  currentSession = makeSession();
  for (const stage of flow.acquisition.filter(stage => stage.id <= "A07")) currentSession.steps[stage.id].status = "completed";
  currentSession.steps.A08 = { status: "quality_failed", error: "连续三次 R 标准差超限" };
  const previous = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/sessions/bb-session/parameters`) throw new Error("修订冲突，请刷新");
    return previous(url, options);
  });
  renderPage();
  expect(await screen.findByText("质量未通过")).toBeVisible();
  expect(screen.getByText("连续三次 R 标准差超限")).toBeVisible();
  expect(screen.getByText("质量门槛：每次 R 标准差不超过阈值；重采仍失败立即阻断后继并保留全部尝试")).toBeVisible();
  const pump = await editor("A06");
  fireEvent.change(pump.getByLabelText("Pump 占空比 (%)"), { target: { value: "10" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  expect(await screen.findByText("Error: 修订冲突，请刷新")).toBeVisible();
  expect(pump.getByLabelText("Pump 占空比 (%)")).toHaveValue(10);
  expect(currentSession.parameters.PUMP_MOD_DUTY).toBe(5);
  expect(screen.getByText(/参数草稿尚未应用/)).toBeVisible();
  expect(mockApi.mock.calls.filter(([url]) => String(url).endsWith("/steps"))).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(currentSession.status).toBe("closed"));
});
