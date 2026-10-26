import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./MxZOptimalControlRFSensitivityPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));

const ID = "mx-z-optimal-control-rf-sensitivity";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const { fields } = fixture.schema;
const { acquisition, analysis } = fixture.workflow;
const parameters = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
const old = { run_id: "0915_history", timestamp: "0915", run_tag: "historical", completion_status: "completed" };
let current: any;
let preflightOk: boolean;
const makeSession = () => ({ session_id: "mxz-session", job_id: "mxz-job", revision: 0, status: "waiting",
  parameters: parameters(), pending_request: null, outputs: { Z: "最优控制ON", 温控: "ON" },
  steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: "pending", error: "" }])) });

beforeEach(() => {
  current = null; preflightOk = true;
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { id: ID, title: "Mx Z 最优控制 RF 灵敏度", description: "成对正交校相与剩磁诊断", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return fixture.schema as never;
    if (url === `${BASE}/workflow`) return fixture.workflow as never;
    if (url === "/api/jobs") return [] as never;
    if (url === "/api/jobs/mxz-job") return { id: "mxz-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === `${BASE}/sessions/active`) return current as never;
    if (url === `${BASE}/sessions` && options?.method === "POST") return current = makeSession();
    if (url === `${BASE}/sessions/mxz-session/steps`) {
      if (body.step_ids.includes("A04") && !body.step_ids.includes("A03")) throw new Error("缺少前置：A03 控制工作点");
      return current = { ...current, revision: current.revision + 1,
        steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: body.step_ids.includes("A07") && ["A03", "A04", "A05", "A06"].includes(stage.id)
          ? "stale" : body.step_ids.includes(stage.id) ? "completed" : "pending", error: "" }])) };
    }
    if (url === `${BASE}/sessions/mxz-session/parameters`) return current = { ...current, parameters: body.parameters,
      revision: current.revision + 1, steps: Object.fromEntries(acquisition.map(stage => [stage.id, { status: "stale", error: "" }])) };
    if (url === `${BASE}/sessions/mxz-session/finish`) return current = { ...current, revision: current.revision + 1, status: "closed",
      steps: { ...current.steps, A12: { status: "completed", error: "" } } };
    if (url === `${BASE}/sessions/mxz-session`) return current as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old], total: 1 } as never;
    if (url === `${BASE}/runs/${old.run_id}/summary`) return { ...old, analysis_status: "not_started", analysis_error: "",
      parameters: { ...parameters(), RUN_TAG: "historical" }, artifacts: { "results/analysis.yaml": "/old.yaml", "results/phase_calibration.png": "/phase.png" } } as never;
    if (url === `${BASE}/runs/${old.run_id}/result`) return { measurement_mode: "rf_sensitivity", response_fit: { success: true }, selected_y_rf_phase_deg: 30 } as never;
    if (url === `${BASE}/runs/${old.run_id}/analysis` && options?.method === "POST") return { id: "analysis-job", status: "running", events: [] } as never;
    if (url === `${BASE}/runs/${old.run_id}/analysis`) return { run_id: old.run_id, revision: 0, options: {}, summary: {}, fingerprint: {},
      steps: Object.fromEntries(analysis.map(stage => [stage.id, { status: "pending", error: "" }])) } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["冻结来源无效"] } as never;
    if (url === `${BASE}/derive`) return { values: {} } as never;
    if (url === `${BASE}/defaults`) return { message: "已保存", schema: { ...fixture.schema, parameter_layout_saved: true, parameter_layout: body.parameter_layout } } as never;
    throw new Error(`未模拟接口 ${url}`);
  });
});
afterEach(cleanup);

const page = () => render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}>
  <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
</MemoryRouter>);
const editor = async (id: string) => {
  const stage = [...acquisition, ...analysis].find(item => item.id === id)!;
  const region = await screen.findByRole("region", { name: `${id} ${stage.title} 参数`, hidden: true });
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  return within(region);
};

it("真实路由保留双 Tab、完整可见参数与本实验质量门槛", async () => {
  page();
  await screen.findByRole("checkbox", { name: /A04/ });
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "历史" })).toBeVisible();
  for (const stage of [...acquisition, ...analysis]) for (const gate of stage.quality_gates) expect(screen.getByText(`检查与质量说明：${gate}`)).toBeVisible();
  const owned = new Set([...acquisition, ...analysis].flatMap(stage => stage.display_parameters));
  expect(fields.every(field => owned.has(field.name))).toBe(true);
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
});

it("缺前置拒绝、选择执行与无前置结束走真实实验会话接口", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: /A04/ }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByText(/Error: 缺少前置：A03/);
  fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await waitFor(() => expect(current.revision).toBe(1));
  expect(screen.getByRole("checkbox", { name: /A12/ })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current.status).toBe("closed"));
});

it("共享等待草稿跨节点同步，还原清预览，应用后显式重跑并恢复会话", async () => {
  current = makeSession();
  for (const key of ["A01", "A02", "A03", "A04", "A05", "A06"]) current.steps[key].status = "completed";
  page();
  const phase = await editor("A04");
  const amplitude = await editor("A05");
  const initial = current.parameters.SYSTEM_SETTLE_S;
  fireEvent.change(phase.getByLabelText("系统稳定时间 (s)"), { target: { value: "0.7" } });
  expect(amplitude.getByLabelText("系统稳定时间 (s)")).toHaveValue(.7);
  expect(screen.getByText(/预计需重跑的已完成步骤：A04/)).toBeVisible();
  fireEvent.change(amplitude.getByLabelText("系统稳定时间 (s)"), { target: { value: String(initial) } });
  expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument();
  fireEvent.change(amplitude.getByLabelText("系统稳定时间 (s)"), { target: { value: ".9" } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(current.parameters.SYSTEM_SETTLE_S).toBe(.9));
  expect(mockApi.mock.calls.filter(([url]) => String(url).endsWith("/steps"))).toHaveLength(0);
});

it("Tab 草稿保留、历史真实快照与科学图、填回不保存或运行，预检失败阻断", async () => {
  page();
  const tag = await editor("A02");
  fireEvent.change(tag.getByLabelText("运行标签"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "填回参数" });
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByText("historical")).toBeInTheDocument();
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByRole("img", { name: "results/phase_calibration.png" })).toHaveAttribute("src", "/phase.png");
  await within(screen.getByRole("tabpanel", { name: "历史" })).findByText(/selected_y_rf_phase_deg/);
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(tag.getByLabelText("运行标签")).toHaveValue("draft");
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("冻结来源无效");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(tag.getByLabelText("运行标签")).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("下一次分析参数保存不混入历史目录，无覆盖的目录仍可分步重算", async () => {
  page();
  const next = await editor("B02");
  fireEvent.change(next.getByLabelText("Y RF 线圈标定系数 (nT/Vpp)"), { target: { value: "2000" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  await screen.findByText(/正在编辑 .* 的分析覆盖值/);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(true));
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.Y_RF_NT_PER_VPP).toBe(2000);
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url.endsWith(`/${old.run_id}/analysis`) && options?.method === "POST"
    && Object.keys(JSON.parse(String(options.body)).analysis_parameters).length === 0)).toBe(true));
});

it("共享参数分类跨节点同步，保存的布局完整无重复且不改变数值", async () => {
  page();
  const phase = await editor("A04");
  const amplitude = await editor("A05");
  const before = phase.getByLabelText("系统稳定时间 (s)").getAttribute("value");
  fireEvent.click(phase.getByRole("button", { name: "系统稳定时间 移至高级参数" }));
  expect(amplitude.getByRole("button", { name: "系统稳定时间 移至基础参数", hidden: true })).toBeInTheDocument();
  expect(phase.getByLabelText("系统稳定时间 (s)")).toHaveAttribute("value", before);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(true));
  const body = JSON.parse(String(mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)![1]?.body));
  expect(body.parameter_layout.advanced).toContain("SYSTEM_SETTLE_S");
  expect(body.parameter_layout.basic).not.toContain("SYSTEM_SETTLE_S");
  expect(new Set([...body.parameter_layout.basic, ...body.parameter_layout.advanced]).size).toBe(fields.length);
});
