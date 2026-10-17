import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));

const ID = "mx-y-rf-sensitivity";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "next", group: "basic", unit: "" },
  { name: "SYSTEM_SETTLE_S", label: "系统稳定时间", type: "number", default: 0.3, group: "basic", unit: "s" },
  { name: "Y_RF_AMP_POINTS", label: "Y RF 幅度点数", type: "integer", default: 81, group: "basic", unit: "" },
  { name: "LOW_FREQ_SKIP_HZ", label: "平坦段搜索最低频率", type: "number", default: 3, group: "basic", unit: "Hz" },
];
const display: Record<string, string[]> = { A02: ["RUN_TAG"], A08: ["SYSTEM_SETTLE_S"],
  A09: ["SYSTEM_SETTLE_S", "Y_RF_AMP_POINTS"], A10: ["SYSTEM_SETTLE_S"], B02: ["LOW_FREQ_SKIP_HZ"] };
const qualityGates: Record<string, string[]> = {
  A01: ["参数、映射、锁相采集能力与安全限值全部有效"],
  A03: ["参考时钟严格回读一致"],
  A08: ["扫频模式中 R 点质量、FIT_R_SQUARED_MIN 和 FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX 均通过；幅度等效线宽模式保留既有免扫频规则"],
  A09: ["R 点标准差合格；重采用尽后保存诊断并阻断后继"],
  B01: ["运行身份、原始格式及必需响应/噪声文件与字段符合要求，目录已封存"],
};
const acquisition = ["启动预检", "设备连接与初态回读", "参考时钟同步与回读", "主场、光功率与辅助场归零", "TEC 目标温度",
  "Pump 连续载波与常开门控", "Y RF 正弦与 R 解调准备", "线宽模式与可选扫频质量门槛", "带符号 Y RF 幅度响应",
  "零 Y RF 的 R 噪声", "响应模式恢复与本轮安全收尾", "安全结束会话"].map((title, index) => {
  const id = `A${String(index + 1).padStart(2, "0")}`;
  return { id, title, group: "采集", dependencies: index && index !== 11 ? [`A${String(index).padStart(2, "0")}`] : [],
    parameters: display[id] ?? [], display_parameters: display[id] ?? [], artifacts: [], quality_gates: qualityGates[id] ?? [] };
});
const analysis = ["加载配置与原始数据资格", "绝对色散、线宽、R PSD 与灵敏度", "结果图与两种斜率报告"].map((title, index) => {
  const id = `B0${index + 1}`;
  return { id, title, group: "独立分析", dependencies: index ? [`B0${index}`] : [],
    parameters: display[id] ?? [], display_parameters: display[id] ?? [], artifacts: [], quality_gates: qualityGates[id] ?? [] };
});
const layout = { basic: fields.map(field => field.name), advanced: [] };
const old = { run_id: "1014_old", timestamp: "1014", run_tag: "old", completion_status: "completed" };
let current: any;
let preflightOk: boolean;
const parameters = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const makeSession = () => ({ session_id: "mx-session", job_id: "mx-job", status: "waiting", revision: 0,
  parameters: parameters(), pending_request: null, outputs: { "Y RF": "OFF", 温控: "ON" },
  steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: "pending", error: "" }])) });

beforeEach(() => {
  current = null; preflightOk = true;
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { id: ID, title: "Mx Y 向 RF 场灵敏度", description: "R-only", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: layout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === "/api/jobs/mx-job") return { id: "mx-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === `${BASE}/workflow`) return { acquisition, analysis,
      parameter_impacts: { acquisition: { RUN_TAG: ["A02", "A09"], SYSTEM_SETTLE_S: ["A08", "A09", "A10", "A11"], Y_RF_AMP_POINTS: ["A09", "A10", "A11"] },
        analysis: { LOW_FREQ_SKIP_HZ: ["B02", "B03"] } }, diagram_url: `${BASE}/workflow/diagram` } as never;
    if (url === `${BASE}/sessions/active`) return current as never;
    if (url === `${BASE}/sessions` && options?.method === "POST") return current = makeSession();
    if (url === `${BASE}/sessions/mx-session/steps`) {
      if (body.step_ids.includes("A09") && !body.step_ids.includes("A08")) throw new Error("缺少前置：A08 线宽模式与可选扫频质量门槛");
      return current = { ...current, revision: current.revision + 1,
        steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: body.step_ids.includes("A11") && ["A07", "A08", "A09", "A10"].includes(stage.id)
          ? "stale" : body.step_ids.includes(stage.id) ? "completed" : "pending", error: "" }])) };
    }
    if (url === `${BASE}/sessions/mx-session/parameters`) return current = { ...current, parameters: body.parameters,
      revision: current.revision + 1, steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: "stale", error: "" }])) };
    if (url === `${BASE}/sessions/mx-session/finish`) return current = { ...current, revision: current.revision + 1, status: "closed" };
    if (url === `${BASE}/sessions/mx-session`) return current as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old], total: 1 } as never;
    if (url === `${BASE}/runs/${old.run_id}/summary`) return { ...old, analysis_status: "not_started", analysis_error: "",
      parameters: { ...parameters(), RUN_TAG: "historical", Y_RF_AMP_POINTS: 41 }, artifacts: {} } as never;
    if (url === `${BASE}/runs/${old.run_id}/analysis` && options?.method === "POST") return { id: "analysis-job", status: "running", events: [] } as never;
    if (url === `${BASE}/runs/${old.run_id}/analysis`) return { run_id: old.run_id, revision: 0,
      options: { LOW_FREQ_SKIP_HZ: 8 }, steps: Object.fromEntries(analysis.map(stage => [stage.id, { status: "completed", error: "" }])), summary: {}, fingerprint: {} } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["幅度范围必须跨过 0"] } as never;
    if (url === `${BASE}/derive`) return { values: {} } as never;
    if (url === `${BASE}/defaults`) return { message: "已保存", schema: { fields, parameter_layout_saved: true,
      parameter_layout: body.parameter_layout } } as never;
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
  for (const gates of Object.values(qualityGates)) {
    expect(screen.getByText(`质量门槛：${gates[0]}`)).toBeVisible();
  }
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: "A09 带符号 Y RF 幅度响应" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: 缺少前置：A08/);
  expect(screen.getByRole("checkbox", { name: "A08 线宽模式与可选扫频质量门槛" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(current.revision).toBe(1));
  expect(screen.getByRole("checkbox", { name: "A12 安全结束会话" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current.status).toBe("closed"));
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
});

it("共享过程参数草稿同步、还原清除预览、应用后显式重跑，恢复存活会话", async () => {
  current = makeSession();
  for (const [id, state] of Object.entries(current.steps) as [string, any][]) {
    if (!["A11", "A12"].includes(id)) state.status = "completed";
  }
  page();
  const sweep = await editor("A08");
  const amplitude = await editor("A09");
  fireEvent.change(sweep.getByLabelText("系统稳定时间 (s)"), { target: { value: "0.7" } });
  expect(amplitude.getByLabelText("系统稳定时间 (s)")).toHaveValue(0.7);
  expect(screen.getByText(/预计需重跑的已完成步骤：A08/)).toBeVisible();
  fireEvent.change(amplitude.getByLabelText("系统稳定时间 (s)"), { target: { value: "0.3" } });
  expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument();
  fireEvent.change(amplitude.getByLabelText("系统稳定时间 (s)"), { target: { value: "0.9" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(current.parameters.SYSTEM_SETTLE_S).toBe(0.9));
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
  await screen.findByText("幅度范围必须跨过 0");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(tag.getByLabelText("运行标签")).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("历史分析草稿与下一次参数隔离，保存默认值不包含历史覆盖", async () => {
  page();
  const next = await editor("B02");
  fireEvent.change(next.getByLabelText("平坦段搜索最低频率 (Hz)"), { target: { value: "5" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await waitFor(() => expect(screen.getByLabelText("平坦段搜索最低频率 (Hz)")).toHaveValue(8));
  fireEvent.change(screen.getByLabelText("平坦段搜索最低频率 (Hz)"), { target: { value: "11" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "" } });
  await waitFor(() => expect(screen.getByLabelText("平坦段搜索最低频率 (Hz)")).toHaveValue(5));
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await waitFor(() => expect(screen.getByLabelText("平坦段搜索最低频率 (Hz)")).toHaveValue(11));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.LOW_FREQ_SKIP_HZ).toBe(5);
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs/${old.run_id}/analysis`
    && options?.method === "POST" && JSON.parse(String(options.body)).analysis_parameters.LOW_FREQ_SKIP_HZ === 11)).toBe(true));
});
