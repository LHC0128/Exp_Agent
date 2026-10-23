import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import fixture from "./MxYRFPowerOptimizationPage.fixture.json";
import type { ParameterLayout, ParameterValues, SchemaField } from "../types/api";
import type { XYSession } from "../components/NoiseSpectrumXY/WorkflowPanel";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));

const ID = "mx-y-rf-power-optimization", BASE = `/api/experiments/${ID}`;
const fields = fixture.fields as SchemaField[], flow = fixture.flow;
const mockApi = vi.mocked(api);
const parameters = (): ParameterValues => Object.fromEntries(fields.map(field => [field.name, field.default]));
const initialLayout = (): ParameterLayout => ({
  basic: fields.filter(field => field.group === "basic").map(field => field.name),
  advanced: fields.filter(field => field.group !== "basic").map(field => field.name),
});
const histories = ["1017_power_old", "1017_power_other"].map(run_id => ({
  run_id, timestamp: "2026-10-17", run_tag: run_id, completion_status: "completed",
}));
let current: XYSession | null, preflightOk: boolean, oldMetadata: boolean;
let savedLayout: ParameterLayout;
const makeSession = (): XYSession => ({ session_id: "power-session", job_id: "power-job", status: "waiting",
  revision: 0, parameters: parameters(), pending_request: null, outputs: { "Y RF": "OFF", 温控: "ON" },
  output_source: "假仪器最近写入", demod_mode: "response",
  steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: "pending", error: "" }])) });
const optionValues = (id: string) => ({ ...Object.fromEntries(flow.analysis[1].parameters.map(key =>
  [key, parameters()[key]])), LOW_FREQ_SKIP_HZ: id === histories[0].run_id ? 8 : 9 });

beforeEach(() => {
  current = null; preflightOk = true; oldMetadata = false; savedLayout = initialLayout();
  mockApi.mockReset(); localStorage.clear();
  mockApi.mockImplementation(async (url, options) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {};
    if (url === BASE) return { id: ID, title: "Mx Y RF 光功率灵敏度优化", description: "蛇形功率网格与双斜率排名", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: savedLayout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === "/api/jobs/power-job") return { id: "power-job", kind: `session:${ID}`, status: "running", events: [] } as never;
    if (url === `${BASE}/workflow`) return (oldMetadata ? { ...flow, parameter_impacts: undefined } : flow) as never;
    if (url === `${BASE}/sessions/active` || url === `${BASE}/sessions/power-session`) return current as never;
    if (url === `${BASE}/sessions`) return (current = makeSession()) as never;
    if (url === `${BASE}/sessions/power-session/steps`) {
      const available = new Set(Object.entries(current!.steps).filter(([, state]) => state.status === "completed").map(([id]) => id));
      for (const stage of flow.acquisition.filter(stage => body.step_ids.includes(stage.id))) {
        const missing = stage.dependencies.filter(id => !available.has(id));
        if (missing.length) throw new Error("缺少前置：" + missing.join("、"));
        available.add(stage.id);
      }
      return (current = { ...current!, revision: current!.revision + 1, steps: Object.fromEntries(flow.acquisition.map(stage =>
        [stage.id, { status: available.has(stage.id) ? "completed" : "pending", error: "" }])) }) as never;
    }
    if (url === `${BASE}/sessions/power-session/parameters`) {
      const changed = Object.keys(body.parameters).filter(key => body.parameters[key] !== current!.parameters[key]);
      const impacts = flow.parameter_impacts.acquisition as Record<string, string[]>;
      const affected = new Set(changed.flatMap(key => impacts[key] ?? []));
      return (current = { ...current!, parameters: body.parameters, revision: current!.revision + 1,
        steps: Object.fromEntries(Object.entries(current!.steps).map(([id, state]) =>
          [id, affected.has(id) ? { status: "stale", error: "" } : state])) }) as never;
    }
    if (url === `${BASE}/sessions/power-session/finish`) return (current = { ...current!, status: "closed" }) as never;
    if (url.startsWith(`${BASE}/runs?`)) return { runs: histories, total: 2 } as never;
    const summary = url.match(/\/runs\/([^/]+)\/summary$/);
    if (summary) return { ...histories.find(item => item.run_id === summary[1]), analysis_status: "not_started",
      analysis_error: "", parameters: { ...parameters(), RUN_TAG: "historical" }, artifacts: {} } as never;
    const analysis = url.match(/\/runs\/([^/]+)\/analysis$/);
    if (analysis && options?.method === "POST") return { id: "power-analysis", status: "completed", events: [] } as never;
    if (analysis) return { run_id: analysis[1], revision: 0, options: optionValues(analysis[1]),
      steps: Object.fromEntries(flow.analysis.map(stage => [stage.id, { status: "completed", error: "" }])),
      fingerprint: {}, summary: {} } as never;
    if (url === `${BASE}/derive`) return { values: { points: 49 } } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["功率超出安全范围"] } as never;
    if (url === `${BASE}/defaults`) {
      savedLayout = body.parameter_layout;
      return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: savedLayout } } as never;
    }
    throw new Error(`未模拟请求：${url}`);
  });
});
afterEach(cleanup);

function page() {
  return render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}><Routes>
    <Route path="/experiments/:experimentId" element={<ExperimentPage />} />
  </Routes></MemoryRouter>);
}
async function editor(id: string) {
  const stage = [...flow.acquisition, ...flow.analysis].find(stage => stage.id === id)!;
  const checkbox = await screen.findByRole("checkbox", { name: `${id} ${stage.title}` });
  const node = checkbox.closest("li")!;
  const details = node.querySelector<HTMLDetailsElement>(".xy-node-parameters")!;
  if (!details.open) fireEvent.click(details.querySelector("summary")!);
  return within(node);
}
const label = (key: string) => {
  const field = fields.find(field => field.name === key)!;
  return field.label + (field.unit ? ` (${field.unit})` : "");
};

it("真实路由提供两列流程、顶部操作、原图和缺前置拒绝", async () => {
  page();
  const node = await editor("A08");
  const container = screen.getByRole("region", { name: "交互实验流程图" });
  expect(within(container).getByRole("button", { name: "保存参数与布局" })).toBeInTheDocument();
  expect(within(container).getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", flow.diagram_url);
  expect(screen.getAllByRole("tab").map(tab => tab.textContent)).toEqual(["运行", "历史"]);
  expect(new Set([...flow.acquisition, ...flow.analysis].flatMap(stage => stage.display_parameters)))
    .toEqual(new Set(fields.map(field => field.name)));
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  await screen.findByText(/等待下一步/);
  fireEvent.click(node.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
  await screen.findByRole("alert", { name: "" });
  expect(screen.getAllByText(/缺少前置：A07/).length).toBeGreaterThan(0);
});

it("共享质量参数跨采集分析同步、多个节点展开、分类保存回读", async () => {
  const mounted = page();
  const a = await editor("A08"), b = await editor("B02");
  const key = "R_BAD_POINT_STD_THRESHOLD_V";
  fireEvent.change(a.getByLabelText(label(key)), { target: { value: "0.02" } });
  expect(b.getByLabelText(label(key))).toHaveValue(0.02);
  fireEvent.click(a.getByText(/^高级参数（/));
  fireEvent.click(a.getByRole("button", { name: `${fields.find(field => field.name === key)!.label} 移至基础参数` }));
  expect(b.getByRole("button", { name: `${fields.find(field => field.name === key)!.label} 移至高级参数` })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  expect(savedLayout.basic).toContain(key);
  mounted.unmount(); page();
  const reloaded = await editor("A08");
  expect(reloaded.getByRole("button", { name: `${fields.find(field => field.name === key)!.label} 移至高级参数` })).toBeVisible();
});

it("草稿还原清除预览，应用后显式重跑，刷新可恢复与结束会话", async () => {
  current = makeSession();
  current.steps = Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: "completed", error: "" }]));
  page();
  const a = await editor("A08");
  await screen.findByText(/等待下一步/);
  const input = a.getByLabelText(label("SYSTEM_SETTLE_S"));
  const initial = parameters().SYSTEM_SETTLE_S;
  fireEvent.change(input, { target: { value: Number(initial) + 1 } });
  expect(screen.getByText(/预计需重跑的已完成步骤：A08/)).toBeInTheDocument();
  fireEvent.change(input, { target: { value: initial } });
  expect(screen.queryByText(/预计需重跑的已完成步骤：/)).not.toBeInTheDocument();
  fireEvent.change(input, { target: { value: Number(initial) + 1 } });
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(current!.steps.A08.status).toBe("stale"));
  expect(mockApi.mock.calls.some(([url]) => url.endsWith("/steps"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await screen.findByText(/下方状态为已结束会话/);
});

it("Tab保留草稿、历史填回不保存或运行，预检失败阻止启动", async () => {
  page();
  const a = await editor("A02");
  fireEvent.change(a.getByLabelText(label("RUN_TAG")), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "填回参数" });
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(a.getByLabelText(label("RUN_TAG"))).toHaveValue("draft");
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("功率超出安全范围");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(a.getByLabelText(label("RUN_TAG"))).toHaveValue("historical");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
});

it("两个历史目录与下一次实验的分析草稿隔离，默认保存不混入历史覆盖", async () => {
  page();
  const b = await editor("B02");
  fireEvent.change(b.getByLabelText(label("LOW_FREQ_SKIP_HZ")), { target: { value: "5" } });
  const choose = screen.getByLabelText("分析操作对象");
  fireEvent.change(choose, { target: { value: histories[0].run_id } });
  await waitFor(() => expect(screen.getByLabelText(label("LOW_FREQ_SKIP_HZ"))).toHaveValue(8));
  fireEvent.change(screen.getByLabelText(label("LOW_FREQ_SKIP_HZ")), { target: { value: "11" } });
  fireEvent.change(choose, { target: { value: histories[1].run_id } });
  await waitFor(() => expect(screen.getByLabelText(label("LOW_FREQ_SKIP_HZ"))).toHaveValue(9));
  fireEvent.change(choose, { target: { value: "" } });
  await waitFor(() => expect(screen.getByLabelText(label("LOW_FREQ_SKIP_HZ"))).toHaveValue(5));
  fireEvent.change(choose, { target: { value: histories[0].run_id } });
  await waitFor(() => expect(screen.getByLabelText(label("LOW_FREQ_SKIP_HZ"))).toHaveValue(11));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const saved = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  expect(JSON.parse(String(saved[1]?.body)).parameters.LOW_FREQ_SKIP_HZ).toBe(5);
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs/${histories[0].run_id}/analysis`
    && options?.method === "POST" && JSON.parse(String(options.body)).analysis_parameters.LOW_FREQ_SKIP_HZ === 11)).toBe(true));
});

it("旧元数据报错仍恢复活动会话并允许安全结束", async () => {
  current = makeSession(); oldMetadata = true; page();
  await screen.findByText(/流程接口版本过旧/);
  await screen.findByText(/等待下一步/);
  fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(current!.status).toBe("closed"));
});
