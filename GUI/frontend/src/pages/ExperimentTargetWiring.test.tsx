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
let extraFields: SchemaField[];
const controlCatalog = { device_library_revision: "lib-r1", physical_mapping_revision: "map-r1", targets: [{ mapping_key: "Time_sequence_2", label: "时序信号2", description: "", kind: "generator", device_id: "dg4e261300744", device_label: "王湾z", model: "DG4000", short_resource: "192.168.124.33", endpoint: { kind: "channel", index: 2 }, shared_mapping_keys: [], safety: {}, read_only: false }] };
const generatorSnapshot = { id: "TCPIP0::192.168.124.33::INSTR", type: "DG4000", label: "王湾z", resource: "TCPIP0::192.168.124.33::INSTR", short_resource: "192.168.124.33", options: {}, idn: "RIGOL", reference_clock: "EXT",
  channels: [{ number: 2, mapping_key: "Time_sequence_2", mapping_keys: ["Time_sequence_2"], label: "时序信号2", read_only: false, output: true, shape: "SQUare", frequency: 100, amplitude: 5, offset: 2.5, phase: 0, voltage_unit: "VPP", load: "INFINITY", square_duty: 50, ramp_symmetry: null, pulse_width: null, pulse_delay: null,
    mod: { enabled: false, type: null }, burst: { enabled: false }, readback_errors: [] }] };
let currentGeneratorSnapshot: typeof generatorSnapshot;
let currentControlCatalog: typeof controlCatalog;
let hardwareBusy: boolean;
let generatorFailure: "" | "error" | "conflict";
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
  definitionExtras = {}; definitionFailure = ""; extraFields = [];
  currentGeneratorSnapshot = structuredClone(generatorSnapshot); currentControlCatalog = structuredClone(controlCatalog);
  hardwareBusy = false; generatorFailure = "";
  requests.length = 0; forbidden.length = 0; sources.length = 0; localStorage.clear();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  vi.spyOn(window, "scrollBy").mockImplementation(() => {});
  vi.stubGlobal("EventSource", TestEventSource);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input); const method = options?.method ?? "GET";
    const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    requests.push({ path, method, body });
    if (path === "/api/health" && method === "GET") return response({ status: "ok", hardware_busy: hardwareBusy });
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
      if (path === `${base}/schema` && method === "GET" && id !== ARCHIVED) return response({ schema_version: 2, fields: zeroFields ? [] : [field, ...extraFields], parameter_layout_saved: true, parameter_layout: { basic: zeroFields ? [] : [field.name], advanced: [] } });
      if (path === `${base}/workflow` && method === "GET" && id !== ARCHIVED) return response(flow);
      if (path === `${base}/sessions/active` && method === "GET" && id !== ARCHIVED) return response(activeSessionId === id ? { session_id: "existing-session", job_id: "session-job", revision: 1, status: "waiting", pending_request: null, parameters: { RUN_TAG: "already-applied" }, steps: {}, outputs: {}, current_step: null, run_id: null } : null);
      if (path.startsWith(`${base}/runs?`) && method === "GET" && id !== ARCHIVED) return response({ runs: [], total: 0, limit: 50, offset: 0 });
      if (path === `${base}/derive` && method === "POST" && id !== ARCHIVED) return response({ ok: true, values: {} });
    }
    if (path === "/api/runs?experiment_id=scope-capture&limit=50&offset=0" && method === "GET") return response({ runs: [], total: 0 });
    if (path === "/api/control-targets" && method === "GET") return response(currentControlCatalog);
    if (path === "/api/control-targets/Time_sequence_2/refresh" && method === "POST") return response({ target: currentControlCatalog.targets[0], snapshot: currentGeneratorSnapshot, read_at: "2026-11-11T12:00:00Z" });
    if (path === "/api/control-targets/Time_sequence_2/generator" && method === "PUT") {
      if (generatorFailure) return response({ detail: generatorFailure === "conflict" ? "仪器映射已变化" : "仪器写入失败" }, generatorFailure === "conflict" ? 409 : 500, generatorFailure === "conflict" ? "revision_conflict" : undefined);
      currentGeneratorSnapshot = { ...currentGeneratorSnapshot, channels: currentGeneratorSnapshot.channels.map(channel => ({ ...channel, ...body.settings })) };
      return response({ target: currentControlCatalog.targets[0], snapshot: currentGeneratorSnapshot, read_at: "2026-11-11T12:01:00Z" });
    }
    forbidden.push(`${method} ${path}`);
    throw new Error(`禁止的未模拟请求：${method} ${path}`);
  }));
});
afterEach(() => {
  cleanup();
  expect(forbidden).toEqual([]);
  expect(requests.filter(item => item.method !== "GET").every(item => item.path === "/api/wiring" && item.method === "PUT" || item.path === "/api/wiring/compare" && item.method === "POST" || /\/derive$/.test(item.path) && item.method === "POST" || /^\/api\/control-targets\/[^/]+\/refresh$/.test(item.path) && item.method === "POST" || item.path === "/api/control-targets/Time_sequence_2/generator" && item.method === "PUT")).toBe(true);
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
  expect(within(panel()).getByRole("region", { name: "目标尚未建立" })).toBeInTheDocument();
  for (const name of ["检查用途与接线图更新", "显式新建空目标", "导出/打印核对清单"]) expect(within(panel()).queryByRole("button", { name })).not.toBeInTheDocument();
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
  await waitFor(() => expect(within(panel()).getByRole("button", { name: "用现场图覆盖当前目标…" })).toBeEnabled());
  expect(within(panel()).queryByRole("combobox", { name: "目标实验" })).not.toBeInTheDocument();
  fireEvent.click(within(panel()).getByRole("button", { name: "用现场图覆盖当前目标…" }));
  expect(within(panel()).getByRole("button", { name: "移动器件：RF 目标保持不变" })).toBeInTheDocument();
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.click(within(panel()).getByRole("button", { name: "用现场图覆盖当前目标…" }));
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

it("不展示接线要点，缺失型号与未绑定用途进入预检且不自动添加器件或连线", async () => {
  definitionExtras = { wiring_notes: ["先关闭输出再改接探头。"], required_devices: ["驱动 (DG4000)", "HF2"], required_mapping_keys: ["drive", "read"] };
  stored.mapping_groups = [{ keys: ["drive"], label: "驱动", device_library_id: null }, { keys: ["read"], label: "读取", device_library_id: null }];
  stored.targets[GENERIC] = { nodes: [{ device: { id: "device-drive", name: "驱动源", model: "DG4000", device_library_id: null, ports: [] }, x: 60, y: 60, follow_mapping: ["drive"] }], edges: [] };
  await open(GENERIC); await target();
  expect(within(panel()).queryByText("接线要点")).not.toBeInTheDocument();
  expect(within(panel()).queryByText("先关闭输出再改接探头。")).not.toBeInTheDocument();
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
  expect(within(panel()).queryByText("先关闭输出再改接探头。")).not.toBeInTheDocument();
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

it("空目标在画布区给出从现场复制 / 从空白开始，复制空目标不弹覆盖确认；⋯ 菜单提供重新载入与全局链接", async () => {
  await open(GENERIC); await target();
  const empty = within(panel()).getByRole("region", { name: "目标尚未建立" });
  fireEvent.click(within(empty).getByRole("button", { name: "从现场图复制" }));
  expect(window.confirm).not.toHaveBeenCalled();
  expect(within(panel()).getByRole("button", { name: "移动器件：现场保持不变" })).toBeInTheDocument();
  expect(within(panel()).queryByRole("region", { name: "目标尚未建立" })).not.toBeInTheDocument();
  const menu = within(panel()).getByText("⋯").closest("details")!;
  expect(within(menu).getByRole("button", { name: "重新载入" })).toBeInTheDocument();
  expect(within(menu).getByRole("link", { name: /打开全局接线图/ })).toHaveAttribute("href", "/instruments/wiring");
  expect(within(menu).getByRole("link", { name: /设备端口建档/ })).toHaveAttribute("href", "/instruments/ports");
  // 已有内容时覆盖仍需确认，取消则保持草稿。
  fireEvent.click(within(menu).getByRole("button", { name: "用现场图覆盖当前目标…" }));
  expect(window.confirm).toHaveBeenCalledTimes(1);
  expect(puts()).toHaveLength(0);
}, 15000);

it("空目标选择从空白开始只建立空目标，不复制现场", async () => {
  await open(GENERIC); await target();
  fireEvent.click(within(within(panel()).getByRole("region", { name: "目标尚未建立" })).getByRole("button", { name: "从空白开始" }));
  expect(within(panel()).queryByRole("region", { name: "目标尚未建立" })).not.toBeInTheDocument();
  expect(within(panel()).queryByRole("button", { name: "移动器件：现场保持不变" })).not.toBeInTheDocument();
  expect(within(panel()).getByText(/0 个器件 \/ 0 条连线/)).toBeInTheDocument();
}, 15000);

it("调试页选中器件时按用途键只读列出本实验已保存参数，不写参数也不改接线", async () => {
  extraFields = [
    { name: "XY_TRIGGER_FREQ", label: "XYZ 共同触发频率", type: "number", default: 100, group: "basic", unit: "Hz", roles: ["Time_sequence_2"] },
    { name: "PUMP_MOD_FREQ", label: "Pump / Larmor 频率", type: "number", default: 10000, group: "basic", unit: "Hz", roles: ["Time_sequence", "lockin_r"] },
    { name: "Z_RF_FREQ", label: "Z RF 频率", type: "number", default: 8000, group: "basic", unit: "Hz", roles: ["Z_magnetic_field"] },
  ];
  stored.targets[RF] = { nodes: [{ ...graph("时序信号源").nodes[0], follow_mapping: ["Time_sequence_2", "Time_sequence"] }], edges: [] };
  await open(RF);
  const view = await target();
  expect(within(view).queryByRole("region", { name: "器件关联实验参数" })).not.toBeInTheDocument();
  fireEvent.click(within(view).getByRole("tab", { name: "调试" }));
  const parameters = within(view).getByRole("region", { name: "器件关联实验参数" });
  expect(within(parameters).getByText("在画布中选中器件后，显示本实验写入该器件的参数。")).toBeInTheDocument();
  fireEvent.click(within(view).getByRole("button", { name: "移动器件：时序信号源" }));
  await within(parameters).findByText("XYZ 共同触发频率");
  expect(within(parameters).getByText("100 Hz")).toBeInTheDocument();
  expect(within(parameters).getByText("10000 Hz")).toBeInTheDocument();
  expect(within(parameters).getByText("同时作用于 lockin_r")).toBeInTheDocument();
  expect(within(parameters).queryByText("Z RF 频率")).not.toBeInTheDocument();
  expect(within(parameters).queryByText("运行标签")).not.toBeInTheDocument();
  expect(within(parameters).queryByRole("textbox")).not.toBeInTheDocument();
  expect(puts()).toHaveLength(0);
  expect(requests.some(item => item.path.endsWith("/defaults"))).toBe(false);
}, 15000);

it("调试页按当前装置映射回读信号源通道，读取只发 refresh，不主动写仪器", async () => {
  stored.targets[RF] = { nodes: [{ ...graph("王湾z").nodes[0], follow_mapping: ["Time_sequence_2", "Z_magnetic_field"] }], edges: [] };
  await open(RF);
  const view = await target();
  fireEvent.click(within(view).getByRole("button", { name: "移动器件：王湾z" }));
  fireEvent.click(within(view).getByRole("tab", { name: "调试" }));
  const debug = within(view).getByRole("region", { name: "器件调试" });
  // 只列出映射中存在的用途；Z_magnetic_field 不在目标清单中，不凭空生成。
  const item = await within(debug).findByRole("article", { name: "调试目标：时序信号2" });
  expect(within(debug).queryAllByRole("article")).toHaveLength(1);
  expect(within(item).getByText("王湾z · 192.168.124.33 · CH2")).toBeInTheDocument();
  expect(within(item).getByText("尚未读取。")).toBeInTheDocument();
  expect(within(item).getByRole("button", { name: "写入仪器" })).toBeDisabled();
  expect(within(item).getByRole("button", { name: "打开输出" })).toBeDisabled();
  const read = within(item).getByRole("button", { name: "读取" });
  await waitFor(() => expect(read).toBeEnabled());
  fireEvent.click(read);
  await within(item).findByText("100 Hz");
  expect(within(item).getByText("5 Vpp")).toBeInTheDocument();
  expect(within(item).getByText("50 %")).toBeInTheDocument();
  expect(within(item).getByText("高阻")).toBeInTheDocument();
  expect(within(item).getAllByRole("textbox")).toHaveLength(5);
  expect(requests.filter(entry => entry.path.startsWith("/api/control-targets") && entry.method !== "GET").map(entry => `${entry.method} ${entry.path}`)).toEqual(["POST /api/control-targets/Time_sequence_2/refresh"]);
  expect(requests.find(entry => entry.path.endsWith("/refresh"))?.body).toEqual({ device_library_revision: "lib-r1", physical_mapping_revision: "map-r1" });
  expect(puts()).toHaveLength(0);
}, 15000);

async function readDebugTarget() {
  stored.targets[RF] = { nodes: [{ ...graph("时序信号源").nodes[0], follow_mapping: ["Time_sequence_2"] }], edges: [] };
  await open(RF);
  const view = await target();
  fireEvent.click(within(view).getByRole("button", { name: "移动器件：时序信号源" }));
  fireEvent.click(within(view).getByRole("tab", { name: "调试" }));
  const item = await within(view).findByRole("article", { name: "调试目标：时序信号2" });
  const read = within(item).getByRole("button", { name: "读取" });
  await waitFor(() => expect(read).toBeEnabled());
  fireEvent.click(read);
  await within(item).findByText("高阻");
  return item;
}

it("调试页按 setting 比较全部已保存参数，频率差异标记且相位 360 与 0 相同", async () => {
  extraFields = [
    { name: "XY_TRIGGER_FREQ", label: "XYZ 共同触发频率", type: "number", default: 120, group: "basic", unit: "Hz", setting: { role: "Time_sequence_2", field: "frequency" } },
    { name: "XY_TRIGGER_AMPLITUDE", label: "XYZ 共同触发幅度", type: "number", default: 5, group: "basic", unit: "Vpp", setting: { role: "Time_sequence_2", field: "amplitude" } },
    { name: "XY_TRIGGER_PHASE", label: "XYZ 共同触发相位", type: "number", default: 360, group: "basic", unit: "°", setting: { role: "Time_sequence_2", field: "phase" } },
    { name: "XY_SHARED_FREQ", label: "共享触发频率", type: "number", default: 100, group: "basic", unit: "Hz", setting: { role: "Time_sequence_2", field: "frequency" } },
    { name: "PUMP_MOD_FREQ", label: "其他用途频率", type: "number", default: 8000, group: "basic", unit: "Hz", setting: { role: "Time_sequence", field: "frequency" } },
  ];
  const item = await readDebugTarget();
  const table = within(item).getByRole("table");
  const frequency = within(table).getByRole("rowheader", { name: "频率" }).closest("tr")!;
  expect(frequency).toHaveClass("wiring-debug-diff");
  expect(within(frequency).getByText("≠")).toBeInTheDocument();
  expect(frequency).toHaveTextContent("XYZ 共同触发频率");
  expect(frequency).toHaveTextContent("120 Hz");
  expect(frequency).toHaveTextContent("共享触发频率");
  expect(within(table).queryByText("其他用途频率")).not.toBeInTheDocument();
  for (const label of ["幅度", "相位"]) {
    const row = within(table).getByRole("rowheader", { name: label }).closest("tr")!;
    expect(row).not.toHaveClass("wiring-debug-diff");
    expect(within(row).queryByText("≠")).not.toBeInTheDocument();
  }
  expect(table).toHaveTextContent("360 °");
}, 15000);

const generatorPuts = () => requests.filter(item => item.path === "/api/control-targets/Time_sequence_2/generator" && item.method === "PUT");

it("调试页只写入填写的有限数频率，更新回读并清空输入，非法值不发请求", async () => {
  const item = await readDebugTarget();
  const frequency = within(item).getByRole("textbox", { name: "待写入：时序信号2 频率" });
  expect(frequency).toHaveAttribute("type", "text");
  expect(frequency).toHaveAttribute("inputmode", "decimal");
  expect(frequency).toHaveAttribute("placeholder", "100");
  fireEvent.change(frequency, { target: { value: "110" } });
  fireEvent.click(within(item).getByRole("button", { name: "写入仪器" }));
  await within(item).findByText("110 Hz");
  expect(generatorPuts().map(entry => entry.body)).toEqual([{
    device_library_revision: "lib-r1", physical_mapping_revision: "map-r1", settings: { frequency: 110 },
  }]);
  expect(frequency).toHaveValue("");
  expect(frequency).toHaveAttribute("placeholder", "110");
  expect(within(item).getByText(`读取于 ${new Date("2026-11-11T12:01:00Z").toLocaleTimeString()}`)).toBeInTheDocument();
  fireEvent.change(frequency, { target: { value: "abc" } });
  fireEvent.click(within(item).getByRole("button", { name: "写入仪器" }));
  expect(within(item).getByRole("alert")).toHaveTextContent("频率");
  expect(generatorPuts()).toHaveLength(1);
  expect(frequency).toHaveValue("abc");
  expect(within(item).getByText("110 Hz")).toBeInTheDocument();
}, 15000);

it("调试页输出独立确认，取消不请求，确认关闭与打开都只发送 output", async () => {
  const item = await readDebugTarget();
  fireEvent.change(within(item).getByRole("textbox", { name: "待写入：时序信号2 频率" }), { target: { value: "110" } });
  const close = within(item).getByRole("button", { name: "关闭输出" });
  fireEvent.click(close);
  expect(window.confirm).toHaveBeenCalledWith("确认关闭「时序信号2」输出？");
  expect(generatorPuts()).toHaveLength(0);
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.click(close);
  await within(item).findByRole("button", { name: "打开输出" });
  expect(generatorPuts().map(entry => entry.body)).toEqual([{
    device_library_revision: "lib-r1", physical_mapping_revision: "map-r1", settings: { output: false },
  }]);
  fireEvent.click(within(item).getByRole("button", { name: "打开输出" }));
  expect(window.confirm).toHaveBeenLastCalledWith("确认打开「时序信号2」输出？");
  await within(item).findByRole("button", { name: "关闭输出" });
  expect(generatorPuts().map(entry => (entry.body as { settings: unknown }).settings)).toEqual([{ output: false }, { output: true }]);
}, 15000);

it.each([
  ["Burst", "Burst/调制通道第一版只读，请在仪器控制页操作"],
  ["调制", "Burst/调制通道第一版只读，请在仪器控制页操作"],
  ["USER1", "任意波通道只读"],
  ["ARBitrary", "任意波通道只读"],
  ["Vrms", "幅度单位不是 Vpp，只读"],
  ["目标只读", "该目标或通道只读，请在仪器控制页操作"],
  ["通道只读", "该目标或通道只读，请在仪器控制页操作"],
])("调试页 %s 禁止参数写入与打开，关闭输出遵守只读标志", async (mode, reason) => {
  const channel = currentGeneratorSnapshot.channels[0];
  if (mode === "Burst") channel.burst.enabled = true;
  else if (mode === "调制") channel.mod.enabled = true;
  else if (mode === "Vrms") channel.voltage_unit = "VRMS";
  else if (mode === "目标只读") currentControlCatalog.targets[0].read_only = true;
  else if (mode === "通道只读") channel.read_only = true;
  else channel.shape = mode;
  const item = await readDebugTarget();
  expect(within(item).getByText(reason)).toHaveClass("wiring-help");
  for (const input of within(item).getAllByRole("textbox")) expect(input).toBeDisabled();
  expect(within(item).getByRole("button", { name: "写入仪器" })).toBeDisabled();
  const close = within(item).getByRole("button", { name: "关闭输出" });
  if (mode === "目标只读" || mode === "通道只读") {
    expect(close).toBeDisabled();
  } else {
    expect(close).toBeEnabled();
    vi.mocked(window.confirm).mockReturnValue(true);
    fireEvent.click(close);
    expect(await within(item).findByRole("button", { name: "打开输出" })).toBeDisabled();
    expect(generatorPuts().map(entry => (entry.body as { settings: unknown }).settings)).toEqual([{ output: false }]);
  }
}, 15000);

it("调试页 DC 仅显示输出、波形、电平和负载，没有待写入列且仍可关闭输出", async () => {
  currentGeneratorSnapshot.channels[0].shape = "DC";
  const item = await readDebugTarget();
  expect(within(item).getByText("DC 通道第一版只读")).toBeInTheDocument();
  const table = within(item).getByRole("table");
  expect(within(table).getAllByRole("rowheader").map(cell => cell.textContent)).toEqual(["输出", "波形", "电平（偏置）", "负载"]);
  expect(within(table).queryByRole("columnheader", { name: "待写入" })).not.toBeInTheDocument();
  expect(within(item).queryByRole("textbox")).not.toBeInTheDocument();
  expect(within(item).getByRole("button", { name: "写入仪器" })).toBeDisabled();
  expect(within(item).getByRole("button", { name: "关闭输出" })).toBeEnabled();
}, 15000);

it("调试页硬件任务忙时禁止读取、参数写入与关闭输出", async () => {
  const item = await readDebugTarget();
  hardwareBusy = true;
  await waitFor(() => expect(within(item).getByRole("button", { name: "写入仪器" })).toBeDisabled(), { timeout: 3500 });
  expect(within(item).getByText("其他硬件任务运行中")).toHaveClass("wiring-help");
  expect(within(item).getByRole("button", { name: "重新读取" })).toBeDisabled();
  expect(within(item).getByRole("button", { name: "关闭输出" })).toBeDisabled();
  for (const input of within(item).getAllByRole("textbox")) expect(input).toBeDisabled();
  expect(generatorPuts()).toHaveLength(0);
}, 15000);

it("调试页使用绝对与相对容差，只比较有限数值并归一化负相位", async () => {
  currentGeneratorSnapshot.channels[0].offset = 5e-10;
  extraFields = [
    { name: "FREQ_CLOSE", label: "容差内频率", type: "number", default: 100.00005, group: "basic", unit: "Hz", setting: { role: "Time_sequence_2", field: "frequency" } },
    { name: "OFFSET_CLOSE", label: "容差内偏置", type: "number", default: 0, group: "basic", unit: "V", setting: { role: "Time_sequence_2", field: "offset" } },
    { name: "PHASE_NEGATIVE", label: "负整周相位", type: "number", default: -360, group: "basic", unit: "°", setting: { role: "Time_sequence_2", field: "phase" } },
    { name: "AMP_TEXT", label: "非数值参数", type: "string", default: "不是数值", group: "basic", unit: "", setting: { role: "Time_sequence_2", field: "amplitude" } },
    { name: "DUTY_DIFFERENT", label: "不同占空比", type: "number", default: 60, group: "basic", unit: "%", setting: { role: "Time_sequence_2", field: "square_duty" } },
  ];
  const item = await readDebugTarget();
  for (const label of ["频率", "偏置", "相位", "幅度"]) expect(within(item).getByRole("rowheader", { name: label }).closest("tr")).not.toHaveClass("wiring-debug-diff");
  expect(within(item).getByRole("rowheader", { name: "占空比" }).closest("tr")).toHaveClass("wiring-debug-diff");
}, 15000);

it("调试页五项数值局部写入且空输入不请求，Vpp 单位大小写不敏感", async () => {
  currentGeneratorSnapshot.channels[0].voltage_unit = "vpp";
  const item = await readDebugTarget();
  fireEvent.click(within(item).getByRole("button", { name: "写入仪器" }));
  expect(generatorPuts()).toHaveLength(0);
  for (const [label, value] of [["频率", " 1.1e2 "], ["幅度", "4.5"], ["偏置", "-0.2"], ["相位", "-360"], ["占空比", "40"]]) {
    fireEvent.change(within(item).getByRole("textbox", { name: `待写入：时序信号2 ${label}` }), { target: { value } });
  }
  fireEvent.click(within(item).getByRole("button", { name: "写入仪器" }));
  await within(item).findByText("110 Hz");
  expect(generatorPuts().map(entry => (entry.body as { settings: unknown }).settings)).toEqual([{ frequency: 110, amplitude: 4.5, offset: -0.2, phase: -360, square_duty: 40 }]);
  for (const input of within(item).getAllByRole("textbox")) expect(input).toHaveValue("");
  for (const value of ["Infinity", "1e999"]) {
    fireEvent.change(within(item).getByRole("textbox", { name: "待写入：时序信号2 频率" }), { target: { value } });
    fireEvent.click(within(item).getByRole("button", { name: "写入仪器" }));
    expect(within(item).getByRole("alert")).toHaveTextContent("频率请输入有限数值");
    expect(generatorPuts()).toHaveLength(1);
  }
}, 15000);

it("调试页非方波不显示占空比输入，脉宽保持只读", async () => {
  currentGeneratorSnapshot.channels[0].shape = "PULSe";
  const item = await readDebugTarget();
  expect(within(item).queryByRole("textbox", { name: "待写入：时序信号2 占空比" })).not.toBeInTheDocument();
  expect(within(item).getAllByRole("textbox")).toHaveLength(4);
  expect(within(item).getByRole("rowheader", { name: "脉宽" }).closest("tr")).toHaveTextContent("—");
}, 15000);

it("调试页重新读取非方波后不提交已隐藏的占空比草稿", async () => {
  const item = await readDebugTarget();
  fireEvent.change(within(item).getByRole("textbox", { name: "待写入：时序信号2 占空比" }), { target: { value: "40" } });
  currentGeneratorSnapshot.channels[0].shape = "PULSe";
  fireEvent.click(within(item).getByRole("button", { name: "重新读取" }));
  await within(item).findByText("PULSe");
  fireEvent.change(within(item).getByRole("textbox", { name: "待写入：时序信号2 频率" }), { target: { value: "110" } });
  fireEvent.click(within(item).getByRole("button", { name: "写入仪器" }));
  await within(item).findByText("110 Hz");
  expect(generatorPuts().map(entry => (entry.body as { settings: unknown }).settings)).toEqual([{ frequency: 110 }]);
}, 15000);

it.each(["error", "conflict"] as const)("调试页写入 %s 显示错误，冲突清回读并重读目录", async failure => {
  const item = await readDebugTarget();
  const catalogReads = requests.filter(entry => entry.path === "/api/control-targets").length;
  generatorFailure = failure;
  fireEvent.change(within(item).getByRole("textbox", { name: "待写入：时序信号2 频率" }), { target: { value: "110" } });
  fireEvent.click(within(item).getByRole("button", { name: "写入仪器" }));
  const debug = within(panel()).getByRole("region", { name: "器件调试" });
  expect(await within(debug).findByRole("alert")).toHaveTextContent(failure === "conflict" ? "仪器映射已变化" : "仪器写入失败");
  if (failure === "conflict") {
    await waitFor(() => expect(requests.filter(entry => entry.path === "/api/control-targets")).toHaveLength(catalogReads + 1));
    expect(within(debug).getByText("尚未读取。")).toBeInTheDocument();
    expect(within(debug).queryByRole("table")).not.toBeInTheDocument();
    expect(within(debug).getByRole("button", { name: "写入仪器" })).toBeDisabled();
  } else {
    // 写入失败后仪器状态未知：作废旧回读，必须重新读取后才能再写或开输出。
    expect(within(item).getByText("尚未读取。")).toBeInTheDocument();
    expect(within(item).queryByText("100 Hz")).not.toBeInTheDocument();
    expect(within(item).getByRole("button", { name: "写入仪器" })).toBeDisabled();
    expect(within(item).getByRole("button", { name: "打开输出" })).toBeDisabled();
  }
}, 15000);

it("调试页存为实验参数只合并勾选项到运行页草稿，不保存参数也不写仪器", async () => {
  extraFields = [
    { name: "XY_TRIGGER_FREQ", label: "XYZ 共同触发频率", type: "number", default: 120, group: "basic", unit: "Hz", setting: { role: "Time_sequence_2", field: "frequency" } },
    { name: "XY_TRIGGER_OFFSET", label: "共同触发偏置", type: "number", default: 2, group: "basic", unit: "V", setting: { role: "Time_sequence_2", field: "offset" } },
    { name: "XY_TRIGGER_AMPLITUDE", label: "共同触发幅度", type: "number", default: 5, group: "basic", unit: "Vpp", setting: { role: "Time_sequence_2", field: "amplitude" } },
  ];
  const item = await readDebugTarget();
  fireEvent.click(within(item).getByRole("button", { name: "存为实验参数…" }));
  const choice = within(item).getByRole("group", { name: "存为实验参数：时序信号2" });
  // 只列出回读与草稿不同的绑定字段；幅度一致不列。
  expect(within(choice).getAllByRole("checkbox")).toHaveLength(2);
  expect(within(choice).queryByText(/共同触发幅度/)).not.toBeInTheDocument();
  fireEvent.click(within(choice).getByRole("checkbox", { name: /共同触发偏置/ }));
  fireEvent.click(within(choice).getByRole("button", { name: "写入草稿" }));
  expect(within(item).getByRole("status")).toHaveTextContent("已把 1 项写入运行页参数草稿，尚未保存");
  // 草稿已含回读频率：频率行不再标差异，偏置仍不同。
  expect(within(item).getByText("XYZ 共同触发频率：100 Hz")).toBeInTheDocument();
  expect(within(item).getByText("共同触发偏置：2 V")).toBeInTheDocument();
  expect(within(item).getAllByLabelText("与仪器回读不同")).toHaveLength(1);
  expect(requests.some(entry => entry.path.endsWith("/defaults"))).toBe(false);
  expect(requests.some(entry => entry.path.endsWith("/generator"))).toBe(false);
  expect(puts()).toHaveLength(0);
}, 15000);

it("调试状态切换接线页后保留，画布节点显示已回读、有未写入值与 ≠ 实验参数徽标", async () => {
  extraFields = [
    { name: "XY_TRIGGER_FREQ", label: "XYZ 共同触发频率", type: "number", default: 120, group: "basic", unit: "Hz", setting: { role: "Time_sequence_2", field: "frequency" } },
  ];
  const item = await readDebugTarget();
  const badges = () => within(panel()).getByLabelText("时序信号源调试状态");
  await waitFor(() => expect(within(badges()).getAllByText(/./).map(node => node.textContent)).toEqual(["已回读", "≠ 实验参数"]));
  fireEvent.change(within(item).getByRole("textbox", { name: "待写入：时序信号2 频率" }), { target: { value: "110" } });
  await waitFor(() => expect(within(badges()).getByText("有未写入值")).toBeInTheDocument());
  fireEvent.click(within(panel()).getByRole("tab", { name: "接线" }));
  expect(within(panel()).getByLabelText("图内器件名称")).toHaveValue("时序信号源");
  expect(within(badges()).getAllByText(/./)).toHaveLength(3);
  fireEvent.click(within(panel()).getByRole("tab", { name: "调试" }));
  const again = within(panel()).getByRole("article", { name: "调试目标：时序信号2" });
  expect(within(again).getByText("100 Hz")).toBeInTheDocument();
  expect(within(again).getByRole("textbox", { name: "待写入：时序信号2 频率" })).toHaveValue("110");
  expect(puts()).toHaveLength(0);
}, 15000);
