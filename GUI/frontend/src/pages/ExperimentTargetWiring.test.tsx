import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "../App";
import { NavigationGuard } from "../components/NavigationGuard";
import { WiringEditor } from "../components/wiring/WiringEditor";
import type { ExperimentDefinition, Job, SchemaField } from "../types/api";
import type { components } from "../types/openapi";
import type { WiringDocument, WiringGraph } from "../components/wiring/wiringTypes";

// jsdom 不提供绘图尺寸；只替换与本任务无关的图表 renderer，真实页面和编辑器不 mock。
vi.mock("../components/ScopePlot", () => ({ ScopePlot: () => <div aria-label="测试波形图" /> }));

type Graph = WiringGraph;
type Document = WiringDocument;
type Body = components["schemas"]["WiringBody"];
const RF = "rf-sensitivity-aw";
const WORKFLOW = "z-aw-closed-loop-waveform-correction";
const STATIC = "static-sensitivity";
const MX = "mx-y-optimal-control-rf-frequency-response";
const GENERIC = "generic-target-test";
const ARCHIVED = "archived-target-test";
const SCOPE = "scope-capture";
const IDS = [RF, WORKFLOW, STATIC, MX, GENERIC, ARCHIVED, SCOPE];
const names: Record<string, string> = { [RF]: "RF 灵敏度（DirectAW）", [WORKFLOW]: "Z 任意波闭环", [STATIC]: "静磁场灵敏度", [MX]: "Mx Y 最优控制", [GENERIC]: "通用实验", [ARCHIVED]: "归档实验", [SCOPE]: "示波器采集" };
const graph = (name: string): Graph => ({ nodes: [{ device: { id: "device-stable", name, model: "人工器件", device_library_id: null, ports: [] }, x: 60, y: 60 }], edges: [] });
const field: SchemaField = { name: "RUN_TAG", label: "运行标签", type: "string", default: "default-tag", group: "basic", unit: "" };
const definition = (id: string): ExperimentDefinition => ({ id, title: names[id], description: "只读测试定义", archived: id === ARCHIVED, category: "test", category_label: "测试", family: "test", variant: "test", required_devices: [], required_mapping_keys: [], execution_mode: "typed_workflow", acquisition_program: "test", analysis_program: null, wiring_notes: [], safety_notes: [], supports_cancel: true, can_analyze: false, ...definitionExtras });
const flow = { acquisition: ["A01", "A02"].map(id => ({ id, title: id, group: "采集", dependencies: [], parameters: id === "A01" ? ["RUN_TAG"] : [], display_parameters: id === "A01" ? ["RUN_TAG"] : [], artifacts: [] })), analysis: [], run_step_id: "A01", finish_step_id: "A02", parameter_impacts: { acquisition: { RUN_TAG: ["A01"] }, analysis: {} }, diagram_url: "/diagram" };
const response = (data: unknown, status = 200, code?: string) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json", ...(code ? { "X-Error-Code": code } : {}) } });
let stored: Document;
let jobs: Job[];
let readFailure: boolean;
let saveFailure: "" | "error" | "conflict";
let pendingSave: Promise<void> | undefined;
let activeSessionId: string | undefined;
let zeroFields: boolean;
let definitionExtras: Partial<Pick<ExperimentDefinition, "wiring_notes" | "required_devices" | "required_mapping_keys">>;
let definitionFailure: string;
const requests: Array<{ path: string; method: string; body?: unknown }> = [];
const forbidden: string[] = [];
const sources: TestEventSource[] = [];
class TestEventSource {
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { sources.push(this); }
  close() { this.closed = true; }
}

beforeEach(() => {
  stored = { schema_version: 1, revision: "wiring-r1", site: graph("现场保持不变"), targets: { [RF]: graph("RF 目标保持不变"), "retired-target": graph("退役目标保持不变") }, diagnostics: { site: [], targets: {} } };
  jobs = []; readFailure = false; saveFailure = ""; pendingSave = undefined; activeSessionId = undefined; zeroFields = false;
  definitionExtras = {}; definitionFailure = "";
  requests.length = 0; forbidden.length = 0; sources.length = 0; localStorage.clear();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  vi.spyOn(window, "scrollBy").mockImplementation(() => {});
  vi.stubGlobal("EventSource", TestEventSource);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input); const method = options?.method ?? "GET";
    const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    requests.push({ path, method, body });
    if (path === "/api/health" && method === "GET") return response({ status: "ok", hardware_busy: false });
    if (path === "/api/jobs" && method === "GET") return response(jobs);
    if (path === "/api/wiring" && method === "GET") return readFailure ? response({ detail: "接线读取失败" }, 500) : response(stored);
    if (path === "/api/device-ports" && method === "GET") return response({ schema_version: 1, revision: "ports-r1", devices: [], presets: [] });
    if (path === "/api/wiring" && method === "PUT") {
      await pendingSave;
      if (saveFailure) return response({ detail: saveFailure === "conflict" ? "其他窗口已修改" : "保存失败" }, saveFailure === "conflict" ? 409 : 500, saveFailure === "conflict" ? "revision_conflict" : undefined);
      stored = { ...stored, revision: "wiring-r2", site: body.site, targets: body.targets };
      return response(stored);
    }
    if (path === "/api/wiring/compare" && method === "POST") return response({ retained: [], removed: [], added: [], mode_changes: [], site_warnings: [], target_warnings: [] });
    if (method === "GET" && path.startsWith("/api/jobs/")) {
      const job = jobs.find(item => path === `/api/jobs/${item.id}`);
      if (job) return response(job);
    }
    for (const id of IDS) {
      const base = `/api/experiments/${id}`;
      if (path === base && method === "GET") return definitionFailure === id ? response({ detail: "实验定义读取失败" }, 500) : response(definition(id));
      if (path === `${base}/schema` && method === "GET" && id !== ARCHIVED) return response({ schema_version: 2, fields: zeroFields ? [] : [field], parameter_layout_saved: true, parameter_layout: { basic: zeroFields ? [] : [field.name], advanced: [] } });
      if (path === `${base}/workflow` && method === "GET" && id !== ARCHIVED) return response(flow);
      if (path === `${base}/sessions/active` && method === "GET" && id !== ARCHIVED) return response(activeSessionId === id ? { session_id: "existing-session", job_id: "session-job", revision: 1, status: "waiting", pending_request: null, parameters: { RUN_TAG: "already-applied" }, steps: {}, outputs: {}, current_step: null, run_id: null } : null);
      if (path.startsWith(`${base}/runs?`) && method === "GET" && id !== ARCHIVED) return response({ runs: [], total: 0, limit: 50, offset: 0 });
      if (path === `${base}/derive` && method === "POST" && id !== ARCHIVED) return response({ ok: true, values: {} });
    }
    if (path === "/api/runs?experiment_id=scope-capture&limit=50&offset=0" && method === "GET") return response({ runs: [], total: 0 });
    forbidden.push(`${method} ${path}`);
    throw new Error(`禁止的未模拟请求：${method} ${path}`);
  }));
});
afterEach(() => {
  cleanup();
  expect(forbidden).toEqual([]);
  expect(requests.filter(item => item.method !== "GET").every(item => item.path === "/api/wiring" && item.method === "PUT" || item.path === "/api/wiring/compare" && item.method === "POST" || /\/derive$/.test(item.path) && item.method === "POST")).toBe(true);
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});
async function open(id = WORKFLOW) {
  const router = createMemoryRouter([{ path: "*", element: <NavigationGuard><App /></NavigationGuard> }], { initialEntries: [`/experiments/${id}`] });
  render(<RouterProvider router={router} />);
  await screen.findByRole("heading", { level: 1, name: names[id] }, { timeout: 15000 });
  return router;
}
const tab = (name: string) => screen.getByRole("tab", { name });
const panel = () => screen.getByRole("tabpanel", { name: "目标接线图" });
const puts = () => requests.filter(item => item.path === "/api/wiring" && item.method === "PUT");
async function target() {
  fireEvent.click(tab("目标接线图"));
  await within(panel()).findByRole("heading", { level: 2, name: "目标接线图" }, { timeout: 15000 });
  await waitFor(() => expect(within(panel()).getByRole("button", { name: "添加空白图内器件" })).toBeEnabled());
  return panel();
}
function add(name = "当前目标器件") {
  fireEvent.click(within(panel()).getByRole("button", { name: "添加空白图内器件" }));
  fireEvent.change(within(panel()).getByLabelText("图内器件名称"), { target: { value: name } });
}
async function save() {
  fireEvent.click(within(panel()).getByRole("button", { name: "保存当前实验目标" }));
  await within(panel()).findByText("当前实验目标接线图已保存。");
  // 异步保存先显示成功提示，子草稿注册与父级刷新保护还需完成后续副作用。
  await act(async () => {});
}
function editParameter(value: string) {
  document.querySelectorAll<HTMLDetailsElement>(".xy-node-parameters").forEach(item => { item.open = true; });
  fireEvent.change(screen.getByLabelText("运行标签"), { target: { value } });
}

it("公共流程真实路由三 Tab，懒载固定当前实验，切换保留参数与目标草稿且只保存当前目标", async () => {
  const router = await open();
  await screen.findByRole("checkbox", { name: /^A01 / });
  expect(screen.getAllByRole("tab").map(item => item.textContent)).toEqual(["运行", "历史", "目标接线图"]);
  expect(requests.some(item => item.path === "/api/wiring")).toBe(false);
  editParameter("parameter-draft");
  const original = structuredClone(stored);
  await target();
  expect(within(panel()).queryByLabelText("目标实验")).not.toBeInTheDocument();
  expect(within(panel()).queryByRole("button", { name: "共享现场图" })).not.toBeInTheDocument();
  expect(within(panel()).getAllByText(/此目标尚未建立/).length).toBeGreaterThan(0);
  expect(puts()).toHaveLength(0);
  add();
  const node = within(panel()).getByRole("button", { name: "移动器件：当前目标器件" });
  fireEvent.click(tab("历史"));
  fireEvent.click(tab("运行"));
  expect(screen.getByLabelText("运行标签")).toHaveValue("parameter-draft");
  fireEvent.click(tab("目标接线图"));
  expect(within(panel()).getByRole("button", { name: "移动器件：当前目标器件" })).toBe(node);
  expect(requests.filter(item => item.path === "/api/wiring" && item.method === "GET")).toHaveLength(1);
  await save();
  const sent = puts()[0].body as Body;
  expect(sent.site).toEqual(original.site);
  expect(sent.targets[RF]).toEqual(original.targets[RF]);
  expect(sent.targets["retired-target"]).toEqual(original.targets["retired-target"]);
  expect(sent.targets[WORKFLOW].nodes[0].device.name).toBe("当前目标器件");
  expect(requests.some(item => item.path === "/api/experiments")).toBe(false);
  // 目标保存不冒充参数保存，离开仍要保护未应用的参数。
  await act(async () => router.navigate("/experiments"));
  expect(router.state.location.pathname).toBe(`/experiments/${WORKFLOW}`);
  expect(window.confirm).toHaveBeenCalled();
}, 15000);

it("DirectAW 当前目标真实编辑与确认复制，现场和其他目标不改变，三项键盘循环", async () => {
  await open(RF);
  const original = structuredClone(stored);
  const run = tab("运行"); run.focus();
  fireEvent.keyDown(run, { key: "ArrowLeft" });
  expect(tab("目标接线图")).toHaveFocus();
  await within(panel()).findByRole("button", { name: "移动器件：RF 目标保持不变" });
  await waitFor(() => expect(within(panel()).getByRole("button", { name: "复制现场到当前实验目标草稿" })).toBeEnabled());
  expect(within(panel()).queryByRole("combobox", { name: "目标实验" })).not.toBeInTheDocument();
  fireEvent.click(within(panel()).getByRole("button", { name: "复制现场到当前实验目标草稿" }));
  expect(within(panel()).getByRole("button", { name: "移动器件：RF 目标保持不变" })).toBeInTheDocument();
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.click(within(panel()).getByRole("button", { name: "复制现场到当前实验目标草稿" }));
  expect(within(panel()).getByRole("button", { name: "移动器件：现场保持不变" })).toBeInTheDocument();
  expect(puts()).toHaveLength(0);
  await save();
  expect(stored.site).toEqual(original.site);
  expect(stored.targets["retired-target"]).toEqual(original.targets["retired-target"]);
  expect(stored.targets[RF]).toEqual(original.site);
  fireEvent.keyDown(tab("目标接线图"), { key: "Home" });
  expect(tab("运行")).toHaveFocus();
  fireEvent.keyDown(tab("运行"), { key: "End" });
  expect(tab("目标接线图")).toHaveFocus();
}, 15000);

it.each(["error", "conflict"] as const)("当前目标保存 %s 保留草稿和基础修订，隐藏后刷新/离开仍保护", async failure => {
  const router = await open(GENERIC); await target(); add("不能丢失");
  saveFailure = failure;
  fireEvent.click(within(panel()).getByRole("button", { name: "保存当前实验目标" }));
  await within(panel()).findByText(failure === "conflict" ? /保存冲突（409），当前目标草稿与基础修订已保留/ : /保存失败，当前目标草稿与基础修订已保留/);
  fireEvent.click(tab("运行"));
  const unload = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(unload);
  expect(unload.defaultPrevented).toBe(true);
  await act(async () => router.navigate("/experiments"));
  expect(router.state.location.pathname).toBe(`/experiments/${GENERIC}`);
  fireEvent.click(tab("目标接线图"));
  expect(within(panel()).getByLabelText("图内器件名称")).toHaveValue("不能丢失");
  saveFailure = ""; await save();
  expect(puts().map(item => (item.body as Body).base_revision)).toEqual(["wiring-r1", "wiring-r1"]);
  const clean = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(clean);
  expect(clean.defaultPrevented).toBe(false);
}, 15000);

it("当前目标读取失败禁空存，保存未完成禁编辑且切 Tab 后保留 pending 状态", async () => {
  readFailure = true;
  await open(GENERIC); fireEvent.click(tab("目标接线图"));
  await within(panel()).findByText(/接线图读取失败，已禁止编辑和空图保存/);
  expect(within(panel()).getByRole("button", { name: "添加空白图内器件" })).toBeDisabled();
  expect(within(panel()).getByRole("button", { name: "保存当前实验目标" })).toBeDisabled();
  readFailure = false;
  fireEvent.click(within(panel()).getByRole("button", { name: "重试读取接线图" }));
  await waitFor(() => expect(within(panel()).getByRole("button", { name: "添加空白图内器件" })).toBeEnabled());
  add();
  let resolve!: () => void;
  pendingSave = new Promise<void>(done => { resolve = done; });
  fireEvent.click(within(panel()).getByRole("button", { name: "保存当前实验目标" }));
  expect(within(panel()).getByRole("button", { name: "正在保存当前目标…" })).toBeDisabled();
  expect(within(panel()).getByLabelText("图内器件名称")).toBeDisabled();
  fireEvent.click(tab("运行")); fireEvent.click(tab("目标接线图"));
  expect(within(panel()).getByRole("button", { name: "正在保存当前目标…" })).toBeDisabled();
  await act(async () => resolve());
  await within(panel()).findByText(/当前实验目标接线图已保存/);
}, 15000);

it.each([STATIC, MX])("%s 活会话已应用值不是草稿，目标切换不重建任务监听，新编辑仍受保护", async id => {
  activeSessionId = id;
  jobs = [{ id: "session-job", kind: `session:${id}`, status: "running", stage: "waiting", percent: null, message: "已有会话", created_at: "2026-11-05T08:00:00Z", events: [] }];
  await open(id);
  await screen.findByRole("checkbox", { name: /^A01 / });
  document.querySelectorAll<HTMLDetailsElement>(".xy-node-parameters").forEach(item => { item.open = true; });
  await waitFor(() => expect(screen.getByLabelText("运行标签")).toHaveValue("already-applied"));
  const streams = [...sources];
  const clean = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(clean);
  expect(clean.defaultPrevented).toBe(false);
  await target(); fireEvent.click(tab("运行"));
  expect(screen.getByLabelText("运行标签")).toHaveValue("already-applied");
  expect(sources).toEqual(streams);
  expect(streams.every(source => !source.closed)).toBe(true);
  editParameter("new-draft");
  fireEvent.click(tab("目标接线图"));
  const dirty = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(dirty);
  expect(dirty.defaultPrevented).toBe(true);
}, 15000);

it("同实验任务链接从目标返回运行，接线草稿和原任务 SSE 均保留且不启动/取消硬件", async () => {
  jobs = [{ id: "resume-job", kind: `experiment:${WORKFLOW}`, status: "running", stage: "waiting", percent: null, message: "已有任务", created_at: "2026-11-05T08:00:00Z", events: [] }];
  const router = await open(); await target(); add("任务旁的草稿");
  const streams = [...sources];
  const link = await screen.findByRole("link", { name: /前往实验：实验运行/ });
  fireEvent.click(link);
  await waitFor(() => expect(tab("运行")).toHaveAttribute("aria-selected", "true"));
  expect(router.state.location.search).toBe("?job=resume-job");
  expect(sources).toEqual(streams);
  fireEvent.click(tab("目标接线图"));
  expect(within(panel()).getByLabelText("图内器件名称")).toHaveValue("任务旁的草稿");
  expect(window.confirm).not.toHaveBeenCalled();
  expect(puts()).toHaveLength(0);
}, 15000);

it.each([STATIC, SCOPE])("%s 无参数也能编辑目标，但不放宽原运行参数门控", async id => {
  zeroFields = true;
  await open(id);
  await target(); add("独立目标"); await save();
  expect(stored.targets[id].nodes[0].device.name).toBe("独立目标");
  expect(requests.filter(item => /\/(preflight|sessions|defaults|runs)$/.test(item.path) && item.method !== "GET")).toEqual([]);
}, 15000);

it("目标图旁展示可折叠接线要点，缺失型号与未绑定用途进入预检且不自动添加器件或连线", async () => {
  definitionExtras = { wiring_notes: ["先关闭输出再改接探头。"], required_devices: ["驱动 (DG4000)", "HF2"], required_mapping_keys: ["drive", "read"] };
  stored.mapping_groups = [{ keys: ["drive"], label: "驱动", device_library_id: null }, { keys: ["read"], label: "读取", device_library_id: null }];
  stored.targets[GENERIC] = { nodes: [{ device: { id: "device-drive", name: "驱动源", model: "DG4000", device_library_id: null, ports: [] }, x: 60, y: 60, follow_mapping: ["drive"] }], edges: [] };
  await open(GENERIC); await target();
  const notes = within(panel()).getByText("接线要点").closest("details")!;
  expect(notes).toHaveAttribute("open");
  expect(within(notes).getByText("先关闭输出再改接探头。")).toBeInTheDocument();
  const preflight = within(panel()).getByRole("region", { name: "接线预检清单" });
  expect(within(preflight).getByText("HF2：图中未找到此型号器件。")).toBeInTheDocument();
  expect(within(preflight).queryByText(/驱动 \(DG4000\)：/)).not.toBeInTheDocument();
  expect(within(preflight).getByText("用途 read：图中无器件绑定此用途组。")).toBeInTheDocument();
  expect(within(preflight).queryByText(/用途 drive：/)).not.toBeInTheDocument();
  expect(within(panel()).queryByRole("region", { name: "实验接线要点与设备要求" })).not.toBeInTheDocument();
  expect(within(panel()).getByText(/1 个器件 \/ 0 条连线/)).toBeInTheDocument();
  expect(puts()).toHaveLength(0);
}, 15000);

it("图内补齐型号后缺失提示消失，仍不自动推断或建立连线", async () => {
  definitionExtras = { wiring_notes: [], required_devices: ["HF2"], required_mapping_keys: [] };
  await open(GENERIC); await target();
  const preflight = within(panel()).getByRole("region", { name: "接线预检清单" });
  expect(within(preflight).getByText("HF2：图中未找到此型号器件。")).toBeInTheDocument();
  expect(within(panel()).queryByText("接线要点")).not.toBeInTheDocument();
  add("锁相放大");
  fireEvent.change(within(panel()).getByLabelText("图内器件型号"), { target: { value: "hf2" } });
  expect(within(preflight).queryByText("HF2：图中未找到此型号器件。")).not.toBeInTheDocument();
  expect(within(panel()).getByText(/1 个器件 \/ 0 条连线/)).toBeInTheDocument();
  expect(puts()).toHaveLength(0);
}, 15000);

it("实验定义读取失败只提示要点，目标图仍可编辑", async () => {
  definitionFailure = GENERIC;
  const router = createMemoryRouter([{ path: "*", element: <WiringEditor experimentId={GENERIC} experimentTitle="通用实验" /> }], { initialEntries: [`/experiments/${GENERIC}`] });
  render(<RouterProvider router={router} />);
  await screen.findByRole("button", { name: "添加空白图内器件" });
  const preflight = screen.getByRole("region", { name: "接线预检清单" });
  await within(preflight).findByText(/实验接线要求读取失败，未核对型号 \/ 用途要求/);
  fireEvent.click(screen.getByRole("button", { name: "添加空白图内器件" }));
  fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "仍可编辑" } });
  expect(screen.getByLabelText("图内器件名称")).toHaveValue("仍可编辑");
  expect(puts()).toHaveLength(0);
}, 15000);

it("接线预检清单汇总缺失型号、未绑定用途、跟随、诊断与档案差异，并可定位到器件或连线", async () => {
  definitionExtras = { wiring_notes: ["先关闭输出再改接探头。"], required_devices: ["HF2"], required_mapping_keys: ["read"] };
  stored.mapping_groups = [{ keys: ["drive"], label: "驱动", device_library_id: null }];
  stored.follow_status = { site: [], targets: { [GENERIC]: [{ device_id: "device-drive", state: "pending", mapping_keys: ["drive"], message: "等待确认用途" }] } };
  stored.diagnostics = { site: [], targets: { [GENERIC]: [{ edge_id: "edge-preflight", code: "direction", message: "方向待确认" }] } };
  stored.targets[GENERIC] = {
    nodes: [
      { device: { id: "device-drive", name: "驱动源", model: "DG4000", device_library_id: "library-drive", ports: [] }, x: 60, y: 60, follow_mapping: ["drive"] },
      { device: { id: "device-read", name: "读取器", model: "人工器件", device_library_id: null, ports: [] }, x: 420, y: 60 },
    ],
    edges: [{ id: "edge-preflight", source: { device_id: "device-drive", port_id: "out" }, target: { device_id: "device-read", port_id: "in" }, signal_type: "electrical", label: "待核对连线" }],
  };
  await open(GENERIC); await target();
  expect(within(panel()).getByText("先关闭输出再改接探头。")).toBeInTheDocument();
  const preflight = within(panel()).getByRole("region", { name: "接线预检清单" });
  await waitFor(() => expect(preflight).toHaveTextContent("警告 5 项"));
  expect(preflight).toHaveTextContent("阻断 0 项");
  expect(within(preflight).getByText("HF2：图中未找到此型号器件。")).toBeInTheDocument();
  expect(within(preflight).getByText("用途 read：图中无器件绑定此用途组。")).toBeInTheDocument();
  expect(within(preflight).getByText("驱动源：等待确认用途（drive）")).toBeInTheDocument();
  expect(within(preflight).getByText("驱动源 ↔ 读取器：方向待确认")).toBeInTheDocument();
  expect(within(preflight).getByText("驱动源：与设备端口档案存在差异（档案中不存在）；请人工核对。")).toBeInTheDocument();
  expect(within(preflight).getAllByText("警告")).toHaveLength(5);
  expect(within(preflight).getAllByText("要求")).toHaveLength(2);
  expect(within(preflight).getByText("跟随")).toBeInTheDocument();
  expect(within(preflight).getByText("诊断")).toBeInTheDocument();
  expect(within(preflight).getByText("档案")).toBeInTheDocument();

  const followItem = within(preflight).getByText("驱动源：等待确认用途（drive）").closest("li")!;
  fireEvent.click(within(followItem).getByRole("button", { name: /^定位：/ }));
  expect(within(panel()).getByLabelText("图内器件名称")).toHaveValue("驱动源");
  const diagnosticItem = within(preflight).getByText("驱动源 ↔ 读取器：方向待确认").closest("li")!;
  fireEvent.click(within(diagnosticItem).getByRole("button", { name: /^定位：/ }));
  expect(within(panel()).getByLabelText("连线标签")).toHaveValue("待核对连线");
  expect(puts()).toHaveLength(0);
}, 15000);

it("归档实验仅提供目标 Tab，不读取参数或恢复运行；通用页不凭空增加历史", async () => {
  await open(ARCHIVED);
  expect(screen.getAllByRole("tab").map(item => item.textContent)).toEqual(["目标接线图"]);
  await waitFor(() => expect(within(panel()).getByRole("button", { name: "添加空白图内器件" })).toBeEnabled());
  expect(screen.getByText(/此实验已归档，GUI/)).toBeInTheDocument();
  add("归档目标"); await save();
  expect(stored.targets[ARCHIVED].nodes[0].device.name).toBe("归档目标");
  expect(requests.some(item => item.path === `/api/experiments/${ARCHIVED}/schema`)).toBe(false);
  cleanup(); requests.length = 0;
  await open(GENERIC);
  expect(screen.getAllByRole("tab").map(item => item.textContent)).toEqual(["运行", "目标接线图"]);
}, 15000);
