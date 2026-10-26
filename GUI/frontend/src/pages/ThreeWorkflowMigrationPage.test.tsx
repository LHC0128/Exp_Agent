import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";
import t2 from "./T2CalibrationPage.fixture.json";
import rf from "./RFDirectAWFrequencyPage.fixture.json";
import xy from "./XYDirectAWDCCalibrationPage.fixture.json";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));
vi.mock("../components/JobView", () => ({ JobView: () => <span>采集任务状态</span> }));
const mocked = vi.mocked(api);
afterEach(cleanup);

const experiments = [
  { id: "t2-calibration", fixture: t2, shared: "PROBE_POWER", first: "A04", second: "A06" },
  { id: "rf-sensitivity-direct-aw-frequency", fixture: rf, shared: "SYSTEM_SETTLE_S", first: "A04", second: "A06" },
  { id: "xy-direct-aw-dc-calibration", fixture: xy, shared: "SYSTEM_SETTLE_S", first: "A04", second: "A06" },
];

describe.each(experiments)("$id 真实路由", ({ id, fixture, shared, first, second }) => {
  const BASE = `/api/experiments/${id}`;
  const fields = fixture.fields;
  const flow = fixture.flow;
  const values = () => Object.fromEntries(fields.map(field => [field.name, field.default]));
  const layout = () => ({ basic: fields.filter(f => f.group === "basic").map(f => f.name), advanced: fields.filter(f => f.group !== "basic").map(f => f.name) });
  let savedLayout: ReturnType<typeof layout>;
  let session: any, preflightOk: boolean, metadata: boolean | "run_step_id" | "finish_step_id";
  const history = { run_id: "old", timestamp: "old-date", run_tag: "historical", completion_status: "completed" };
  const makeSession = () => ({ session_id: "session", job_id: "hardware-job", status: "waiting", revision: 0,
    parameters: values(), pending_request: null, outputs: { 温控: "5 V DC + ON" },
    steps: Object.fromEntries(flow.acquisition.map(s => [s.id, { status: "pending", error: "" }])) });
  const stageLabel = (sid: string) => `${sid} ${flow.acquisition.find(s => s.id === sid)!.title}`;
  const page = () => render(<MemoryRouter initialEntries={[`/experiments/${id}`]}>
    <Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes>
  </MemoryRouter>);
  const node = async (sid: string) => {
    const stage = [...flow.acquisition, ...flow.analysis].find(s => s.id === sid)!;
    const region = await screen.findByRole("region", { name: `${sid} ${stage.title} 参数`, hidden: true });
    const details = region.closest("details")!;
    if (!details.open) fireEvent.click(details.querySelector("summary")!);
    region.querySelectorAll<HTMLDetailsElement>("details").forEach(d => { d.open = true; });
    return within(region);
  };

  beforeEach(() => {
    mocked.mockReset(); session = null; preflightOk = true; metadata = true; savedLayout = layout();
    mocked.mockImplementation(async (url, options) => {
      const body = options?.body ? JSON.parse(String(options.body)) : {};
      if (url === BASE) return { id, title: id, description: "真实业务共用的完整／分步流程", wiring_notes: [] } as never;
      if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: savedLayout } as never;
      if (url === "/api/jobs") return [] as never;
      if (url === "/api/jobs/hardware-job") return { id: "hardware-job", kind: `session:${id}`, status: "running", events: [] } as never;
      if (url === "/api/jobs/analysis-job") return { id: "analysis-job", status: "running", events: [] } as never;
      if (url === `${BASE}/workflow`) return (metadata === true ? flow : { ...flow,
        ...(metadata === false ? { parameter_impacts: undefined } : { [metadata]: undefined }) }) as never;
      if (url === `${BASE}/sessions/active`) return session as never;
      if (url === `${BASE}/sessions` && options?.method === "POST") return session = makeSession();
      if (url === `${BASE}/sessions/session`) return session as never;
      if (url === `${BASE}/sessions/session/steps`) {
        if (body.step_ids.includes("A06") && !body.step_ids.includes("A05")) throw new Error("缺少前置 A05");
        return session = { ...session, revision: session.revision + 1,
          steps: Object.fromEntries(flow.acquisition.map(s => [s.id, { status: body.step_ids.includes(s.id) ? "completed" : "pending", error: "" }])) };
      }
      if (url === `${BASE}/sessions/session/parameters`) {
        const impacts = flow.parameter_impacts.acquisition as Record<string, string[]>;
        const changed = Object.keys(body.parameters).filter(key => JSON.stringify(body.parameters[key]) !== JSON.stringify(session.parameters[key]));
        const affected = new Set(changed.flatMap(key => impacts[key] ?? []));
        return session = { ...session, parameters: body.parameters, revision: session.revision + 1,
          steps: Object.fromEntries(Object.entries(session.steps).map(([sid, state]: [string, any]) => [sid,
            affected.has(sid) && state.status === "completed" ? { status: "stale", error: "" } : state])) };
      }
      if (url === `${BASE}/sessions/session/finish`) return session = { ...session, status: "closed", revision: session.revision + 1 };
      if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [history], total: 1 } as never;
      if (url === `${BASE}/runs/old/summary`) return { ...history, analysis_status: "not_started", analysis_error: "", parameters: { ...values(), RUN_TAG: "historical" }, artifacts: {} } as never;
      if (url === `${BASE}/runs/old/analysis` && options?.method === "POST") return { id: "analysis-job", status: "running", events: [] } as never;
      if (url === `${BASE}/runs/old/analysis`) return { run_id: "old", revision: 0, options: {}, fingerprint: {}, summary: {},
        steps: Object.fromEntries(flow.analysis.map(s => [s.id, { status: "pending", error: "" }])) } as never;
      if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["安全预检失败"] } as never;
      if (url === `${BASE}/derive`) return { values: { 预览: 1 } } as never;
      if (url === `${BASE}/defaults`) {
        savedLayout = body.parameter_layout;
        return { message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: savedLayout } } as never;
      }
      throw new Error(`未模拟接口 ${url}`);
    });
  });

  it("运行历史双 Tab、节点编辑归属和顶部操作，下载本实验源图", async () => {
    page(); await screen.findByRole("button", { name: "创建分步会话" });
    expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "历史" })).toBeVisible();
    expect(flow.acquisition).toHaveLength(7); expect(flow.analysis).toHaveLength(2);
    const owned = new Set([...flow.acquisition, ...flow.analysis].flatMap(s => s.display_parameters));
    expect([...owned].sort()).toEqual(fields.map(f => f.name).sort());
    const top = screen.getByRole("button", { name: "保存参数与布局" }).closest(".xy-workflow-controls")!;
    expect(top).toContainElement(screen.getByRole("button", { name: "仅预检" }));
    expect(top).toContainElement(screen.getByRole("button", { name: "预检并运行实验" }));
    expect(within(top as HTMLElement).getByText("采集任务状态")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  });

  it("拒绝缺前置不自动补选，显式执行所选并可安全结束", async () => {
    page(); const create = await screen.findByRole("button", { name: "创建分步会话" });
    await waitFor(() => expect(create).toBeEnabled()); fireEvent.click(create);
    await screen.findByText(/分步等待期间保持输出状态/);
    fireEvent.click(screen.getByRole("checkbox", { name: stageLabel("A06") }));
    fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
    await screen.findByText(/Error: 缺少前置 A05/);
    expect(screen.getByRole("checkbox", { name: stageLabel("A05") })).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "选择全部采集步骤" }));
    fireEvent.click(screen.getByRole("button", { name: "运行所选 / 重跑" }));
    await waitFor(() => expect(session.revision).toBe(1));
    expect(screen.getByRole("checkbox", { name: stageLabel("A07") })).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "结束会话" }));
    await waitFor(() => expect(session.status).toBe("closed"));
  });

  it("共享参数同步、草稿还原与应用只失效不自动运行", async () => {
    session = makeSession(); Object.values(session.steps).forEach((s: any) => { s.status = "completed"; });
    page(); const left = await node(first), right = await node(second);
    const field = fields.find(f => f.name === shared)!;
    const label = `${field.label}${"unit" in field && field.unit ? ` (${field.unit})` : ""}`;
    const initial = Number(field.default), next = initial + .001;
    fireEvent.change(left.getByLabelText(label), { target: { value: String(next) } });
    expect(right.getByLabelText(label)).toHaveValue(next);
    expect(screen.getByText(/预计需重跑的已完成步骤/)).toBeVisible();
    fireEvent.change(right.getByLabelText(label), { target: { value: String(initial) } });
    expect(screen.queryByText(/参数草稿尚未应用/)).not.toBeInTheDocument();
    fireEvent.change(left.getByLabelText(label), { target: { value: String(next) } });
    fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
    await waitFor(() => expect(session.parameters[shared]).toBe(next));
    expect(session.steps[second].status).toBe("stale"); expect(session.steps.A01.status).toBe("completed");
    expect(mocked.mock.calls.some(([url]) => String(url).endsWith("/steps"))).toBe(false);
  });

  it("Tab保留参数、历史填回不启动，预检失败阻止完整运行，目录分析无伪覆盖参数", async () => {
    page(); const tag = (await node("A02")).getByLabelText("运行标签");
    fireEvent.change(tag, { target: { value: "draft" } });
    fireEvent.click(screen.getByRole("tab", { name: "历史" })); await screen.findByRole("button", { name: "填回参数" });
    fireEvent.click(screen.getByRole("tab", { name: "运行" })); expect(tag).toHaveValue("draft");
    preflightOk = false; fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
    await screen.findByText("安全预检失败");
    fireEvent.click(screen.getByRole("tab", { name: "历史" })); fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
    expect(tag).toHaveValue("historical");
    expect(mocked.mock.calls.some(([url, opt]) => url === `${BASE}/runs` && opt?.method === "POST")).toBe(false);
    fireEvent.change(screen.getByLabelText("分析操作对象"), { target: { value: "old" } });
    const selectAnalysis = screen.getByRole("button", { name: "选择全部分析步骤" });
    await waitFor(() => expect(selectAnalysis).toBeEnabled()); fireEvent.click(selectAnalysis);
    const analyze = screen.getByRole("button", { name: "运行所选分析 / 重算" });
    await waitFor(() => expect(analyze).toBeEnabled()); fireEvent.click(analyze);
    await waitFor(() => expect(mocked.mock.calls.some(([url, opt]) => url === `${BASE}/runs/old/analysis` && opt?.method === "POST"
      && Object.keys(JSON.parse(String(opt.body)).analysis_parameters).length === 0)).toBe(true));
  });

  it.each([false, "run_step_id", "finish_step_id"] as const)("缺元数据 %s 阻止新执行但恢复会话并允许安全结束", async missing => {
    session = makeSession(); metadata = missing; page(); await screen.findByText(/接口版本过旧/);
    expect(screen.getByRole("button", { name: "创建分步会话" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "运行所选 / 重跑" })).toBeDisabled();
    expect(mocked.mock.calls.some(([url, options]) => url === `${BASE}/sessions` && options?.method === "POST")).toBe(false);
    const finish = await screen.findByRole("button", { name: "结束会话" });
    await waitFor(() => expect(finish).toBeEnabled()); fireEvent.click(finish);
    await waitFor(() => expect(session.status).toBe("closed"));
  });
});
