import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));

const ID = "mx-main-field-calibration";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
// 由本实验真实 schema 和步骤清单生成，覆盖全部可见参数。
const fields = [
  {"name":"SYSTEM_SETTLE_S","label":"系统稳定时间","type":"number","default":0.1,"group":"basic","unit":"s","minimum":0},
  {"name":"RUN_TAG","label":"运行标签","type":"string","default":"mx_main_field_cal","group":"basic","unit":""},
  {"name":"MAIN_FIELD_START_MA","label":"主场电流起点","type":"number","default":7,"group":"basic","unit":"mA"},
  {"name":"MAIN_FIELD_STOP_MA","label":"主场电流终点","type":"number","default":10,"group":"basic","unit":"mA"},
  {"name":"MAIN_FIELD_STEP_MA","label":"主场电流步进","type":"number","default":0.5,"group":"basic","unit":"mA","minimum":0.001},
  {"name":"MAIN_FIELD_REFERENCE_CURRENT_MA","label":"预测参考电流","type":"number","default":9.333,"group":"basic","unit":"mA"},
  {"name":"MAIN_FIELD_REFERENCE_FREQUENCY_HZ","label":"预测参考频率","type":"number","default":90000,"group":"basic","unit":"Hz","minimum":0.001},
  {"name":"MAIN_FIELD_INITIAL_HZ_PER_MA","label":"主场初始频率斜率","type":"number","default":9673.338,"group":"basic","unit":"Hz/mA","minimum":0.001},
  {"name":"FREQUENCY_HALF_WIDTH_HZ","label":"局部扫频半宽","type":"number","default":10000,"group":"basic","unit":"Hz","minimum":0.001},
  {"name":"FREQUENCY_STEP_HZ","label":"局部扫频步进","type":"number","default":500,"group":"basic","unit":"Hz","minimum":0.001},
  {"name":"Y_RF_AMPLITUDE_VPP","label":"Y RF 扫频幅度","type":"number","default":0.05,"group":"basic","unit":"Vpp","minimum":0,"maximum":2},
  {"name":"GYROMAGNETIC_RATIO_HZ_PER_NT","label":"频率-磁场换算系数","type":"number","default":7,"group":"basic","unit":"Hz/nT","minimum":0.001},
  {"name":"FREQUENCY_DURATION_S","label":"频点采样时间","type":"number","default":0.1,"group":"basic","unit":"s","minimum":0.001},
  {"name":"TEMP_SWITCH_ON_LAG_S","label":"每频点温控恢复等待","type":"number","default":1,"group":"basic","unit":"s","minimum":0},
  {"name":"R_BAD_POINT_STD_THRESHOLD_V","label":"R 坏点标准差阈值","type":"number","default":0.01,"group":"advanced","unit":"V","minimum":0},
  {"name":"R_POINT_MAX_ATTEMPTS","label":"R 点最大采集次数","type":"integer","default":3,"group":"advanced","unit":"","minimum":1},
  {"name":"DEMOD_IDX","label":"锁相放大器 Demod 索引","type":"integer","default":0,"group":"advanced","unit":"","minimum":0},
  {"name":"DEMOD_OSC_IDX","label":"锁相放大器 振荡器索引","type":"integer","default":0,"group":"advanced","unit":"","minimum":0},
  {"name":"HF2_SIGNAL_RANGE_V","label":"锁相放大器 Input1 量程","type":"number","default":2,"group":"advanced","unit":"V","minimum":0.001},
  {"name":"RESPONSE_RATE_SA_S","label":"响应请求采样率","type":"number","default":1000,"group":"advanced","unit":"Sa/s","minimum":1},
  {"name":"RESPONSE_TIME_CONSTANT_S","label":"响应时间常数","type":"number","default":0.001,"group":"advanced","unit":"s","minimum":0},
  {"name":"RESPONSE_DEMOD_ORDER","label":"响应解调阶数","type":"integer","default":4,"group":"advanced","unit":"","minimum":1},
  {"name":"PUMP_CARRIER_FREQUENCY_HZ","label":"Pump AOM 载波频率","type":"number","default":100000000,"group":"advanced","unit":"Hz","minimum":1},
  {"name":"PUMP_CARRIER_AMPLITUDE_VPP","label":"Pump AOM 载波幅度","type":"number","default":0.18,"group":"advanced","unit":"Vpp","minimum":0,"maximum":0.18},
  {"name":"PUMP_GATE_VOLTAGE_V","label":"Pump RF 开关常开电平","type":"number","default":5,"group":"advanced","unit":"V","minimum":5,"maximum":5},
  {"name":"FIXED_PARAMS.Pump_laser_power","label":"Pump 光功率","type":"number","default":0.5,"group":"basic","unit":"V"},
  {"name":"FIXED_PARAMS.Probe_laser_power","label":"Probe 光功率","type":"number","default":0.3,"group":"basic","unit":"V"},
  {"name":"FIXED_PARAMS.temperature","label":"气室温度","type":"number","default":120,"group":"basic","unit":"°C"},
  {"name":"LINEAR_R_SQUARED_MIN","label":"线性标定最低 R²","type":"number","default":0.99,"group":"advanced","unit":"","minimum":0,"maximum":1},
];
const acquisition = [
  {"id":"A01","title":"启动预检","group":"准备","dependencies":[],"parameters":[],"artifacts":[],"display_parameters":[]},
  {"id":"A02","title":"设备连接与 GS200 初态回读","group":"准备","dependencies":["A01"],"parameters":["RUN_TAG"],"artifacts":[],"display_parameters":["RUN_TAG"]},
  {"id":"A03","title":"参考时钟同步与回读","group":"准备","dependencies":["A02"],"parameters":[],"artifacts":[],"display_parameters":[]},
  {"id":"A04","title":"辅助场归零、光功率与 GS200 限流","group":"初始条件","dependencies":["A03"],"parameters":["FIXED_PARAMS.Pump_laser_power","FIXED_PARAMS.Probe_laser_power"],"artifacts":[],"display_parameters":["FIXED_PARAMS.Pump_laser_power","FIXED_PARAMS.Probe_laser_power"]},
  {"id":"A05","title":"TEC 目标温度","group":"初始条件","dependencies":["A04"],"parameters":["FIXED_PARAMS.temperature"],"artifacts":[],"display_parameters":["FIXED_PARAMS.temperature"]},
  {"id":"A06","title":"Pump 连续载波与常开门控","group":"激励","dependencies":["A05"],"parameters":["PUMP_CARRIER_FREQUENCY_HZ","PUMP_CARRIER_AMPLITUDE_VPP","PUMP_GATE_VOLTAGE_V"],"artifacts":[],"display_parameters":["PUMP_CARRIER_FREQUENCY_HZ","PUMP_CARRIER_AMPLITUDE_VPP","PUMP_GATE_VOLTAGE_V"]},
  {"id":"A07","title":"Y RF 正弦与 R 解调准备","group":"激励","dependencies":["A06"],"parameters":["Y_RF_AMPLITUDE_VPP","MAIN_FIELD_REFERENCE_FREQUENCY_HZ","DEMOD_IDX","DEMOD_OSC_IDX","HF2_SIGNAL_RANGE_V","RESPONSE_RATE_SA_S","RESPONSE_TIME_CONSTANT_S","RESPONSE_DEMOD_ORDER"],"artifacts":[],"display_parameters":["Y_RF_AMPLITUDE_VPP","MAIN_FIELD_REFERENCE_FREQUENCY_HZ","DEMOD_IDX","DEMOD_OSC_IDX","HF2_SIGNAL_RANGE_V","RESPONSE_RATE_SA_S","RESPONSE_TIME_CONSTANT_S","RESPONSE_DEMOD_ORDER"]},
  {"id":"A08","title":"GS200 电流与局部 Y RF 扫频","group":"采集","dependencies":["A07"],"parameters":["MAIN_FIELD_START_MA","MAIN_FIELD_STOP_MA","MAIN_FIELD_STEP_MA","MAIN_FIELD_REFERENCE_CURRENT_MA","MAIN_FIELD_REFERENCE_FREQUENCY_HZ","MAIN_FIELD_INITIAL_HZ_PER_MA","FREQUENCY_HALF_WIDTH_HZ","FREQUENCY_STEP_HZ","Y_RF_AMPLITUDE_VPP","FREQUENCY_DURATION_S","SYSTEM_SETTLE_S","TEMP_SWITCH_ON_LAG_S","R_BAD_POINT_STD_THRESHOLD_V","R_POINT_MAX_ATTEMPTS"],"artifacts":["raw/main_field_scan_index.npz","raw/current_*/*.npz"],"display_parameters":["MAIN_FIELD_START_MA","MAIN_FIELD_STOP_MA","MAIN_FIELD_STEP_MA","MAIN_FIELD_REFERENCE_CURRENT_MA","MAIN_FIELD_REFERENCE_FREQUENCY_HZ","MAIN_FIELD_INITIAL_HZ_PER_MA","FREQUENCY_HALF_WIDTH_HZ","FREQUENCY_STEP_HZ","Y_RF_AMPLITUDE_VPP","FREQUENCY_DURATION_S","SYSTEM_SETTLE_S","TEMP_SWITCH_ON_LAG_S","R_BAD_POINT_STD_THRESHOLD_V","R_POINT_MAX_ATTEMPTS"]},
  {"id":"A09","title":"恢复 GS200 与本轮安全收尾","group":"结束","dependencies":["A08"],"parameters":[],"artifacts":[],"display_parameters":[]},
  {"id":"A10","title":"安全结束会话","group":"结束","dependencies":[],"parameters":[],"artifacts":[],"display_parameters":[]},
];
const analysis = [
  {"id":"B01","title":"配置与原始频扫资格","group":"独立分析","dependencies":[],"parameters":[],"artifacts":[],"display_parameters":[]},
  {"id":"B02","title":"逐电流 Lorentzian 共振中心","group":"独立分析","dependencies":["B01"],"parameters":[],"artifacts":["curve_fits.json"],"display_parameters":[]},
  {"id":"B03","title":"加权线性标定与磁场换算","group":"独立分析","dependencies":["B02"],"parameters":["GYROMAGNETIC_RATIO_HZ_PER_NT","LINEAR_R_SQUARED_MIN"],"artifacts":["calibration_evaluation.json"],"display_parameters":["GYROMAGNETIC_RATIO_HZ_PER_NT","LINEAR_R_SQUARED_MIN"]},
  {"id":"B04","title":"标定报告与结果图","group":"独立分析","dependencies":["B03"],"parameters":[],"artifacts":["analysis.yaml","analysis.json","calibration_results.npz","frequency_response_fits.png","main_field_frequency_calibration.png","main_field_frequency_residuals.png"],"display_parameters":[]},
];
const impacts: Record<string, Record<string, string[]>> = {"acquisition":{"RUN_TAG":["A02","A03","A04","A05","A06","A07","A08","A09"],"FIXED_PARAMS.Pump_laser_power":["A04","A05","A06","A07","A08","A09"],"FIXED_PARAMS.Probe_laser_power":["A04","A05","A06","A07","A08","A09"],"FIXED_PARAMS.temperature":["A05","A06","A07","A08","A09"],"PUMP_CARRIER_FREQUENCY_HZ":["A06","A07","A08","A09"],"PUMP_CARRIER_AMPLITUDE_VPP":["A06","A07","A08","A09"],"PUMP_GATE_VOLTAGE_V":["A06","A07","A08","A09"],"Y_RF_AMPLITUDE_VPP":["A07","A08","A09"],"MAIN_FIELD_REFERENCE_FREQUENCY_HZ":["A07","A08","A09"],"DEMOD_IDX":["A07","A08","A09"],"DEMOD_OSC_IDX":["A07","A08","A09"],"HF2_SIGNAL_RANGE_V":["A07","A08","A09"],"RESPONSE_RATE_SA_S":["A07","A08","A09"],"RESPONSE_TIME_CONSTANT_S":["A07","A08","A09"],"RESPONSE_DEMOD_ORDER":["A07","A08","A09"],"MAIN_FIELD_START_MA":["A08","A09"],"MAIN_FIELD_STOP_MA":["A08","A09"],"MAIN_FIELD_STEP_MA":["A08","A09"],"MAIN_FIELD_REFERENCE_CURRENT_MA":["A08","A09"],"MAIN_FIELD_INITIAL_HZ_PER_MA":["A08","A09"],"FREQUENCY_HALF_WIDTH_HZ":["A08","A09"],"FREQUENCY_STEP_HZ":["A08","A09"],"FREQUENCY_DURATION_S":["A08","A09"],"SYSTEM_SETTLE_S":["A08","A09"],"TEMP_SWITCH_ON_LAG_S":["A08","A09"],"R_BAD_POINT_STD_THRESHOLD_V":["A08","A09"],"R_POINT_MAX_ATTEMPTS":["A08","A09"]},"analysis":{"GYROMAGNETIC_RATIO_HZ_PER_NT":["B03","B04"],"LINEAR_R_SQUARED_MIN":["B03","B04"]}};
const layout = { basic: fields.filter(field => field.group === "basic").map(field => field.name),
  advanced: fields.filter(field => field.group === "advanced").map(field => field.name) };
const old = { run_id: "1014_old", timestamp: "1014", run_tag: "old", completion_status: "completed" };
const older = { ...old, run_id: "1013_older", timestamp: "1013", run_tag: "older" };
let current: any;
let preflightOk: boolean;
let applyError: boolean;
let savedParameters: Record<string, unknown>;
let savedLayout: typeof layout;
const parameters = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const makeSession = () => ({ session_id: "mx-session", job_id: "mx-job", status: "waiting", revision: 0,
  parameters: parameters(), pending_request: null, outputs: { "Y RF": "OFF", 温控: "ON" },
  steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: "pending", error: "" }])) });

beforeEach(() => {
  current = null; preflightOk = true; applyError = false;
  savedParameters = parameters(); savedLayout = structuredClone(layout);
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { id: ID, title: "Mx 主磁场频率标定", description: "R-only", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields: fields.map(field => ({ ...field, default: savedParameters[field.name] })),
      parameter_layout_saved: true, parameter_layout: savedLayout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === "/api/jobs/mx-job") return { id: "mx-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === `${BASE}/workflow`) return { acquisition, analysis,
      parameter_impacts: impacts, run_step_id: "A08", finish_step_id: "A10", diagram_url: `${BASE}/workflow/diagram` } as never;
    if (url === `${BASE}/sessions/active`) return current as never;
    if (url === `${BASE}/sessions` && options?.method === "POST") return current = makeSession();
    if (url === `${BASE}/sessions/mx-session/steps`) {
      const available = new Set(Object.keys(current.steps).filter(id => current.steps[id].status === "completed"));
      for (const stage of acquisition.filter(stage => body.step_ids.includes(stage.id))) {
        const missing = stage.dependencies.filter(id => !available.has(id));
        if (missing.length) throw new Error(`缺少前置：${missing.map(id => `${id} ${acquisition.find(item => item.id === id)!.title}`).join("、")}`);
        available.add(stage.id);
      }
      const states = structuredClone(current.steps);
      for (const id of body.step_ids) states[id] = { status: "completed", error: "" };
      if (body.step_ids.includes("A09")) for (const id of ["A04", "A05", "A06", "A07", "A08"]) states[id] = { status: "stale", error: "" };
      return current = { ...current, revision: current.revision + 1, steps: states };
    }
    if (url === `${BASE}/sessions/mx-session/parameters`) {
      if (applyError) throw new Error("模拟参数应用失败");
      const changed = Object.keys(body.parameters).filter(key => body.parameters[key] !== current.parameters[key]);
      const affected = new Set(changed.flatMap(key => impacts.acquisition[key] ?? []));
      return current = { ...current, parameters: body.parameters, revision: current.revision + 1,
        steps: Object.fromEntries(acquisition.map(stage => [stage.id, affected.has(stage.id) && current.steps[stage.id].status !== "pending"
          ? { status: "stale", error: "" } : current.steps[stage.id]])) };
    }
    if (url === `${BASE}/sessions/mx-session/finish`) return current = { ...current, revision: current.revision + 1, status: "closed",
      steps: { ...current.steps, A10: { status: "completed", error: "" } } };
    if (url === `${BASE}/sessions/mx-session`) return current as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old, older], total: 2 } as never;
    for (const run of [old, older]) {
      if (url === `${BASE}/runs/${run.run_id}/summary`) return { ...run, analysis_status: "not_started", analysis_error: "",
        parameters: { ...parameters(), RUN_TAG: run === old ? "historical" : "earlier", MAIN_FIELD_STEP_MA: 0.25 }, artifacts: {} } as never;
      if (url === `${BASE}/runs/${run.run_id}/analysis` && options?.method === "POST") return { id: "analysis-job", status: "running", events: [] } as never;
      if (url === `${BASE}/runs/${run.run_id}/analysis`) return { run_id: run.run_id, revision: 0,
        options: { GYROMAGNETIC_RATIO_HZ_PER_NT: run === old ? 8 : 9, LINEAR_R_SQUARED_MIN: 0.99 },
        steps: Object.fromEntries(analysis.map(stage => [stage.id, { status: "completed", error: "" }])), summary: {}, fingerprint: {} } as never;
    }
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["预测扫频下限必须大于 0 Hz"] } as never;
    if (url === `${BASE}/derive`) return { values: {} } as never;
    if (url === `${BASE}/defaults`) {
      savedParameters = body.parameters; savedLayout = body.parameter_layout;
      return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: savedLayout } } as never;
    }
    throw new Error(`未模拟接口 ${url}`);
  });
});
afterEach(cleanup);

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
  fireEvent.click(screen.getByRole("checkbox", { name: "A08 GS200 电流与局部 Y RF 扫频" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: 缺少前置：A07/);
  expect(screen.getByRole("checkbox", { name: "A07 Y RF 正弦与 R 解调准备" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(current.revision).toBe(1));
  expect(screen.getByRole("checkbox", { name: "A10 安全结束会话" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current.status).toBe("closed"));
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
});

it("共享过程参数草稿同步、还原清除预览、应用后显式重跑，恢复存活会话", async () => {
  current = makeSession();
  for (const stage of acquisition.slice(0, 7)) current.steps[stage.id].status = "completed";
  page();
  const sweep = await editor("A07");
  const amplitude = await editor("A08");
  fireEvent.change(sweep.getByLabelText("预测参考频率 (Hz)"), { target: { value: "90500" } });
  expect(amplitude.getByLabelText("预测参考频率 (Hz)")).toHaveValue(90500);
  expect(screen.getByText(/预计需重跑的已完成步骤：A07/)).toBeVisible();
  fireEvent.change(amplitude.getByLabelText("预测参考频率 (Hz)"), { target: { value: "90000" } });
  expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument();
  fireEvent.change(amplitude.getByLabelText("预测参考频率 (Hz)"), { target: { value: "91000" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(current.parameters.MAIN_FIELD_REFERENCE_FREQUENCY_HZ).toBe(91000));
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
  fireEvent.change(next.getByLabelText("频率-磁场换算系数 (Hz/nT)"), { target: { value: "5" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await waitFor(() => expect(screen.getByLabelText("频率-磁场换算系数 (Hz/nT)")).toHaveValue(8));
  fireEvent.change(screen.getByLabelText("频率-磁场换算系数 (Hz/nT)"), { target: { value: "11" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "" } });
  await waitFor(() => expect(screen.getByLabelText("频率-磁场换算系数 (Hz/nT)")).toHaveValue(5));
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await waitFor(() => expect(screen.getByLabelText("频率-磁场换算系数 (Hz/nT)")).toHaveValue(11));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.GYROMAGNETIC_RATIO_HZ_PER_NT).toBe(5);
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs/${old.run_id}/analysis`
    && options?.method === "POST" && JSON.parse(String(options.body)).analysis_parameters.GYROMAGNETIC_RATIO_HZ_PER_NT === 11)).toBe(true));
});

it("多参数影响合并、全部还原、失败保留草稿且执行记录只在成功应用后失效", async () => {
  current = makeSession();
  for (const stage of acquisition.slice(0, 8)) current.steps[stage.id].status = "completed";
  const original = structuredClone(current.steps);
  page();
  const sweep = await editor("A07");
  const scan = await editor("A08");
  const pump = await editor("A06");
  fireEvent.click(pump.getByText(/高级参数（/));
  fireEvent.change(sweep.getByLabelText("预测参考频率 (Hz)"), { target: { value: "90500" } });
  fireEvent.change(pump.getByLabelText("Pump AOM 载波幅度 (Vpp)"), { target: { value: "0.15" } });
  expect(scan.getByLabelText("预测参考频率 (Hz)")).toHaveValue(90500);
  expect(screen.getByText(/预计需重跑的已完成步骤：A06.*A07.*A08/)).toBeVisible();
  expect(current.steps).toEqual(original);
  fireEvent.change(scan.getByLabelText("预测参考频率 (Hz)"), { target: { value: "90000" } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A06/)).toBeVisible();
  fireEvent.change(pump.getByLabelText("Pump AOM 载波幅度 (Vpp)"), { target: { value: "0.18" } });
  expect(screen.queryByText(/预计需重跑的已完成步骤/)).not.toBeInTheDocument();
  fireEvent.change(scan.getByLabelText("预测参考频率 (Hz)"), { target: { value: "91000" } });
  fireEvent.change(pump.getByLabelText("Pump AOM 载波幅度 (Vpp)"), { target: { value: "0.15" } });
  applyError = true;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await screen.findByText("Error: 模拟参数应用失败");
  expect(scan.getByLabelText("预测参考频率 (Hz)")).toHaveValue(91000);
  expect(pump.getByLabelText("Pump AOM 载波幅度 (Vpp)" )).toHaveValue(0.15);
  expect(current.steps).toEqual(original);
  expect(screen.getByText(/参数草稿尚未应用/)).toBeVisible();
  applyError = false;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument());
  for (const id of ["A06", "A07", "A08"]) expect(current.steps[id].status).toBe("stale");
  expect(current.steps.A05.status).toBe("completed");
  expect(current.steps.A10.status).toBe("pending");
  expect(mockApi.mock.calls.filter(([url]) => String(url).endsWith("/steps"))).toHaveLength(0);
});

it("真实全部参数有编辑入口，共享分类移动后跨节点同步并保存回读", async () => {
  const view = page();
  const sweep = await editor("A07");
  const scan = await editor("A08");
  fireEvent.change(sweep.getByLabelText("Y RF 扫频幅度 (Vpp)"), { target: { value: "0.04" } });
  expect(scan.getByLabelText("Y RF 扫频幅度 (Vpp)")).toHaveValue(0.04);
  fireEvent.click(sweep.getByRole("button", { name: "Y RF 扫频幅度 移至高级参数" }));
  expect(sweep.getByRole("button", { name: "Y RF 扫频幅度 移至基础参数", hidden: true })).toBeInTheDocument();
  expect(scan.getByRole("button", { name: "Y RF 扫频幅度 移至基础参数", hidden: true })).toBeInTheDocument();
  for (const stage of [...acquisition, ...analysis].filter(stage => stage.display_parameters.length)) {
    const region = await editor(stage.id);
    for (const name of stage.display_parameters) {
      const field = fields.find(item => item.name === name)!;
      expect(region.getByLabelText(field.unit ? `${field.label} (${field.unit})` : field.label)).toBeInTheDocument();
    }
  }
  const initialization = await editor("A04");
  expect(initialization.getByLabelText("Pump 光功率 (V)")).toBeInTheDocument();
  expect(scan.queryByLabelText("Pump 光功率 (V)")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(savedLayout.advanced).toContain("Y_RF_AMPLITUDE_VPP");
  expect(savedLayout.basic).not.toContain("Y_RF_AMPLITUDE_VPP");
  expect(new Set([...savedLayout.basic, ...savedLayout.advanced]).size).toBe(fields.length);
  expect(savedParameters.Y_RF_AMPLITUDE_VPP).toBe(0.04);
  view.unmount();
  page();
  const reloaded = await editor("A07");
  expect(reloaded.getByRole("button", { name: "Y RF 扫频幅度 移至基础参数", hidden: true })).toBeInTheDocument();
  expect(reloaded.getByLabelText("Y RF 扫频幅度 (Vpp)")).toHaveValue(0.04);
});

it("两个历史分析对象与下一次实验分别保留草稿和分类，切Tab及保存不会混写", async () => {
  page();
  const next = await editor("B03");
  fireEvent.change(next.getByLabelText("频率-磁场换算系数 (Hz/nT)"), { target: { value: "5" } });
  const selector = screen.getByLabelText("分析操作对象");
  fireEvent.change(selector, { target: { value: old.run_id } });
  await waitFor(() => expect(screen.getByLabelText("频率-磁场换算系数 (Hz/nT)")).toHaveValue(8));
  fireEvent.change(screen.getByLabelText("频率-磁场换算系数 (Hz/nT)"), { target: { value: "11" } });
  fireEvent.change(selector, { target: { value: older.run_id } });
  await waitFor(() => expect(screen.getByLabelText("频率-磁场换算系数 (Hz/nT)")).toHaveValue(9));
  fireEvent.change(screen.getByLabelText("频率-磁场换算系数 (Hz/nT)"), { target: { value: "12" } });
  fireEvent.click(screen.getByRole("button", { name: "频率-磁场换算系数 移至高级参数" }));
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "填回参数" });
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByLabelText("频率-磁场换算系数 (Hz/nT)")).toHaveValue(12);
  fireEvent.change(selector, { target: { value: old.run_id } });
  await waitFor(() => expect(screen.getByLabelText("频率-磁场换算系数 (Hz/nT)")).toHaveValue(11));
  fireEvent.change(selector, { target: { value: "" } });
  await waitFor(() => expect(screen.getByLabelText("频率-磁场换算系数 (Hz/nT)")).toHaveValue(5));
  fireEvent.change(selector, { target: { value: older.run_id } });
  await waitFor(() => expect(screen.getByLabelText("频率-磁场换算系数 (Hz/nT)")).toHaveValue(12));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(savedParameters.GYROMAGNETIC_RATIO_HZ_PER_NT).toBe(5);
  expect(savedLayout.advanced).toContain("GYROMAGNETIC_RATIO_HZ_PER_NT");
  expect(mockApi.mock.calls.filter(([url, options]) => String(url).endsWith("/analysis") && options?.method === "POST")).toHaveLength(0);
});

it("恢复质量失败会话显示诊断，后继拒绝后仍能安全结束且保留执行记录", async () => {
  current = makeSession();
  for (const stage of acquisition.slice(0, 7)) current.steps[stage.id].status = "completed";
  current.steps.A08 = { status: "quality_failed", error: "连续3次 std(R) 超过阈值" };
  page();
  await screen.findByText("连续3次 std(R) 超过阈值");
  expect(screen.getByText("质量未通过")).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox", { name: "A09 恢复 GS200 与本轮安全收尾" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: 缺少前置：A08/);
  expect(current.steps.A09.status).toBe("pending");
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await screen.findByText(/下方状态为已结束会话的执行记录/);
  expect(current.status).toBe("closed");
  expect(current.steps.A08.status).toBe("quality_failed");
  expect(current.steps.A10.status).toBe("completed");
  const scan = await editor("A08");
  fireEvent.change(scan.getByLabelText("主场电流步进 (mA)"), { target: { value: "0.25" } });
  expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument();
  expect(current.steps.A08.status).toBe("quality_failed");
});
