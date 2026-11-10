import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, expect, expectTypeOf, it, vi } from "vitest";

import { api } from "../api";
import type { ParameterValues, SchemaField } from "../types/api";
import { WorkflowExperimentPage } from "./WorkflowExperimentPage";
import { WorkflowPanel, type XYSession } from "../components/workflow/WorkflowPanel";
import { WorkflowPanel as LegacyWorkflowPanel, type XYSession as LegacyXYSession } from "../components/NoiseSpectrumXY/WorkflowPanel";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));

const ID = "shared-workflow-test";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
const fields: SchemaField[] = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "next", group: "basic", unit: "" },
  { name: "GAIN", label: "采集增益", type: "number", default: 2, group: "basic", unit: "" },
];
const layout = { basic: fields.map(field => field.name), advanced: [] };
const stage = (id: string, parameters: string[] = []) => ({
  id, title: id, group: "真实步骤", dependencies: [], parameters, display_parameters: parameters, artifacts: [],
});
const old = { run_id: "1014_old", timestamp: "1014", run_tag: "old", completion_status: "completed" };
let preflightOk: boolean;

beforeEach(() => {
  preflightOk = true;
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    if (url === BASE) return { id: ID, title: "公共流程测试", description: "说明", wiring_notes: [] } as never;
    if (url === `${BASE}/schema`) return { fields, parameter_layout_saved: true, parameter_layout: layout } as never;
    if (url === "/api/jobs") return [] as never;
    if (url === `${BASE}/sessions/active`) return null as never;
    if (url === `${BASE}/workflow`) return {
      acquisition: [stage("A01", fields.map(field => field.name)), stage("A02")], analysis: [],
      run_step_id: "A01", finish_step_id: "A02",
      parameter_impacts: { acquisition: { RUN_TAG: ["A01"], GAIN: ["A01"] }, analysis: {} }, diagram_url: "/diagram",
    } as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [old], total: 1 } as never;
    if (url === `${BASE}/runs/${old.run_id}/summary`) return {
      ...old, analysis_status: "not_started", analysis_error: "", parameters: { RUN_TAG: "historical", GAIN: 9 }, artifacts: {},
    } as never;
    if (url === `${BASE}/derive`) return { values: {} } as never;
    if (url === `${BASE}/preflight`) return { ok: preflightOk, errors: preflightOk ? [] : ["参数超限"] } as never;
    if (url === `${BASE}/defaults`) return {
      message: "已保存", schema: { fields, parameter_layout_saved: true, parameter_layout: JSON.parse(String(options?.body)).parameter_layout },
    } as never;
    throw new Error(`未模拟接口 ${url}`);
  });
});
afterEach(cleanup);

function page(props: Partial<Omit<ComponentProps<typeof WorkflowExperimentPage>, "experimentId" | "hasAnalysis">> & { hasAnalysis?: boolean } = {}) {
  return render(<WorkflowExperimentPage experimentId={ID} hasAnalysis={false} {...props} />);
}

it("旧流程面板导出及会话类型与公共入口兼容", () => {
  expect(LegacyWorkflowPanel).toBe(WorkflowPanel);
  expectTypeOf<LegacyXYSession>().toEqualTypeOf<XYSession>();
});

it("无分析器只显示真实采集节点，参数切换Tab后保留且历史只读取快照", async () => {
  page();
  await screen.findByRole("checkbox", { name: /A01/ });
  expect(screen.queryByRole("heading", { name: "分析流程" })).not.toBeInTheDocument();
  expect(screen.getByText("本实验无离线分析，结果见历史记录。")).toBeInTheDocument();
  fireEvent.click(screen.getByText(/参数：运行标签、采集增益/));
  const input = screen.getByLabelText("运行标签");
  fireEvent.change(input, { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "填回参数" });
  const history = screen.getByRole("tabpanel", { name: "历史" });
  expect(within(history).getByText("historical")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "重新分析" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("draft");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
});

it("预检失败不启动，历史填回不保存或启动，保存使用下一次实验值", async () => {
  page();
  await screen.findByRole("checkbox", { name: /A01/ });
  preflightOk = false;
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByText("参数超限");
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect(mockApi.mock.calls.some(([url]) => url === `${BASE}/defaults`)).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("已保存");
  const request = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`);
  expect(JSON.parse(String(request?.[1]?.body))).toEqual({ parameters: { RUN_TAG: "historical", GAIN: 9 }, parameter_layout: layout });
});

it("两个主Tab支持键盘切换", async () => {
  page();
  await screen.findByRole("checkbox", { name: /A01/ });
  const tab = screen.getByRole("tab", { name: "运行" });
  tab.focus();
  fireEvent.keyDown(tab, { key: "ArrowRight" });
  await waitFor(() => expect(screen.getByRole("tab", { name: "历史" })).toHaveFocus());
});

it.each<{ status: string; artifacts: string[]; files?: Record<string, (string | number)[] | null>; expected: string | null }>([
  { status: "quality_failed", artifacts: ["diagnostic.yaml"], expected: "diagnostic.yaml" },
  { status: "quality_failed", artifacts: [], expected: null },
  { status: "stale", artifacts: ["diagnostic.yaml"], expected: null },
  { status: "pending", artifacts: ["diagnostic.yaml"], expected: null },
  { status: "completed", artifacts: [], expected: null },
  { status: "completed", artifacts: [], files: { "actual.npz": [12, "sha256"], "missing.npz": null }, expected: "actual.npz" },
  { status: "quality_failed", artifacts: [], files: { "diagnostic.yaml": [12, "sha256"] }, expected: "diagnostic.yaml" },
])("$status 节点只显示执行记录中有依据的产物", async ({ status, artifacts, files, expected }) => {
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/workflow`) return {
      run_step_id: "A01", finish_step_id: "A02",
      acquisition: [{ ...stage("A01"), artifacts: ["expected.npz"] }, stage("A02")], analysis: [],
      parameter_impacts: { acquisition: {}, analysis: {} }, diagram_url: "/diagram",
    } as never;
    return original(url, options);
  });
  render(<WorkflowPanel experimentId={ID} enabled fields={fields} layout={layout} values={{}}
    setLayout={vi.fn()} setNotice={vi.fn()} parametersDisabled={false} setValues={vi.fn()}
    session={{ session_id: "closed", job_id: "", revision: 1, status: "closed", run_id: "saved",
      parameters: {}, output_source: "none", demod_mode: "none",
      steps: { A01: { status, artifacts, files, error: "" } } }}
    setSession={vi.fn()} setJob={vi.fn()} setAnalysisJob={vi.fn()} hardwareBusy={false} runs={[]}
    setError={vi.fn()} onRefresh={vi.fn()}><div /></WorkflowPanel>);
  await screen.findByRole("checkbox", { name: "A01 A01" });
  expect(screen.queryByRole("link", { name: "expected.npz", hidden: true })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "missing.npz", hidden: true })).not.toBeInTheDocument();
  if (expected) {
    expect(screen.getByRole("link", { name: expected, hidden: true })).toHaveAttribute(
      "href", `${BASE}/runs/saved/files/${expected}`);
  } else {
    expect(screen.queryByText("产物")).toBeNull();
  }
});

it.each(["unknown-run", "unknown-finish", "same-role", "dependent-finish"])("真实页面拒绝非法角色 %s，不创建任务", async invalid => {
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/workflow`) return {
      acquisition: [stage("A01"), { ...stage("A02"), dependencies: invalid === "dependent-finish" ? ["A01"] : [] }],
      analysis: [], parameter_impacts: { acquisition: {}, analysis: {} }, diagram_url: "/diagram",
      run_step_id: invalid === "unknown-run" ? "missing" : invalid === "same-role" ? "A02" : "A01",
      finish_step_id: invalid === "unknown-finish" ? "missing" : "A02",
    } as never;
    return original(url, options);
  });
  page();
  await screen.findByText(/流程接口版本过旧或角色无效/);
  expect(screen.getByRole("button", { name: "创建分步会话" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "运行所选" })).toBeDisabled();
  expect(mockApi.mock.calls.some(([url, options]) => [`${BASE}/sessions`, `${BASE}/runs`].includes(String(url))
    && options?.method === "POST")).toBe(false);
});

it.each([
  { id: "session", revision: 4, request: "old", pending: "new", expectedRevision: 4, error: "原错误" },
  { id: "session", revision: 4, request: "new", pending: null, expectedRevision: 4, error: "本次错误" },
  { id: "session", revision: 3, request: "new", pending: "new", expectedRevision: 4, error: "原错误" },
  { id: "other-session", revision: 99, request: "new", pending: "new", expectedRevision: 4, error: "原错误" },
  { id: "session", revision: 5, request: undefined, pending: "new", expectedRevision: 5, error: "原错误" },
])("SSE 回执 $request 修订 $revision 只确认同会话的当前请求", async test => {
  let current: XYSession | undefined = { session_id: "session", job_id: "job", revision: 4, status: "waiting",
    parameters: {}, steps: {}, pending_request: "new", error: "原错误", output_source: "未回读", demod_mode: "unknown" };
  const update = vi.fn((value: Parameters<React.Dispatch<React.SetStateAction<XYSession | undefined>>>[0]) => {
    current = typeof value === "function" ? value(current) : value;
  });
  const snapshot = { ...current, session_id: test.id, revision: test.revision };
  render(<WorkflowPanel experimentId={ID} enabled fields={fields} layout={layout} values={{}}
    setLayout={vi.fn()} setNotice={vi.fn()} parametersDisabled={false} setValues={vi.fn()}
    session={current} setSession={update} job={{ id: "job", status: "running", events: [{ data: {
      workflow_session: snapshot, command_result: test.request ? { request_id: test.request, error: "本次错误" } : undefined,
    } }] } as never} setJob={vi.fn()} setAnalysisJob={vi.fn()} hardwareBusy={false} runs={[]}
    setError={vi.fn()} onRefresh={vi.fn()}><div /></WorkflowPanel>);
  await waitFor(() => expect(update).toHaveBeenCalled());
  expect(current?.session_id).toBe("session");
  expect(current?.pending_request).toBe(test.pending);
  expect(current?.revision).toBe(test.expectedRevision);
  expect(current?.error).toBe(test.error);
});

it("页头小标题默认 EXPERIMENT，传入 eyebrow 时覆盖", async () => {
  page();
  await screen.findByRole("checkbox", { name: /A01/ });
  expect(screen.getByText("EXPERIMENT")).toBeInTheDocument();
  cleanup();
  page({ eyebrow: "MEASUREMENT · XY SINE" });
  await screen.findByRole("checkbox", { name: /A01/ });
  expect(screen.getByText("MEASUREMENT · XY SINE")).toBeInTheDocument();
});

it("mapFields 只影响流程图参数表单，不改动参数值", async () => {
  page({ mapFields: (items, current) => items.map(field =>
    field.name === "GAIN" ? { ...field, read_only: current.RUN_TAG === "draft" } : field) });
  await screen.findByRole("checkbox", { name: /A01/ });
  fireEvent.click(screen.getByText(/参数：运行标签、采集增益/));
  fireEvent.change(screen.getByLabelText("运行标签"), { target: { value: "draft" } });
  expect(screen.getByLabelText("采集增益")).toBeDisabled();
  expect(screen.getByLabelText("运行标签")).not.toBeDisabled();
});

it("未传 renderPreview 时保留默认派生预览", async () => {
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/derive`) return { values: { GAIN: 7 } } as never;
    return original(url, options);
  });
  page();
  await screen.findByRole("checkbox", { name: /A01/ });
  const summary = (await screen.findByText("派生预览（只读）")).closest("details")!;
  await waitFor(() => expect(within(summary).getByText("7")).toBeInTheDocument());
});

it("renderPreview 接收派生值与 setValues，并替换默认预览", async () => {
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/derive`) return { values: { GAIN: 7 } } as never;
    return original(url, options);
  });
  page({ renderPreview: ({ derived, derivedError, setValues, busy }) => <div aria-label="自定义预览">
    {derivedError && <p>{derivedError}</p>}
    <p>派生增益 {String(derived.GAIN ?? "—")}</p>
    <button disabled={busy} onClick={() => setValues(current => ({ ...current, RUN_TAG: "preview" }))}>用预览填值</button>
  </div> });
  await screen.findByRole("checkbox", { name: /A01/ });
  expect(screen.queryByText("派生预览（只读）")).not.toBeInTheDocument();
  expect(await screen.findByText("派生增益 7")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "用预览填值" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("preview");
});

it("renderPreview 的派生失败写入 derivedError，不写页面级错误条", async () => {
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/derive`) throw new Error("无法计算");
    return original(url, options);
  });
  page({ renderPreview: ({ derivedError }) => <p>{derivedError || "无错误"}</p> });
  await screen.findByText("派生预览失败：Error: 无法计算");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("未传 renderPreview 时派生失败仍写页面级错误条", async () => {
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/derive`) throw new Error("无法计算");
    return original(url, options);
  });
  page();
  expect(await screen.findByRole("alert")).toHaveTextContent("派生预览失败：Error: 无法计算");
});

it("startLabel 覆盖启动按钮文字", async () => {
  page();
  await screen.findByRole("checkbox", { name: /A01/ });
  expect(screen.getByRole("button", { name: "预检并运行实验" })).toBeInTheDocument();
  cleanup();
  page({ startLabel: "完整运行" });
  await screen.findByRole("checkbox", { name: /A01/ });
  expect(screen.getByRole("button", { name: "完整运行" })).toBeInTheDocument();
});

it("fillBack 同步返回自定义参数并写入提示", async () => {
  page({ fillBack: ({ values }) => ({ values: { ...values, RUN_TAG: "filled" }, notice: "已按实验规则填回" }) });
  await screen.findByRole("checkbox", { name: /A01/ });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  await waitFor(() => expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true"));
  expect(screen.getByLabelText("运行标签")).toHaveValue("filled");
  expect(screen.getByText("已按实验规则填回")).toBeInTheDocument();
});

it("fillBack 只返回部分参数时合并进当前值，未返回的字段保留", async () => {
  page({ fillBack: () => ({ values: { RUN_TAG: "partial" } }) });
  await screen.findByRole("checkbox", { name: /A01/ });
  const gain = screen.getByLabelText("采集增益");
  fireEvent.change(gain, { target: { value: "7" } });
  await waitFor(() => expect(screen.getByLabelText("采集增益")).toHaveValue(7));
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  await waitFor(() => expect(screen.getByLabelText("运行标签")).toHaveValue("partial"));
  expect(screen.getByLabelText("采集增益")).toHaveValue(7);
});

it("fillBack 异步期间禁用填回，结果经 normalizeParameters 写入", async () => {
  const doubleGain = (values: ParameterValues): ParameterValues => ({ ...values, GAIN: Number(values.GAIN) * 2 });
  let resolve!: (value: { values: ParameterValues }) => void;
  page({ normalizeParameters: doubleGain,
    fillBack: () => new Promise(done => { resolve = done; }) });
  await screen.findByRole("checkbox", { name: /A01/ });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const button = await screen.findByRole("button", { name: "填回参数" });
  fireEvent.click(button);
  await waitFor(() => expect(button).toBeDisabled());
  resolve({ values: { RUN_TAG: "async", GAIN: 5 } });
  await waitFor(() => expect(screen.getByLabelText("采集增益")).toHaveValue(10));
  expect(screen.getByLabelText("运行标签")).toHaveValue("async");
});

it("fillBack 异常写入错误条并留在历史 Tab", async () => {
  page({ fillBack: async () => { throw new Error("旧目录无法填回"); } });
  await screen.findByRole("checkbox", { name: /A01/ });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("旧目录无法填回");
  expect(screen.getByRole("tab", { name: "历史" })).toHaveAttribute("aria-selected", "true");
});

function mockAnalysisFlow() {
  const original = mockApi.getMockImplementation()!;
  const runs = Array.from({ length: 60 }, (_, index) => ({
    run_id: `saved-${index}`, timestamp: "t", run_tag: "", completion_status: index === 59 ? "failed" : "completed",
  }));
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/workflow`) return {
      acquisition: [stage("A01", ["RUN_TAG", "GAIN"]), stage("A02")], analysis: [stage("B01")],
      run_step_id: "A01", finish_step_id: "A02",
      parameter_impacts: { acquisition: { RUN_TAG: ["A01"], GAIN: ["A01"] }, analysis: {} }, diagram_url: "/diagram",
    } as never;
    if (String(url).startsWith(`${BASE}/runs?`)) {
      const query = new URLSearchParams(String(url).split("?")[1]);
      const offset = Number(query.get("offset")), limit = Number(query.get("limit"));
      return { runs: runs.slice(offset, offset + limit), total: runs.length } as never;
    }
    if (String(url).endsWith("/summary")) {
      const runId = String(url).split("/runs/")[1].split("/")[0];
      const run = runs.find(item => item.run_id === runId);
      return { ...run, analysis_status: "not_started", analysis_error: "", parameters: { RUN_TAG: "historical", GAIN: 9 }, artifacts: {} } as never;
    }
    return original(url, options);
  });
  return runs;
}

it("分析候选读前 500 条，受 analysisRunFilter 过滤且独立于历史分页", async () => {
  mockAnalysisFlow();
  page({ hasAnalysis: true, analysisRunFilter: run => run.completion_status === "completed" });
  await screen.findByRole("checkbox", { name: /A01/ });
  const choices = () => within(screen.getByLabelText("分析操作对象")).getAllByRole("option").map(option => (option as HTMLOptionElement).value);
  await waitFor(() => expect(choices()).toContain("saved-58"));
  expect(choices()).toContain("saved-0");
  expect(choices()).not.toContain("saved-59");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: "saved-50" });
  expect(screen.queryByRole("button", { name: "saved-0" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(choices()).toContain("saved-0");
  expect(choices()).toContain("saved-58");
  expect(choices()).not.toContain("saved-59");
});

it("未传 analysisRunFilter 时分析候选不过滤", async () => {
  mockAnalysisFlow();
  page({ hasAnalysis: true });
  await screen.findByRole("checkbox", { name: /A01/ });
  const choices = () => within(screen.getByLabelText("分析操作对象")).getAllByRole("option").map(option => (option as HTMLOptionElement).value);
  await waitFor(() => expect(choices()).toContain("saved-59"));
  expect(choices()).toContain("saved-0");
});

it.each(["running", "acquired"])("%s 运行的重新分析禁用", async status => {
  const original = mockApi.getMockImplementation()!;
  mockApi.mockImplementation(async (url, options) => {
    if (url === `${BASE}/runs/${old.run_id}/summary`) return {
      ...old, completion_status: status, analysis_status: "not_started", analysis_error: "",
      parameters: { RUN_TAG: "historical", GAIN: 9 }, artifacts: {},
    } as never;
    if (String(url).startsWith(`${BASE}/runs?`)) return { runs: [{ ...old, completion_status: status }], total: 1 } as never;
    return original(url, options);
  });
  page({ hasAnalysis: true });
  await screen.findByRole("checkbox", { name: /A01/ });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  const button = await screen.findByRole("button", { name: "重新分析" });
  expect(button).toBeDisabled();
});

it("renderLatest 替换最近结果内容并保留标题与打开入口", async () => {
  page({ hasAnalysis: true, renderLatest: ({ latest, openRun }) => <div>
    <p>自定义最新：{latest?.run_id ?? "无"}</p>
    {latest && <button onClick={() => openRun(latest.run_id)}>打开最新</button>}
  </div> });
  await screen.findByRole("checkbox", { name: /A01/ });
  expect(screen.getByRole("heading", { name: "最近一次结果" })).toBeInTheDocument();
  expect(await screen.findByText("自定义最新：1014_old")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "打开最新" }));
  expect(screen.getByRole("tab", { name: "历史" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByText("historical")).toBeInTheDocument();
});

it("renderHistory 替换历史主体并使用模板上下文", async () => {
  page({ renderHistory: ({ fields, setValues, openRunTab, busy }) => <div>
    <p>自定义历史 {fields.length} 项</p>
    <button disabled={busy} onClick={() => {
      setValues(current => ({ ...current, RUN_TAG: "history-fill" })); openRunTab();
    }}>自定义填回</button>
  </div> });
  await screen.findByRole("checkbox", { name: /A01/ });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(screen.getByText("自定义历史 2 项")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "填回参数" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "自定义填回" }));
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByLabelText("运行标签")).toHaveValue("history-fill");
});
