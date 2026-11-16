import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./CoilConstantCalibrationPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => <span>采集任务状态</span> }));
const ID = "coil-constant-calibration", BASE = `/api/experiments/${ID}`;
const { fields, flow } = fixture;
const mockApi = vi.mocked(api);
const parameters = () => Object.fromEntries(fields.map(f => [f.name, f.default]));
const layout = () => ({ basic: fields.filter(f => f.group === "basic").map(f => f.name),
  advanced: fields.filter(f => f.group !== "basic").map(f => f.name) });
const old = { run_id: "1116_old", timestamp: "1116", run_tag: "old", completion_status: "completed" };
let current: any, preflightOk: boolean;
const makeSession = () => ({ session_id: "coil-const-session", job_id: "coil-const-job", status: "waiting", revision: 0,
  parameters: parameters(), pending_request: null, outputs: { 主场: "OFF", 功放: "OFF", "Z 通道": "未知" },
  steps: Object.fromEntries(flow.acquisition.map(s => [s.id, { status: "pending", error: "" }])) });

beforeEach(() => {
  current = null; preflightOk = true;
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { id: ID, title: "Z 线圈常数标定", description: "电流→场常数与过零标定", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: layout() } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === "/api/jobs/coil-const-job") return { id: "coil-const-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === "/api/jobs/analysis-job") return { id: "analysis-job", status: "running", events: [] } as never;
    if (url === `${BASE}/workflow`) return flow as never;
    if (url === `${BASE}/sessions/active`) return current as never;
    if (url === `${BASE}/sessions` && options?.method === "POST") return current = makeSession();
    if (url === `${BASE}/sessions/coil-const-session/steps`) {
      if (body.step_ids.includes("A04") && !body.step_ids.includes("A03")) throw new Error("A04 功放零偏与直流 I–V 核对 缺少前置模块：A03 参考时钟同步与严格回读");
      return current = { ...current, revision: current.revision + 1,
        steps: Object.fromEntries(flow.acquisition.map(s => [s.id,
          { status: body.step_ids.includes(s.id) ? "completed" : "pending", error: "" }])) };
    }
    if (url === `${BASE}/sessions/coil-const-session/parameters`) {
      const changed = Object.keys(body.parameters).filter(k => JSON.stringify(body.parameters[k]) !== JSON.stringify(current.parameters[k]));
      const affected = new Set(changed.flatMap(k => (flow.parameter_impacts.acquisition as Record<string, string[]>)[k] ?? []));
      return current = { ...current, parameters: body.parameters, revision: current.revision + 1,
        steps: Object.fromEntries(Object.entries(current.steps).map(([id, state]: [string, any]) => [id,
          affected.has(id) && state.status === "completed" ? { status: "stale", error: "" } : state])) };
    }
    if (url === `${BASE}/sessions/coil-const-session/finish`) return current = { ...current, revision: current.revision + 1, status: "closed" };
    if (url === `${BASE}/sessions/coil-const-session`) return current as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old], total: 1 } as never;
    if (url === `${BASE}/runs/${old.run_id}/summary`) return { ...old, analysis_status: "completed", analysis_error: "",
      parameters: { ...parameters(), RUN_TAG: "historical", CURRENT_STEP_MA: 25 }, artifacts: {} } as never;
    if (url === `${BASE}/runs/${old.run_id}/analysis` && options?.method === "POST") return { id: "analysis-job", status: "running", events: [] } as never;
    if (url === `${BASE}/runs/${old.run_id}/analysis`) return { run_id: old.run_id, revision: 0, options: { NEAR_ZERO_FIT_FLOOR_HZ: 4000 },
      steps: Object.fromEntries(flow.analysis.map(s => [s.id, { status: "completed", error: "" }])), summary: {}, fingerprint: {} } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["z_pulse_coil_current 必须有有限上下界"] } as never;
    if (url === `${BASE}/derive`) return { values: { crossing_current_ma: -149.5, crossing_inside: true, branch_counts: { below: 4, above: 13 } } } as never;
    if (url === `${BASE}/defaults`) return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: body.parameter_layout } } as never;
    throw new Error(`未模拟接口 ${url}`);
  });
});
afterEach(cleanup);
const page = () => render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}>
  <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
</MemoryRouter>);
const node = async (id: string) => {
  const stage = [...flow.acquisition, ...flow.analysis].find(s => s.id === id)!;
  const region = await screen.findByRole("region", { name: `${id} ${stage.title} 参数`, hidden: true });
  const details = region.closest("details")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  for (const advanced of region.querySelectorAll<HTMLDetailsElement>("details")) advanced.open = true;
  return within(region);
};
const label = (id: string) => `${id} ${flow.acquisition.find(s => s.id === id)!.title}`;

it("真实路由提供 11/4 节点，缺前置拒绝，可单独执行并安全结束", async () => {
  page();
  await waitFor(() => expect(screen.getByRole("button", { name: "创建分步会话" })).toBeEnabled());
  expect(flow.acquisition.map(s => s.id)).toEqual(["A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09", "A10"]);
  expect(flow.analysis.map(s => s.id)).toEqual(["B01", "B02", "B03", "B04"]);
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/分步等待期间保持输出状态/);
  fireEvent.click(screen.getByRole("checkbox", { name: label("A04") }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选" }));
  await screen.findByText(/缺少前置模块：A03/);
  for (const id of ["A01", "A02", "A03"]) fireEvent.click(screen.getByRole("checkbox", { name: label(id) }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选" }));
  await waitFor(() => expect(current.steps.A04.status).toBe("completed"));
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current.status).toBe("closed"));
  expect(screen.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
});

it("全部可见参数都有编辑入口，顶部含保存、预检与完整运行", async () => {
  page(); await screen.findByRole("button", { name: "创建分步会话" });
  const owned = new Set([...flow.acquisition, ...flow.analysis].flatMap(s => s.display_parameters));
  expect([...owned].sort()).toEqual(fields.map(f => f.name).sort());
  const top = screen.getByRole("button", { name: "保存参数与布局" }).closest(".xy-workflow-controls")!;
  expect(top).toContainElement(screen.getByRole("button", { name: "仅预检" }));
  expect(top).toContainElement(screen.getByRole("button", { name: "预检并运行实验" }));
});

it("预检失败阻止完整运行，历史填回不保存不启动", async () => {
  page(); const preflight = await node("A01");
  fireEvent.change(preflight.getByLabelText("运行标签"), { target: { value: "draft" } });
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("z_pulse_coil_current 必须有有限上下界");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  expect(preflight.getByLabelText("运行标签")).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("历史目录的零场拟合下限覆盖值独立于下一次实验参数", async () => {
  page(); const next = await node("B03");
  fireEvent.change(next.getByLabelText("零场不拟合下限 (Hz)"), { target: { value: "5500" } });
  fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: old.run_id } });
  const field = () => within(screen.getByRole("region", { name: `B03 ${flow.analysis[2].title} 参数`, hidden: true }))
    .getByLabelText("零场不拟合下限 (Hz)");
  await waitFor(() => expect(field()).toHaveValue(4000));
  fireEvent.change(field(), { target: { value: "6000" } });
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" })); await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.NEAR_ZERO_FIT_FLOOR_HZ).toBe(5500);
  fireEvent.click(screen.getByRole("button", { name: "全选分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs/${old.run_id}/analysis`
    && options?.method === "POST" && JSON.parse(String(options.body)).analysis_parameters.NEAR_ZERO_FIT_FLOOR_HZ === 6000)).toBe(true));
});
