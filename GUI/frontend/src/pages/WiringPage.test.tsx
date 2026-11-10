import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "../App";
import { NavigationGuard } from "../components/NavigationGuard";
import type { ExperimentDefinition } from "../types/api";
import type { components } from "../types/openapi";
import type { WiringComparison, WiringDocument, WiringEdge, WiringGraph } from "../components/wiring/wiringTypes";

type Graph = WiringGraph;
type Edge = WiringEdge;
type Profile = components["schemas"]["DevicePortProfile"];
type Port = components["schemas"]["PhysicalPort"];
type Document = WiringDocument;
type Body = components["schemas"]["WiringBody"];
type CompareBody = components["schemas"]["WiringCompareBody"];
type Comparison = WiringComparison;
const TARGET = "rf-sensitivity-aw";
const port = (id: string, name: string, direction: Port["direction"] = null): Port => ({ id, name, group: "", signal_type: null, connector: "", purpose: "", direction, modes: [], selected_mode: null });
const profile = (id: string, name: string, ports: Port[] = []): Profile => ({ id, name, model: "手工型号", device_library_id: null, ports });
const generator = (): Profile => ({ ...profile("generator", "测试信号源", [
  { ...port("out", "CH1", "out"), signal_type: "electrical" },
  { ...port("ref", "10 MHz", "switchable"), signal_type: "electrical", modes: [{ id: "mode-in", name: "参考接收", direction: "in" }, { id: "mode-out", name: "参考输出", direction: "out" }] },
]), device_library_id: "library-generator" });
const receiver = (): Profile => profile("receiver", "测试锁相", [
  { ...port("signal-v", "V", "in"), group: "Signal Input", signal_type: "electrical" },
  { ...port("aux-v", "V", "in"), group: "Aux Input", signal_type: "electrical" },
]);
const graph = (...devices: Profile[]): Graph => ({ nodes: devices.map((device, index) => ({ device: structuredClone(device), x: 60 + index * 420, y: 60 })), edges: [] });
const edge = (id = "existing-edge", sourcePort = "out", targetPort = "signal-v"): Edge => ({ id, source: { device_id: "generator", port_id: sourcePort }, target: { device_id: "receiver", port_id: targetPort }, signal_type: "electrical", label: "" });
const initial = (): Document => ({ schema_version: 1, revision: "wiring-r1", site: graph(), targets: {}, diagnostics: { site: [], targets: {} } });
const experiment = (id = TARGET, title = "RF 灵敏度（DirectAW）"): ExperimentDefinition => ({ id, title, archived: false, category: "test", category_label: "测试", family: "rf", variant: "aw", description: "", required_devices: [], required_mapping_keys: [], execution_mode: "typed_workflow", acquisition_program: "test", analysis_program: null, wiring_notes: [], safety_notes: [], supports_cancel: true, can_analyze: false });
let stored: Document;
let profiles: Profile[];
let experiments: ExperimentDefinition[];
let readFailure: boolean;
let profilesFailure: boolean;
let experimentsFailure: boolean;
let saveFailure: "" | "error" | "conflict";
let pendingSave: Promise<void> | undefined;
let pendingCompare: Promise<void> | undefined;
let compareResult: Comparison;
const requests: Array<{ path: string; method: string; body?: Body | CompareBody }> = [];
const forbidden: string[] = [];
const response = (body: unknown, status = 200, code?: string) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...(code ? { "X-Error-Code": code } : {}) } });
let captureOwner: Element | null = null;
let capturedOn: Element | null = null;
let planeOffset = { x: 0, y: 0 };

beforeEach(() => {
  stored = initial(); profiles = [generator(), receiver()]; experiments = [experiment()]; readFailure = false; profilesFailure = false; experimentsFailure = false; saveFailure = ""; pendingSave = undefined; pendingCompare = undefined;
  compareResult = { retained: [], removed: [], added: [], mode_changes: [], site_warnings: [], target_warnings: [] };
  requests.length = 0; forbidden.length = 0; captureOwner = null; capturedOn = null; planeOffset = { x: 0, y: 0 }; localStorage.clear();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  // jsdom 没有 PointerEvent / 捕获；保留真实 pointerdown→pointerup→click 序列及捕获目标语义。
  class TestPointerEvent extends MouseEvent {
    pointerId: number;
    constructor(type: string, options: PointerEventInit = {}) { super(type, options); this.pointerId = options.pointerId ?? 1; }
  }
  vi.stubGlobal("PointerEvent", TestPointerEvent);
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const node = this.closest<HTMLElement>(".wiring-node");
    const translation = node?.style.transform.match(/translate\(([-\d.]+)px, ([-\d.]+)px\)/);
    const x = (Number.parseInt(node?.style.left ?? "0") || 0) + Number(translation?.[1] ?? 0); const y = (Number.parseInt(node?.style.top ?? "0") || 0) + Number(translation?.[2] ?? 0);
    if (this.matches("text[data-edge-label]")) return { x: 0, y: 0, left: 0, top: 0, right: 100, bottom: 14, width: 100, height: 14, toJSON: () => ({}) };
    const isPort = this.matches("[data-port-id]");
    const index = Array.from(this.parentElement?.parentElement?.querySelectorAll("[data-port-id]") ?? []).indexOf(this);
    const left = (isPort ? x + (this.getAttribute("data-side") === "right" ? 140 : 8) : x) + planeOffset.x;
    const top = (isPort ? y + 50 + index * 30 : y) + planeOffset.y;
    const width = isPort ? 100 : node ? 248 : 2600; const height = isPort ? 20 : node ? 180 : 1600;
    return { x: left, y: top, left, top, right: left + width, bottom: top + height, width, height, toJSON: () => ({}) };
  });
  Object.defineProperties(Element.prototype, {
    setPointerCapture: { configurable: true, value: vi.fn(function (this: Element) { captureOwner = this; capturedOn = this; }) },
    hasPointerCapture: { configurable: true, value: vi.fn(function (this: Element) { return captureOwner === this; }) },
    releasePointerCapture: { configurable: true, value: vi.fn(function (this: Element) { if (captureOwner === this) captureOwner = null; }) },
  });
  Object.defineProperty(document, "elementFromPoint", { configurable: true, value: vi.fn(() => null) });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input); const method = options?.method ?? "GET";
    const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    requests.push({ path, method, body });
    if (path === "/api/health" && method === "GET") return response({ status: "ok", hardware_busy: false });
    if (path === "/api/jobs" && method === "GET") return response([{ id: "passive-job", kind: "clock-sync", status: "running", percent: 20, message: "全局任务横幅保留" }]);
    if (path === "/api/control-targets" && method === "GET") return response({ device_library_revision: "library-r1", physical_mapping_revision: "mapping-r1", targets: [] });
    if (path === "/api/device-library" && method === "GET") return response({ schema_version: 1, revision: "library-r1", network_resources: [], devices: {} });
    if (path === "/api/device-ports" && method === "GET") return profilesFailure ? response({ detail: "档案暂不可用" }, 503) : response({ schema_version: 1, revision: "ports-r1", devices: profiles, presets: [] });
    if (path === "/api/experiments" && method === "GET") return experimentsFailure ? response({ detail: "实验列表暂不可用" }, 503) : response(experiments);
    if (path === "/api/wiring" && method === "GET") return readFailure ? response({ detail: "接线存储读取失败" }, 500) : response(stored);
    if (path === "/api/wiring" && method === "PUT") {
      await pendingSave;
      if (saveFailure === "error") return response({ detail: "磁盘写入失败" }, 500);
      if (saveFailure === "conflict") return response({ detail: "其它窗口已修改接线图" }, 409, "revision_conflict");
      stored = { ...stored, revision: "wiring-r2", site: body.site, targets: body.targets };
      return response(stored);
    }
    if (path === "/api/wiring/compare" && method === "POST") { await pendingCompare; return response(compareResult); }
    forbidden.push(`${method} ${path}`); throw new Error(`禁止的未模拟请求：${method} ${path}`);
  }));
});
afterEach(() => {
  cleanup(); expect(forbidden).toEqual([]);
  expect(requests.every(item => item.method === "GET" || item.method === "PUT" && item.path === "/api/wiring" || item.method === "POST" && item.path === "/api/wiring/compare")).toBe(true);
  delete (Element.prototype as Partial<Element>).setPointerCapture; delete (Element.prototype as Partial<Element>).hasPointerCapture; delete (Element.prototype as Partial<Element>).releasePointerCapture;
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});
async function openPage(path = "/instruments/wiring") {
  const router = createMemoryRouter([{ path: "*", element: <NavigationGuard><App /></NavigationGuard> }], { initialEntries: [path] });
  render(<RouterProvider router={router} />);
  await screen.findByRole("heading", { name: path.endsWith("wiring") ? "实验接线图" : path.endsWith("ports") ? "设备端口建档" : "仪器控制", level: 1 }, { timeout: 15000 });
  return router;
}
async function ready() { await waitFor(() => expect(screen.getByRole("button", { name: "添加空白图内器件" })).toBeEnabled()); }
const puts = () => requests.filter(item => item.method === "PUT");
const compares = () => requests.filter(item => item.method === "POST");
const body = () => puts().at(-1)?.body as Body;
const snapshot = (name: string) => screen.getByRole("button", { name: `移动器件：${name}` });
const endpoint = (name: string) => screen.getByRole("button", { name: `连接端口：${name}` });
const plane = () => screen.getByTestId("wiring-plane");
const searchBox = () => screen.getByLabelText("搜索器件、端口或连线标签");
function add(name: string) { fireEvent.click(within(screen.getByRole("group", { name: "加入器件" })).getByRole("button", { name: new RegExp(name) })); }
function usePorts(name: string) {
  const toggle = screen.queryByRole("button", { name: new RegExp(`^未用端口：${name}（`) });
  if (!toggle) return;
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
  const list = screen.getByLabelText(`${name}未用端口`);
  for (const button of within(list).getAllByRole("button", { name: /^使用状态：/ })) fireEvent.click(button);
}
function pointerClick(element: Element, pointerId = 1) {
  capturedOn = null;
  fireEvent.pointerDown(element, { pointerId, button: 0, clientX: 100, clientY: 100 });
  const clicked = capturedOn ?? element;
  fireEvent.pointerUp(captureOwner ?? element, { pointerId, button: 0, clientX: 100, clientY: 100 });
  fireEvent.click(clicked, { detail: 1 });
}
async function saveAll() {
  fireEvent.click(screen.getByRole("button", { name: "保存全部接线图" }));
  await screen.findByText("全部接线图已保存。");
}
function makeLine(source: string, target: string, type: "optical" | "electrical" = "electrical", label = "") {
  pointerClick(endpoint(source)); pointerClick(endpoint(target));
  expect(screen.getByLabelText("新连线光电类型")).toHaveValue("");
  expect(screen.getByRole("button", { name: "记录连线" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("新连线光电类型"), { target: { value: type } });
  fireEvent.change(screen.getByLabelText("新连线标签"), { target: { value: label } });
  fireEvent.click(screen.getByRole("button", { name: "记录连线" }));
}
function selectEdge(index = 0) { fireEvent.click(screen.getAllByRole("button", { name: /^选择连线记录：/ })[index]); }

it("真实仪器与建档页入口、直接路由及全局横幅保留，空现场不创建文件", async () => {
  const router = await openPage("/instruments");
  fireEvent.click(screen.getByRole("link", { name: "实验接线图" }));
  await screen.findByRole("heading", { name: "实验接线图", level: 1 }); await ready();
  expect(screen.getByText("全局任务横幅保留")).toBeVisible();
  expect(screen.getByLabelText("目标实验")).toHaveValue(TARGET);
  expect(screen.getByRole("button", { name: "共享现场图" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled();
  expect(screen.queryByRole("group", { name: /^器件快照：/ })).not.toBeInTheDocument();
  expect(puts()).toHaveLength(0);
  fireEvent.click(screen.getByRole("link", { name: "设备端口建档" }));
  await screen.findByRole("heading", { name: "设备端口建档", level: 1 });
  fireEvent.click(screen.getByRole("link", { name: "实验接线图" })); await ready();
  await act(async () => router.navigate("/instruments/wiring"));
  expect(screen.getByRole("heading", { name: "实验接线图", level: 1 })).toBeVisible();
  expect(requests.filter(item => item.method !== "GET")).toEqual([]);
}, 15000);

it("主图读取失败禁编辑与空保存，辅助请求仍独立，明确重试才开放", async () => {
  readFailure = true;
  await openPage();
  expect(await screen.findByText(/接线图读取失败，已禁止编辑和空图保存/)).toBeVisible();
  expect(screen.getByRole("button", { name: "添加空白图内器件" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "保存全部接线图" })); expect(puts()).toHaveLength(0);
  expect(requests.map(item => item.path)).toEqual(expect.arrayContaining(["/api/wiring", "/api/device-ports", "/api/experiments"]));
  readFailure = false; stored.site = graph(generator());
  fireEvent.click(screen.getByRole("button", { name: "重试读取接线图" })); await ready();
  expect(snapshot("测试信号源")).toBeInTheDocument();
});

it("档案与实验列表同时降级仍编辑图内、空白器件和保留未知退役目标", async () => {
  profilesFailure = true; experimentsFailure = true;
  stored.site = graph(generator()); stored.targets = { "retired-unknown": graph(receiver()) };
  await openPage(); await ready();
  expect(screen.getByText(/端口档案读取失败/)).toBeVisible(); expect(screen.getByText(/实验列表读取失败/)).toBeVisible();
  pointerClick(snapshot("测试信号源")); fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "降级仍可改现场" } });
  fireEvent.click(screen.getByRole("button", { name: "添加空白图内器件" }));
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.change(screen.getByLabelText("目标实验"), { target: { value: "retired-unknown" } });
  fireEvent.click(screen.getByRole("button", { name: "实验目标图" }));
  expect(snapshot("测试锁相")).toBeInTheDocument();
  await saveAll(); expect(stored.targets["retired-unknown"].nodes[0].device.id).toBe("receiver");
  expect(stored.site.nodes[0].device.name).toBe("降级仍可改现场"); expect(stored.site.nodes).toHaveLength(2);
});

it("目标实验名称取实验列表标题，列表没有该ID时才回退默认文案", async () => {
  experiments = [{ ...experiment(), title: "列表中的新标题" }];
  await openPage(); await ready();
  const select = screen.getByLabelText("目标实验");
  expect(within(select).getByRole("option", { name: "列表中的新标题" })).toBeInTheDocument();
  expect(within(select).queryByRole("option", { name: "RF 灵敏度（DirectAW）" })).not.toBeInTheDocument();
  cleanup(); requests.length = 0; experiments = [];
  await openPage(); await ready();
  expect(within(screen.getByLabelText("目标实验")).getByRole("option", { name: "RF 灵敏度（DirectAW）" })).toBeInTheDocument();
}, 15000);

it("全局目标视图用实验列表中的要求生成预检，不额外请求实验定义", async () => {
  stored.targets[TARGET] = graph(generator());
  stored.mapping_groups = [{ keys: ["drive"], label: "驱动", device_library_id: null }];
  experiments = [{ ...experiment(), required_devices: ["HF2"], required_mapping_keys: ["read"], wiring_notes: ["从实验列表读取的要点"] }];
  await openPage(); await ready();
  fireEvent.click(screen.getByRole("button", { name: "实验目标图" }));
  const preflight = await screen.findByRole("region", { name: "接线预检清单" });
  await within(preflight).findByText("HF2：图中未找到此型号器件。");
  expect(within(preflight).getByText("用途 read：图中无器件绑定此用途组。")).toBeInTheDocument();
  expect(screen.getByText("接线要点").closest("details")).toHaveTextContent("从实验列表读取的要点");
  expect(requests.some(item => item.path === `/api/experiments/${TARGET}`)).toBe(false);
  expect(puts()).toHaveLength(0);
}, 15000);

it("全局目标未在实验列表时只提示未核对，不清空现有图", async () => {
  stored.targets["retired-unknown"] = graph(receiver());
  await openPage(); await ready();
  fireEvent.click(screen.getByRole("button", { name: "实验目标图" }));
  fireEvent.change(screen.getByLabelText("目标实验"), { target: { value: "retired-unknown" } });
  expect(await screen.findByText("该目标未在当前实验列表，未核对型号/用途要求。")).toBeVisible();
  expect(snapshot("测试锁相")).toBeVisible();
  const preflight = screen.getByRole("region", { name: "接线预检清单" });
  expect(within(preflight).queryByText(/图中未找到此型号器件/)).not.toBeInTheDocument();
  expect(within(preflight).getByText("未发现可自动识别的问题。")).toBeInTheDocument();
  expect(puts()).toHaveLength(0);
}, 15000);

it("已连线端口改方向或光电类型先确认，未连线端口切换不打断", async () => {
  stored.site = graph(generator(), receiver()); stored.site.edges = [edge()];
  await openPage(); await ready(); pointerClick(snapshot("测试信号源"));
  const firstPort = within(screen.getByRole("group", { name: "图内端口 1" }));
  vi.mocked(window.confirm).mockClear();
  fireEvent.change(firstPort.getByLabelText("图内端口光电类型"), { target: { value: "optical" } });
  expect(window.confirm).toHaveBeenCalledTimes(1);
  expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining("会改变线路语义"));
  expect(firstPort.getByLabelText("图内端口光电类型")).toHaveValue("electrical");
  fireEvent.change(firstPort.getByLabelText("图内端口方向"), { target: { value: "in" } });
  expect(window.confirm).toHaveBeenCalledTimes(2);
  expect(firstPort.getByLabelText("图内端口方向")).toHaveValue("out");
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.change(firstPort.getByLabelText("图内端口方向"), { target: { value: "in" } });
  expect(firstPort.getByLabelText("图内端口方向")).toHaveValue("in");
  vi.mocked(window.confirm).mockClear();
  fireEvent.change(screen.getByLabelText("图内记录模式"), { target: { value: "mode-in" } });
  expect(window.confirm).not.toHaveBeenCalled();
  expect(screen.getByLabelText("图内记录模式")).toHaveValue("mode-in");
}, 15000);

it("已连线可切换端口换到不同方向的模式先确认，取消保留当前记录", async () => {
  stored.site = graph(generator(), receiver()); stored.site.edges = [edge("ref-edge", "ref", "signal-v")];
  await openPage(); await ready(); pointerClick(snapshot("测试信号源"));
  const select = screen.getByLabelText("图内记录模式");
  expect(select).toHaveValue("");
  vi.mocked(window.confirm).mockClear();
  fireEvent.change(select, { target: { value: "mode-in" } });
  expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining("会改变方向"));
  expect(select).toHaveValue("");
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.change(select, { target: { value: "mode-in" } });
  expect(select).toHaveValue("mode-in");
}, 15000);

it("档案完整快照保留设备端口模式ID、单图去重并隔离每图模式与名称", async () => {
  await openPage(); await ready(); add("测试信号源");
  expect(within(screen.getByRole("group", { name: "加入器件" })).getByRole("button", { name: /测试信号源/ })).toBeDisabled();
  expect(screen.getByLabelText("图内记录模式")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "现场独立命名" } });
  fireEvent.change(screen.getByLabelText("图内记录模式"), { target: { value: "mode-out" } });
  vi.mocked(window.confirm).mockClear();
  fireEvent.click(screen.getByRole("button", { name: "实验目标图" }));
  expect(window.confirm).not.toHaveBeenCalled();
  expect(screen.queryByRole("group", { name: /^器件快照：/ })).not.toBeInTheDocument(); add("测试信号源");
  expect(screen.getByLabelText("图内记录模式")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("图内记录模式"), { target: { value: "mode-in" } });
  await saveAll();
  expect(stored.site.nodes[0].device).toEqual({ ...generator(), name: "现场独立命名", ports: generator().ports.map(item => item.id === "ref" ? { ...item, selected_mode: "mode-out" } : item) });
  expect(stored.targets[TARGET].nodes[0].device.ports[1]).toMatchObject({ id: "ref", selected_mode: "mode-in", modes: generator().ports[1].modes });
  expect(profiles[0]).toEqual(generator());
  fireEvent.click(screen.getByRole("button", { name: "共享现场图" })); pointerClick(snapshot("现场独立命名"));
  expect(screen.getByLabelText("图内记录模式")).toHaveValue("mode-out");
});

it("重建档案同库来源不同profile ID也禁止重复加入且不替换旧图快照", async () => {
  stored.site = graph({ ...generator(), id: "older-profile", name: "已记录旧档案快照" });
  await openPage(); await ready();
  expect(within(screen.getByRole("group", { name: "加入器件" })).getByRole("button", { name: /测试信号源/ })).toBeDisabled();
  pointerClick(snapshot("已记录旧档案快照")); fireEvent.change(screen.getByLabelText("图内器件型号"), { target: { value: "旧档案仍可手工核对" } });
  await saveAll(); expect(stored.site.nodes).toHaveLength(1); expect(stored.site.nodes[0].device.id).toBe("older-profile");
});

it("空目标仅浏览不进targets，显式新建与现场复制均需明确动作及覆盖确认", async () => {
  stored.site = graph(generator()); stored.targets["other-target"] = graph(receiver());
  await openPage(); await ready(); fireEvent.click(screen.getByRole("button", { name: "实验目标图" }));
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled();
  expect(screen.queryByRole("group", { name: /^器件快照：/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "复制现场到当前实验目标草稿" }));
  expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining("覆盖该目标的全部器件"));
  expect(screen.queryByRole("group", { name: /^器件快照：/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "显式新建空目标" })); await saveAll();
  expect(stored.targets[TARGET]).toEqual(graph()); expect(stored.targets["other-target"]).toEqual(graph(receiver()));
  vi.mocked(window.confirm).mockReturnValue(true); fireEvent.click(screen.getByRole("button", { name: "复制现场到当前实验目标草稿" }));
  pointerClick(snapshot("测试信号源")); fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "目标复制后独立修改" } });
  await saveAll(); expect(stored.site.nodes[0].device.name).toBe("测试信号源"); expect(stored.targets[TARGET].nodes[0].device.id).toBe("generator");
  expect(stored.targets[TARGET].nodes[0].device.name).toBe("目标复制后独立修改");
});

it("真实pointer点击两端才选择类型，允许同口多线与同设备异口，重复反向拒绝、自身口点击取消，线可改标签类型删除", async () => {
  stored.site = graph(generator(), receiver());
  await openPage(); await ready();
  usePorts("测试信号源"); usePorts("测试锁相");
  makeLine("测试信号源 / CH1", "测试锁相 / Signal Input / V", "optical", "人工光路");
  makeLine("测试信号源 / CH1", "测试锁相 / Aux Input / V");
  makeLine("测试信号源 / CH1", "测试信号源 / 10 MHz");
  expect(screen.getAllByRole("button", { name: /^选择连线记录：/ })).toHaveLength(3);
  pointerClick(endpoint("测试锁相 / Signal Input / V")); pointerClick(endpoint("测试信号源 / CH1"));
  expect(screen.getByText(/这对物理端口已有连线/)).toBeVisible();
  pointerClick(endpoint("测试信号源 / CH1")); pointerClick(endpoint("测试信号源 / CH1"));
  expect(screen.queryByText(/不能连接自身同一物理端口/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "取消连线" })).not.toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "待建立连线" })).not.toBeInTheDocument();
  selectEdge(); fireEvent.change(screen.getByLabelText("连线标签"), { target: { value: "改接前记录" } });
  fireEvent.change(screen.getByLabelText("连线光电类型"), { target: { value: "electrical" } });
  await saveAll();
  expect(stored.site.edges).toHaveLength(3); expect(stored.site.edges[0]).toMatchObject({ label: "改接前记录", signal_type: "electrical" });
  expect(new Set(stored.site.edges.map(item => item.id)).size).toBe(3); const ids = stored.site.edges.map(item => item.id);
  selectEdge(1); fireEvent.click(screen.getByRole("button", { name: "删除所选连线" })); await saveAll();
  expect(stored.site.edges.map(item => item.id)).toEqual([ids[0], ids[2]]);
});

it("捕获指针拖线按elementFromPoint落点，后续click不误起线，Escape、pointercancel和释放回起点可撤销", async () => {
  stored.site = graph(generator(), receiver()); await openPage(); await ready();
  usePorts("测试信号源"); usePorts("测试锁相");
  const source = endpoint("测试信号源 / CH1"); const target = endpoint("测试锁相 / Signal Input / V");
  vi.mocked(document.elementFromPoint).mockReturnValue(target);
  fireEvent.pointerDown(source, { pointerId: 9, button: 0, clientX: 100, clientY: 100 });
  expect(capturedOn).toBe(source);
  fireEvent.pointerMove(captureOwner!, { pointerId: 9, clientX: 500, clientY: 160 });
  fireEvent.pointerUp(captureOwner!, { pointerId: 9, clientX: 500, clientY: 160 });
  fireEvent.click(source, { detail: 1 });
  expect(screen.getByRole("group", { name: "待建立连线" })).toBeVisible(); expect(screen.queryByRole("button", { name: "取消连线" })).not.toBeInTheDocument();
  fireEvent.keyDown(window, { key: "Escape" }); expect(screen.queryByRole("group", { name: "待建立连线" })).not.toBeInTheDocument();
  pointerClick(source); expect(screen.getByRole("button", { name: "取消连线" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "取消连线" }));
  fireEvent.pointerDown(source, { pointerId: 10, button: 0, clientX: 100, clientY: 100 });
  fireEvent.pointerMove(captureOwner!, { pointerId: 10, clientX: 500, clientY: 180 });
  fireEvent.pointerCancel(captureOwner!, { pointerId: 10 });
  expect(screen.queryByRole("group", { name: "待建立连线" })).not.toBeInTheDocument(); expect(puts()).toHaveLength(0);
  vi.mocked(document.elementFromPoint).mockReturnValue(null);
  fireEvent.pointerDown(source, { pointerId: 11, button: 0, clientX: 100, clientY: 100 });
  fireEvent.pointerMove(captureOwner!, { pointerId: 11, clientX: 500, clientY: 180 });
  fireEvent.pointerUp(source, { pointerId: 11, clientX: 100, clientY: 100 });
  expect(screen.queryByRole("button", { name: "取消连线" })).not.toBeInTheDocument();
  expect(screen.queryByText(/不能连接自身同一物理端口/)).not.toBeInTheDocument();
  expect(puts()).toHaveLength(0);
});

it("标题拖动吸附20网格且SVG随移动，滚动坐标/方向键/取消保原位与上下限", async () => {
  stored.site = graph(generator(), receiver()); stored.site.edges = [edge()]; await openPage(); await ready();
  const title = snapshot("测试信号源"); const card = screen.getByRole("group", { name: "器件快照：测试信号源" });
  const line = () => screen.getByRole("button", { name: /^选择连线：/ }).getAttribute("d"); const before = line();
  // 模拟滚动时平面与所有子元素同向偏移，不给继承的原型 mock 设置全局固定矩形。
  planeOffset = { x: -200, y: -100 };
  fireEvent.pointerDown(title, { pointerId: 3, button: 0, clientX: 100, clientY: 100 });
  fireEvent.pointerMove(captureOwner!, { pointerId: 3, clientX: 133, clientY: 151 });
  expect(card).toHaveStyle({ left: "100px", top: "120px" }); expect(line()).not.toBe(before);
  fireEvent.pointerUp(captureOwner!, { pointerId: 3, clientX: 133, clientY: 151 }); fireEvent.click(title, { detail: 1 });
  fireEvent.keyDown(title, { key: "ArrowRight" }); expect(card).toHaveStyle({ left: "120px", top: "120px" });
  fireEvent.pointerDown(title, { pointerId: 4, button: 0, clientX: 100, clientY: 100 }); fireEvent.pointerMove(captureOwner!, { pointerId: 4, clientX: 250, clientY: 250 });
  fireEvent.pointerCancel(captureOwner!, { pointerId: 4 }); expect(card).toHaveStyle({ left: "120px", top: "120px" });
  fireEvent.pointerDown(title, { pointerId: 5, button: 0, clientX: 100, clientY: 100 }); fireEvent.pointerMove(captureOwner!, { pointerId: 5, clientX: 250, clientY: 250 });
  fireEvent.keyDown(window, { key: "Escape" }); expect(card).toHaveStyle({ left: "120px", top: "120px" });
  fireEvent.pointerDown(title, { pointerId: 6, button: 0, clientX: 100, clientY: 100 }); fireEvent.pointerMove(captureOwner!, { pointerId: 6, clientX: 99999, clientY: -99999 });
  fireEvent.pointerUp(captureOwner!, { pointerId: 6, clientX: 99999, clientY: -99999 });
  expect(card).toHaveStyle({ left: "20000px", top: "0px" }); await saveAll();
  expect(stored.site.nodes[0]).toMatchObject({ x: 20000, y: 0 }); expect(stored.site.edges[0].id).toBe("existing-edge");
});

it("空白分路器人工增改端口与光电方向，删除端口/器件确认只级联当前图", async () => {
  stored.site = graph(receiver()); stored.targets[TARGET] = graph(receiver()); await openPage(); await ready();
  fireEvent.click(screen.getByRole("button", { name: "添加空白图内器件" }));
  fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "人工分路器" } }); fireEvent.change(screen.getByLabelText("图内器件型号"), { target: { value: "手工确认型号" } });
  fireEvent.click(screen.getByRole("button", { name: "添加图内物理端口" }));
  expect(screen.getByLabelText("图内端口方向")).toHaveValue(""); expect(screen.getByLabelText("图内端口光电类型")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("图内端口名称"), { target: { value: "入射光" } }); fireEvent.change(screen.getByLabelText("图内端口分组"), { target: { value: "光路" } });
  fireEvent.change(screen.getByLabelText("图内端口光电类型"), { target: { value: "optical" } }); expect(within(screen.getByLabelText("图内端口方向")).queryByRole("option", { name: /双向/ })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("图内端口方向"), { target: { value: "in" } });
  expect(screen.queryByRole("button", { name: "连接端口：人工分路器 / 光路 / 入射光" })).not.toBeInTheDocument();
  usePorts("人工分路器"); usePorts("测试锁相");
  makeLine("人工分路器 / 光路 / 入射光", "测试锁相 / Signal Input / V", "optical");
  fireEvent.click(screen.getByRole("button", { name: "删除图内端口及相关连线" })); expect(screen.getByLabelText("图内端口名称")).toHaveValue("入射光");
  vi.mocked(window.confirm).mockReturnValue(true); fireEvent.click(screen.getByRole("button", { name: "删除图内端口及相关连线" }));
  expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining("及 1 条相关连线"));
  expect(screen.queryByRole("button", { name: /^选择连线记录：/ })).not.toBeInTheDocument();
  await saveAll(); const blank = stored.site.nodes[1].device; expect(blank.id).not.toBe("receiver"); expect(blank.ports).toEqual([]);
  pointerClick(snapshot("测试锁相")); fireEvent.click(screen.getByRole("button", { name: "删除当前图器件及相关连线" })); await saveAll();
  expect(stored.site.nodes.map(item => item.device.name)).toEqual(["人工分路器"]); expect(stored.targets[TARGET]).toEqual(graph(receiver())); expect(profiles).toEqual([generator(), receiver()]);
});

it("全图dirty保护离开刷新与切图保留；放弃全部恢复全部图且重新载入取消不请求", async () => {
  stored.site = graph(generator()); stored.targets[TARGET] = graph(receiver()); await openPage(); await ready();
  pointerClick(snapshot("测试信号源")); fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "现场草稿" } });
  fireEvent.click(screen.getByRole("link", { name: "返回仪器控制" })); await waitFor(() => expect(window.confirm).toHaveBeenCalled());
  expect(screen.getByLabelText("图内器件名称")).toHaveValue("现场草稿");
  const unload = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(unload); expect(unload.defaultPrevented).toBe(true);
  const reads = requests.filter(item => item.path === "/api/wiring" && item.method === "GET").length;
  fireEvent.click(screen.getByRole("button", { name: "重新载入" })); expect(requests.filter(item => item.path === "/api/wiring" && item.method === "GET")).toHaveLength(reads);
  vi.mocked(window.confirm).mockClear();
  fireEvent.click(screen.getByRole("button", { name: "实验目标图" })); expect(window.confirm).not.toHaveBeenCalled(); pointerClick(snapshot("测试锁相"));
  fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "目标草稿" } });
  fireEvent.click(screen.getByRole("button", { name: "共享现场图" })); expect(window.confirm).not.toHaveBeenCalled(); expect(snapshot("现场草稿")).toBeInTheDocument();
  vi.mocked(window.confirm).mockReturnValue(true); fireEvent.click(screen.getByRole("button", { name: "放弃全部草稿" })); expect(snapshot("测试信号源")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "实验目标图" })); expect(snapshot("测试锁相")).toBeInTheDocument();
  const cleanUnload = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(cleanUnload); expect(cleanUnload.defaultPrevented).toBe(false);
  fireEvent.click(screen.getByRole("link", { name: "返回仪器控制" })); await screen.findByRole("heading", { name: "仪器控制", level: 1 }); expect(puts()).toHaveLength(0);
});

it.each(["error", "conflict"] as const)("PUT %s失败保全site/targets/base，重试不换base且冲突重载需确认", async failure => {
  stored.site = graph(generator()); stored.targets[TARGET] = graph(receiver()); await openPage(); await ready();
  pointerClick(snapshot("测试信号源")); fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "失败保留现场" } });
  vi.mocked(window.confirm).mockReturnValue(true); fireEvent.click(screen.getByRole("button", { name: "实验目标图" })); pointerClick(snapshot("测试锁相"));
  fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "失败保留目标" } }); saveFailure = failure;
  fireEvent.click(screen.getByRole("button", { name: "保存全部接线图" }));
  await screen.findByText(failure === "conflict" ? /保存冲突（409）/ : /保存失败，全部草稿与基础修订已保留/);
  expect(screen.getByLabelText("图内器件名称")).toHaveValue("失败保留目标"); expect(screen.getByText(/修订 wiring-r1/)).toBeVisible();
  expect(body()).toMatchObject({ base_revision: "wiring-r1", site: { nodes: [{ device: { name: "失败保留现场" } }] }, targets: { [TARGET]: { nodes: [{ device: { name: "失败保留目标" } }] } } });
  if (failure === "conflict") {
    vi.mocked(window.confirm).mockReturnValue(false); const reads = requests.filter(item => item.path === "/api/wiring" && item.method === "GET").length;
    fireEvent.click(screen.getByRole("button", { name: "确认重新载入最新接线图" })); expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining("丢弃全部未保存修改"));
    expect(requests.filter(item => item.path === "/api/wiring" && item.method === "GET")).toHaveLength(reads);
  }
  fireEvent.click(screen.getByRole("button", { name: "保存全部接线图" })); await waitFor(() => expect(puts()).toHaveLength(2));
  await screen.findByText(failure === "conflict" ? /保存冲突（409）/ : /保存失败，全部草稿与基础修订已保留/);
  expect(puts().map(item => (item.body as Body).base_revision)).toEqual(["wiring-r1", "wiring-r1"]);
  saveFailure = ""; await saveAll(); expect(stored.site.nodes[0].device.name).toBe("失败保留现场"); expect(stored.targets[TARGET].nodes[0].device.name).toBe("失败保留目标");
});

it("saving禁全部图编辑、拖动、模式、复制和比较，成功响应不吞新编辑", async () => {
  stored.site = graph(generator(), receiver()); stored.site.edges = [edge()]; stored.targets[TARGET] = graph();
  let finish!: () => void; pendingSave = new Promise<void>(resolve => { finish = resolve; });
  await openPage(); await ready(); pointerClick(snapshot("测试信号源"));
  fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "保存前快照" } }); selectEdge();
  fireEvent.click(screen.getByRole("button", { name: "保存全部接线图" }));
  expect(screen.getByRole("button", { name: "正在保存全部图…" })).toBeDisabled();
  for (const element of [screen.getByLabelText("图内器件名称"), screen.getByLabelText("图内器件型号"), screen.getByLabelText("图内记录模式"),
    screen.getByLabelText("连线标签"), screen.getByLabelText("连线光电类型"), screen.getByLabelText("目标实验"), snapshot("保存前快照"), endpoint("保存前快照 / CH1"),
    screen.getByRole("button", { name: "添加空白图内器件" }), screen.getByRole("button", { name: "添加图内物理端口" }), screen.getByRole("button", { name: "复制现场到当前实验目标草稿" }),
    screen.getByRole("button", { name: "删除当前图器件及相关连线" }), screen.getByRole("button", { name: "重新载入" }), screen.getByRole("button", { name: "放弃全部草稿" }), screen.getByRole("button", { name: "比较现场与当前目标草稿" })]) expect(element).toBeDisabled();
  fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "不应接受" } }); fireEvent.keyDown(snapshot("保存前快照"), { key: "ArrowRight" });
  expect(screen.getByLabelText("图内器件名称")).toHaveValue("保存前快照");
  await act(async () => finish()); await screen.findByText(/全部接线图已保存。/);
  expect(stored.site.nodes[0]).toMatchObject({ x: 60, device: { name: "保存前快照" } }); expect(screen.getByLabelText("图内器件名称")).toBeEnabled();
});

it("compare严格POST完整当前草稿，服务端结果人类端点/模式/警告，后续编辑清结果并隐藏旧诊断", async () => {
  stored.site = graph(generator(), receiver()); stored.site.edges = [edge()]; stored.targets[TARGET] = structuredClone(stored.site);
  stored.diagnostics.site = [{ edge_id: "existing-edge", code: "old", message: "上次保存的服务端提示" }];
  compareResult = { retained: [edge()], removed: [{ ...edge("remove"), label: "待拆" }], added: [{ ...edge("add"), signal_type: "optical", label: "待加" }], mode_changes: [{ device_id: "generator", device_name: "测试信号源", port_id: "ref", port_name: "10 MHz", site_mode: null, target_mode: "参考输出" }], site_warnings: [{ edge_id: "existing-edge", code: "direction", message: "草稿现场方向待确认" }], target_warnings: [{ edge_id: "add", code: "type", message: "草稿目标类型提示" }] };
  const diagnosticsSection = () => screen.getByRole("heading", { level: 2, name: "最近读取 / 保存的服务端提示" }).closest("section")!;
  await openPage(); await ready(); expect(within(diagnosticsSection()).getByText(/上次保存的服务端提示/)).toBeVisible();
  pointerClick(snapshot("测试信号源")); fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "草稿现场信号源" } });
  expect(within(diagnosticsSection()).queryByText(/上次保存的服务端提示/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "比较现场与当前目标草稿" }));
  const result = await screen.findByLabelText("草稿比较结果"); expect(result).toHaveTextContent("草稿现场信号源 / CH1"); expect(result).toHaveTextContent("测试锁相 / Signal Input / V");
  expect(result).toHaveTextContent("待确认 → 参考输出"); expect(result).toHaveTextContent("草稿现场方向待确认"); expect(result).toHaveTextContent("草稿目标类型提示"); expect(result).not.toHaveTextContent("existing-edge");
  expect(compares()).toHaveLength(1); expect(compares()[0].body).toEqual({ site: { ...stored.site, nodes: stored.site.nodes.map(node => node.device.id === "generator" ? { ...node, device: { ...node.device, name: "草稿现场信号源" } } : node) }, target: stored.targets[TARGET] });
  fireEvent.keyDown(snapshot("草稿现场信号源"), { key: "ArrowRight" }); expect(screen.queryByLabelText("草稿比较结果")).not.toBeInTheDocument(); expect(puts()).toHaveLength(0);
});

it("线缆记录可编辑并随画布标签展示，比较结果单列差异且不改变同一根线匹配", async () => {
  stored.site = graph(generator(), receiver()); stored.site.edges = [edge()]; stored.targets[TARGET] = structuredClone(stored.site);
  await openPage(); await ready();
  selectEdge();
  fireEvent.change(screen.getByLabelText("线缆接头形式"), { target: { value: " BNC " } });
  fireEvent.change(screen.getByLabelText("线缆编号"), { target: { value: " C-01 " } });
  fireEvent.change(screen.getByLabelText("线缆备注"), { target: { value: " 测试线 " } });
  expect(document.querySelector('text[data-edge-label="existing-edge"]')).toHaveTextContent("电学 · 线缆 BNC / C-01");
  expect(screen.getAllByLabelText(/^选择连线：.*线缆 BNC \/ C-01，备注 测试线/).length).toBeGreaterThan(0);
  await saveAll();
  expect(body().site.edges[0]).toMatchObject({ id: "existing-edge", connector: "BNC", cable_id: "C-01", note: "测试线" });

  compareResult = {
    retained: [stored.site.edges[0]], removed: [], added: [], mode_changes: [], site_warnings: [], target_warnings: [],
    cable_changes: [{ edge_id: "existing-edge", site_connector: "BNC", target_connector: "SMA", site_cable_id: "C-01", target_cable_id: "C-02", site_note: "测试线", target_note: null }],
  };
  fireEvent.click(screen.getByRole("button", { name: "比较现场与当前目标草稿" }));
  const result = await screen.findByLabelText("草稿比较结果");
  const cables = within(result).getByRole("region", { name: "线缆记录比较" });
  expect(cables).toHaveTextContent("测试信号源 / CH1 ↔ 测试锁相 / Signal Input / V");
  expect(cables).toHaveTextContent("接头 BNC → SMA；编号 C-01 → C-02；备注 测试线 → 未记录");
  expect(compares()).toHaveLength(1);
}, 15000);

it("档案差异徽标可见，确认同步后只改当前图草稿并保留稳定 ID、连线与使用状态", async () => {
  stored.site = graph(generator(), receiver());
  stored.site.nodes[0].device.model = "旧型号";
  stored.site.nodes[0].device.ports[0].connector = "旧接头";
  stored.site.nodes[0].device.ports[0].purpose = "旧用途";
  stored.site.nodes[0].port_usage = { out: "used" };
  stored.site.nodes[1].device.device_library_id = "library-missing";
  stored.site.edges = [edge()];
  profiles[0] = { ...generator(), ports: [...generator().ports, port("monitor", "MON", "out")] };
  await openPage(); await ready();
  expect(await screen.findByText("型号不一致")).toBeVisible();
  expect(screen.getByText("档案中不存在")).toBeVisible();
  expect(screen.getAllByText("端口或模式不一致")).toHaveLength(1);
  pointerClick(snapshot("测试信号源"));
  const panel = screen.getByRole("group", { name: "器件快照编辑" });
  fireEvent.click(within(panel).getByRole("button", { name: "从档案同步本器件" }));
  const preview = within(panel).getByRole("region", { name: "从档案同步差异摘要" });
  expect(preview).toHaveTextContent("型号：旧型号 → 手工型号");
  expect(preview).toHaveTextContent("档案新增端口：MON");
  expect(preview).toHaveTextContent("端口或模式更新：CH1");
  expect(puts()).toHaveLength(0);
  fireEvent.click(within(preview).getByRole("button", { name: "确认同步到当前图草稿" }));
  expect(screen.queryByRole("region", { name: "从档案同步差异摘要" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("图内器件型号")).toHaveValue("手工型号");
  expect(screen.queryByText("型号不一致")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeEnabled();
  expect(puts()).toHaveLength(0);
  await saveAll();
  const savedNode = stored.site.nodes[0];
  expect(savedNode.device.id).toBe("generator");
  expect(savedNode.device.ports.map(item => item.id)).toEqual(expect.arrayContaining(["out", "ref"]));
  const added = savedNode.device.ports.find(item => item.name === "MON")!;
  expect(added.id).not.toBe("monitor");
  expect(added).toMatchObject({ selected_mode: null, modes: [] });
  expect(savedNode.port_usage).toEqual({ out: "used", [added.id]: "unconfirmed" });
  expect(stored.site.edges[0].id).toBe("existing-edge");
  expect(profiles[0].ports).toHaveLength(3);
}, 15000);

it("档案同步遇到模式匹配不唯一时整体拒绝，不部分写入草稿", async () => {
  stored.site = graph(generator());
  stored.site.nodes[0].device.ports[1].modes = [
    { id: "mode-a", name: "参考接收", direction: "in" },
    { id: "mode-b", name: "参考接收", direction: "in" },
  ];
  await openPage(); await ready();
  expect(await screen.findByText("端口或模式不一致")).toBeVisible();
  pointerClick(snapshot("测试信号源"));
  const panel = screen.getByRole("group", { name: "器件快照编辑" });
  fireEvent.click(within(panel).getByRole("button", { name: "从档案同步本器件" }));
  expect(within(panel).getByRole("alert")).toHaveTextContent(/无法从档案同步：.*模式.*未修改任何内容/);
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled();
  expect(puts()).toHaveLength(0);
}, 15000);

it("compare进行中修改草稿会丢弃迟到结果，重载失败保留草稿仅暂停保存", async () => {
  stored.site = graph(generator()); stored.targets[TARGET] = graph(generator());
  let finish!: () => void; pendingCompare = new Promise<void>(resolve => { finish = resolve; });
  await openPage(); await ready(); fireEvent.click(screen.getByRole("button", { name: "比较现场与当前目标草稿" }));
  fireEvent.keyDown(snapshot("测试信号源"), { key: "ArrowRight" }); await act(async () => finish());
  expect(screen.queryByLabelText("草稿比较结果")).not.toBeInTheDocument();
  vi.mocked(window.confirm).mockReturnValue(true); readFailure = true; fireEvent.click(screen.getByRole("button", { name: "重新载入" }));
  await screen.findByText(/重新载入失败，现有全部草稿保留/); expect(screen.getByRole("group", { name: "器件快照：测试信号源" })).toHaveStyle({ left: "80px" });
  expect(screen.getByText(/可继续查看和编辑草稿，但保存已暂停/)).toBeVisible();
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled(); expect(puts()).toHaveLength(0);
  expect(screen.getByRole("button", { name: "添加空白图内器件" })).toBeEnabled();
  pointerClick(snapshot("测试信号源"));
  expect(screen.getByLabelText("图内器件名称")).toBeEnabled();
  expect(screen.getByRole("button", { name: "重新载入" })).toBeEnabled();
});

it("撤销重做按图快照恢复，文本连续输入合并一步，保存后清空历史并清理比较结果", async () => {
  stored.site = graph(generator()); stored.targets[TARGET] = graph(generator());
  await openPage(); await ready();
  fireEvent.click(screen.getByRole("button", { name: "实验目标图" })); pointerClick(snapshot("测试信号源"));
  const name = screen.getByLabelText("图内器件名称");
  const undo = screen.getByRole("button", { name: "撤销" }); const redo = screen.getByRole("button", { name: "重做" });
  expect(undo).toBeDisabled(); expect(redo).toBeDisabled();

  fireEvent.change(name, { target: { value: "目标草稿A" } });
  fireEvent.change(name, { target: { value: "目标草稿AB" } });
  expect(undo).toBeEnabled();
  // 输入框内保留原生文本撤销，不拦截图级历史。
  fireEvent.keyDown(name, { key: "z", ctrlKey: true }); expect(name).toHaveValue("目标草稿AB");
  fireEvent.click(undo); expect(name).toHaveValue("测试信号源"); expect(undo).toBeDisabled();
  expect(redo).toBeEnabled(); fireEvent.click(redo); expect(name).toHaveValue("目标草稿AB");

  fireEvent.click(screen.getByRole("button", { name: "比较现场与当前目标草稿" }));
  await screen.findByLabelText("草稿比较结果");
  fireEvent.keyDown(window, { key: "z", ctrlKey: true });
  expect(screen.queryByLabelText("草稿比较结果")).not.toBeInTheDocument(); expect(name).toHaveValue("测试信号源");

  fireEvent.keyDown(window, { key: "z", ctrlKey: true, shiftKey: true }); expect(name).toHaveValue("目标草稿AB");
  const card = screen.getByRole("group", { name: "器件快照：目标草稿AB" });
  fireEvent.keyDown(snapshot("目标草稿AB"), { key: "ArrowRight" });
  expect(card).toHaveStyle({ left: "80px" }); fireEvent.click(undo); expect(card).toHaveStyle({ left: "60px" });

  const title = snapshot("目标草稿AB");
  fireEvent.pointerDown(title, { pointerId: 7, button: 0, clientX: 100, clientY: 100 });
  fireEvent.pointerMove(captureOwner!, { pointerId: 7, clientX: 133, clientY: 151 });
  fireEvent.pointerUp(captureOwner!, { pointerId: 7, clientX: 133, clientY: 151 }); fireEvent.click(title, { detail: 1 });
  expect(card).toHaveStyle({ left: "100px", top: "120px" }); fireEvent.click(undo); expect(card).toHaveStyle({ left: "60px", top: "60px" });

  await saveAll();
  expect(stored.targets[TARGET].nodes[0].device.name).toBe("目标草稿AB");
  expect(undo).toBeDisabled(); expect(redo).toBeDisabled();
});

it("搜索按器件、端口与连线标签高亮，Enter 定位选中首个结果，Esc 清空且不改数据", async () => {
  stored.site = graph(generator(), receiver()); stored.site.edges = [edge("existing-edge", "out", "signal-v")]; stored.site.edges[0].label = "人工参考线";
  await openPage(); await ready();
  const input = searchBox();
  fireEvent.change(input, { target: { value: "锁相" } });
  const receiverCard = screen.getByRole("group", { name: "器件快照：测试锁相" });
  expect(receiverCard).toHaveClass("wiring-search-match");
  expect(screen.getByRole("group", { name: "器件快照：测试信号源" })).not.toHaveClass("wiring-search-match");
  expect(screen.getByLabelText("搜索结果")).toHaveTextContent("1 个匹配");
  fireEvent.keyDown(input, { key: "Enter" });
  expect(screen.getByLabelText("图内器件名称")).toHaveValue("测试锁相");
  fireEvent.keyDown(input, { key: "Escape" });
  expect(input).toHaveValue("");
  expect(receiverCard).not.toHaveClass("wiring-search-match");
  expect(screen.queryByLabelText("搜索结果")).not.toBeInTheDocument();

  fireEvent.change(input, { target: { value: "Signal Input" } });
  expect(endpoint("测试锁相 / Signal Input / V")).toHaveClass("wiring-port-search-match");
  expect(receiverCard).toHaveClass("wiring-search-match");
  fireEvent.keyDown(input, { key: "Enter" });
  expect(screen.getByLabelText("图内器件名称")).toHaveValue("测试锁相");

  fireEvent.change(input, { target: { value: "人工参考线" } });
  expect(plane().querySelector('[data-edge-id="existing-edge"]')).toHaveClass("wiring-search-match");
  fireEvent.keyDown(input, { key: "Enter" });
  expect(screen.getByLabelText("连线标签")).toHaveValue("人工参考线");
  fireEvent.keyDown(input, { key: "Escape" });
  expect(input).toHaveValue("");
  expect(plane().querySelector('[data-edge-id="existing-edge"]')).not.toHaveClass("wiring-search-match");
  expect(puts()).toHaveLength(0);
});

it("新增器件从视口左上扫描现有盒子的网格空位，不重叠且不自动保存", async () => {
  stored.site = graph(generator(), receiver()); await openPage(); await ready();
  const addBlank = () => fireEvent.click(screen.getByRole("button", { name: "添加空白图内器件" }));
  const left = (card: HTMLElement) => Number.parseFloat(card.style.left);
  const top = (card: HTMLElement) => Number.parseFloat(card.style.top);
  addBlank();
  await waitFor(() => expect(screen.getAllByRole("group", { name: "器件快照：新器件" })).toHaveLength(1));
  const first = screen.getByRole("group", { name: "器件快照：新器件" });
  expect(first).toHaveStyle({ left: "760px", top: "0px" });
  addBlank();
  await waitFor(() => expect(screen.getAllByRole("group", { name: "器件快照：新器件" })).toHaveLength(2));
  const cards = screen.getAllByRole("group", { name: /^(器件快照：|新器件)/ });
  const positions = cards.map(card => ({ left: left(card), top: top(card), right: left(card) + 248, bottom: top(card) + 180 }));
  for (let index = 0; index < positions.length; index++) for (let other = index + 1; other < positions.length; other++) {
    const a = positions[index]; const b = positions[other];
    expect(a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom).toBe(false);
  }
  expect(positions.map(item => item.left)).toEqual(expect.arrayContaining([760, 1040]));
  expect(puts()).toHaveLength(0);
});
