import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));

const ID = "mx-z-field-calibration";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "next", group: "basic", unit: "" },
  { name: "ZERO_BIAS_CENTER_FREQUENCY_HZ", label: "Z 零偏预测中心", type: "number", default: 90000, group: "basic", unit: "Hz" },
  { name: "Z_BIAS_STEP_V", label: "Z 电压步进", type: "number", default: 0.5, group: "basic", unit: "V" },
  { name: "LINEAR_R_SQUARED_MIN", label: "线性标定最低 R²", type: "number", default: 0.99, group: "basic", unit: "" },
];
const display: Record<string, string[]> = { A02: ["RUN_TAG"], A07: ["ZERO_BIAS_CENTER_FREQUENCY_HZ"],
  A08: ["ZERO_BIAS_CENTER_FREQUENCY_HZ", "Z_BIAS_STEP_V"], B03: ["LINEAR_R_SQUARED_MIN"] };
const gates: Record<string, string[]> = {
  A01: ["参数、接线映射、独立通道与安全限值有效"], A03: ["参考时钟严格回读一致"],
  A08: ["每点 R 标准差不超过 R_BAD_POINT_STD_THRESHOLD_V；重采用尽后保留诊断并阻断后继"],
  B01: ["实验身份、原始格式、索引路径和 R 频扫字段有效"],
  B03: ["记录有效 Lorentzian 中心与 LINEAR_R_SQUARED_MIN 判据；不合格保留掩码、NaN 和原因，仍可导出诊断"],
};
const acquisition = ["启动预检", "设备连接与初态回读", "参考时钟同步与回读", "辅助场归零、光功率与固定主场", "TEC 目标温度",
  "Pump 连续载波与常开门控", "Y RF 正弦与 R 解调准备", "Z 电压与局部 Y RF 扫频", "本轮安全收尾与封存", "安全结束会话"].map((title, index) => {
  const id = `A${String(index + 1).padStart(2, "0")}`;
  return { id, title, group: "采集", dependencies: index && index !== 9 ? [`A${String(index).padStart(2, "0")}`] : [],
    parameters: display[id] ?? [], display_parameters: display[id] ?? [], quality_gates: gates[id] ?? [], artifacts: [] };
});
const analysis = ["配置与原始频扫资格", "逐 Z 电压 Lorentzian 共振中心", "加权线性 Z 电压标定", "标定报告与结果图"].map((title, index) => {
  const id = `B0${index + 1}`;
  return { id, title, group: "独立分析", dependencies: index ? [`B0${index}`] : [],
    parameters: display[id] ?? [], display_parameters: display[id] ?? [], quality_gates: gates[id] ?? [], artifacts: [] };
});
const layout = { basic: fields.map(field => field.name), advanced: [] };
const old = { run_id: "1014_old", timestamp: "1014", run_tag: "old", completion_status: "completed" };
const other = { ...old, run_id: "1014_other", run_tag: "other" };
let current: any;
let preflightOk: boolean;
let applyError: boolean;
let storedLayout: typeof layout;
let storedValues: ReturnType<typeof parameters>;
const parameters = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const makeSession = () => ({ session_id: "mx-session", job_id: "mx-job", status: "waiting", revision: 0,
  parameters: parameters(), pending_request: null, outputs: { "Y RF": "OFF", 温控: "ON" },
  steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: "pending", error: "" }])) });

beforeEach(() => {
  current = null; preflightOk = true; applyError = false;
  storedLayout = structuredClone(layout); storedValues = parameters();
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { id: ID, title: "Mx 高主场 Z 磁场频率标定", description: "R-only", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields: fields.map(field => ({ ...field, default: storedValues[field.name] })),
      parameter_layout_saved: true, parameter_layout: storedLayout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === "/api/jobs/mx-job") return { id: "mx-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === `${BASE}/workflow`) return { acquisition, analysis,
      parameter_impacts: { acquisition: { RUN_TAG: acquisition.slice(1, 9).map(stage => stage.id), ZERO_BIAS_CENTER_FREQUENCY_HZ: ["A07", "A08", "A09"], Z_BIAS_STEP_V: ["A08", "A09"] },
        analysis: { LINEAR_R_SQUARED_MIN: ["B03", "B04"] } }, diagram_url: `${BASE}/workflow/diagram` } as never;
    if (url === `${BASE}/sessions/active`) return current as never;
    if (url === `${BASE}/sessions` && options?.method === "POST") return current = makeSession();
    if (url === `${BASE}/sessions/mx-session/steps`) {
      if (body.step_ids.includes("A08") && !body.step_ids.includes("A07")) throw new Error("缺少前置：A07 Y RF 正弦与 R 解调准备");
      if (body.step_ids.includes("A09") && current.steps.A08.status === "quality_failed" && !body.step_ids.includes("A08"))
        throw new Error("A09 缺少前置模块：A08 质量未通过");
      const states = Object.fromEntries(acquisition.map(stage => [stage.id,
        { status: body.step_ids.includes(stage.id) ? "completed" : current.steps[stage.id].status, error: "" }]));
      if (body.step_ids.includes("A09")) for (const id of ["A07", "A08"]) states[id].status = "stale";
      return current = { ...current, revision: current.revision + 1,
        steps: states };
    }
    if (url === `${BASE}/sessions/mx-session/parameters`) {
      if (applyError) throw new Error("参数预检失败：扫频下限必须大于 0 Hz");
      return current = { ...current, parameters: body.parameters, revision: current.revision + 1,
        steps: Object.fromEntries(acquisition.map(stage => [stage.id,
          { status: ["A07", "A08", "A09"].includes(stage.id) && current.steps[stage.id].status !== "pending" ? "stale" : current.steps[stage.id].status, error: "" }])) };
    }
    if (url === `${BASE}/sessions/mx-session/finish`) return current = { ...current, revision: current.revision + 1, status: "closed",
      steps: { ...current.steps, A10: { status: "completed", error: "" } } };
    if (url === `${BASE}/sessions/mx-session`) return current as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old, other], total: 2 } as never;
    if ([old, other].some(run => url === `${BASE}/runs/${run.run_id}/summary`)) return { ...([old, other].find(run => url === `${BASE}/runs/${run.run_id}/summary`)!), analysis_status: "not_started", analysis_error: "",
      parameters: { ...parameters(), RUN_TAG: "historical", Z_BIAS_STEP_V: 0.25 }, artifacts: Object.fromEntries(
        ["z_frequency_calibration.png", "frequency_response_fits.png", "z_frequency_residuals.png", "analysis.json"].map(
          name => [`results/${name}`, `${BASE}/runs/${old.run_id}/files/results/${name}`])) } as never;
    if (url === `${BASE}/runs/${old.run_id}/analysis` && options?.method === "POST") return { id: "analysis-job", status: "running", events: [] } as never;
    if ([old, other].some(run => url === `${BASE}/runs/${run.run_id}/analysis`)) return { run_id: String(url).includes(other.run_id) ? other.run_id : old.run_id, revision: 0,
      options: { LINEAR_R_SQUARED_MIN: String(url).includes(other.run_id) ? 0.97 : 0.98 }, steps: Object.fromEntries(analysis.map(stage => [stage.id, { status: "completed", error: "" }])), summary: {}, fingerprint: {} } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["预测扫频下限必须大于 0 Hz"] } as never;
    if (url === `${BASE}/derive`) return { values: {} } as never;
    if (url === `${BASE}/defaults`) {
      storedLayout = structuredClone(body.parameter_layout); storedValues = { ...body.parameters };
      return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: storedLayout } } as never;
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
  fireEvent.click(screen.getByRole("checkbox", { name: "A08 Z 电压与局部 Y RF 扫频" }));
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
  for (const stage of acquisition.slice(0, 8)) current.steps[stage.id].status = "completed";
  page();
  const sweep = await editor("A07");
  const amplitude = await editor("A08");
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
  for (const stage of acquisition.slice(0, 7)) current.steps[stage.id].status = "completed";
  const before = structuredClone(current);
  applyError = true;
  page();
  const first = await editor("A07");
  const second = await editor("A08");
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
  const second = await editor("A08");
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
  const reloaded = await editor("A08");
  fireEvent.click(reloaded.getByText("高级参数（1）"));
  expect(reloaded.getByRole("button", { name: "Z 零偏预测中心 移至基础参数" })).toBeVisible();
  expect(reloaded.getByLabelText("Z 零偏预测中心 (Hz)")).toHaveValue(90500);
});

it("质量门槛和实际失败记录位于对应节点，失败后允许安全结束", async () => {
  current = makeSession();
  for (const stage of acquisition.slice(0, 7)) current.steps[stage.id].status = "completed";
  current.run_id = "1014_failed";
  current.steps.A08 = { status: "quality_failed", error: "frequency_0000 连续 3 次 std(R) > 0.01 V" };
  page();
  for (const [id, values] of Object.entries(gates)) {
    const stage = [...acquisition, ...analysis].find(item => item.id === id)!;
    const checkbox = await screen.findByRole("checkbox", { name: `${id} ${stage.title}` });
    expect(within(checkbox.closest("li")!).getByText(`质量门槛：${values[0]}`)).toBeVisible();
  }
  const failed = screen.getByRole("checkbox", { name: "A08 Z 电压与局部 Y RF 扫频" }).closest("li")!;
  expect(within(failed).getByText("质量未通过")).toBeVisible();
  expect(within(failed).getByText(/连续 3 次 std/)).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox", { name: "A09 本轮安全收尾与封存" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: A09 缺少前置模块：A08 质量未通过/);
  expect(screen.getByRole("button", { name: "结束会话" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current.status).toBe("closed"));
});

it("使用真实摘要的 results 路径保留三张科学图与分析下载", async () => {
  page();
  expect(await screen.findByRole("img", { name: "Z 电压与共振中心" })).toHaveAttribute("src",
    `${BASE}/runs/${old.run_id}/files/results/z_frequency_calibration.png`);
  expect(screen.getByRole("img", { name: "各 Z 电压的 R 频扫拟合" })).toBeVisible();
  expect(screen.getByRole("img", { name: "线性拟合残差" })).toBeVisible();
  expect(screen.getByRole("link", { name: "results/analysis.json" })).toHaveAttribute("href",
    `${BASE}/runs/${old.run_id}/files/results/analysis.json`);
});
