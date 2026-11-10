import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "../App";
import { NavigationGuard } from "../components/NavigationGuard";
import type { ExperimentDefinition } from "../types/api";
import type { components } from "../types/openapi";
import type { WiringComparison, WiringDocument, WiringGraph } from "../components/wiring/wiringTypes";

type Document = WiringDocument;
type Graph = WiringGraph;
type Body = components["schemas"]["WiringBody"];
type CompareBody = components["schemas"]["WiringCompareBody"];
type Comparison = components["schemas"]["WiringComparison"];
const TARGET = "internal-signals-test";
const graph = (): Graph => ({ nodes: [
  { device: { id: "lockin", name: "内部锁相", model: "MFLI", device_library_id: null, ports: [
    { id: "v", name: "V", direction: "in", group: "", signal_type: "electrical", connector: "BNC", purpose: "", modes: [], selected_mode: null },
  ] }, x: 60, y: 60, follow_mapping: [], port_usage: { v: "used" }, internal_ports: [
    { id: "demod-y", name: "Demod0 Y", direction: "out" }, { id: "aux-source", name: "AuxOut2 信号源", direction: "in" },
    { id: "aux-value", name: "AuxOut2 内部值", direction: "out" }, { id: "rf-input", name: "RF 解调器 1 内部回采", direction: "in" },
  ] },
  { device: { id: "noise", name: "稳功率器", model: "Noise Eater", device_library_id: null, ports: [
    { id: "power", name: "功率设定输入", direction: "in", group: "", signal_type: "electrical", connector: "", purpose: "", modes: [], selected_mode: null },
  ] }, x: 480, y: 60, follow_mapping: [], settings: [
    { id: "range", name: "高低档", value: "high", options: [{ value: "high", label: "高档" }, { value: "low", label: "低档" }] },
    { id: "step", name: "子档", value: "1", options: [1, 2, 3, 4].map(value => ({ value: String(value), label: String(value) })) },
  ], internal_ports: [{ id: "other-in", name: "其他内部输入", direction: "in" }] },
], edges: [] });
const route = (): Graph["edges"][number] => ({ id: "internal-kept", source: { device_id: "lockin", port_id: "demod-y" }, target: { device_id: "lockin", port_id: "aux-source" }, signal_type: "internal", label: "内部信号源" });
const definition: ExperimentDefinition = { id: TARGET, title: "内部信号测试实验", description: "无硬件路由验证", archived: false, category: "test", category_label: "测试", family: "test", variant: "test", required_devices: [], required_mapping_keys: [], execution_mode: "typed_workflow", acquisition_program: "test", analysis_program: null, wiring_notes: [], safety_notes: [], supports_cancel: true, can_analyze: false };
const response = (value: unknown, status = 200, code?: string) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json", ...(code ? { "X-Error-Code": code } : {}) } });
let stored: Document;
let comparison: Comparison;
let saveFailure: "error" | "conflict" | undefined;
let saveWait: Promise<void> | undefined;
const requests: Array<{ path: string; method: string; body?: Body | CompareBody }> = [];
const forbidden: string[] = [];
beforeEach(() => {
  stored = { schema_version: 1, revision: "internal-r1", site: graph(), targets: { [TARGET]: graph(), retired: graph() }, diagnostics: { site: [], targets: {} } };
  comparison = { retained: [], removed: [], added: [], internal_retained: [], internal_removed: [], internal_added: [], setting_changes: [], mode_changes: [], usage_changes: [], site_warnings: [], target_warnings: [] };
  requests.length = 0; forbidden.length = 0; saveFailure = undefined; saveWait = undefined; localStorage.clear();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const node = this.closest<HTMLElement>(".wiring-node");
    const translation = node?.style.transform.match(/translate\(([-\d.]+)px, ([-\d.]+)px\)/);
    const x = (Number.parseInt(node?.style.left ?? "0") || 0) + Number(translation?.[1] ?? 0);
    const y = (Number.parseInt(node?.style.top ?? "0") || 0) + Number(translation?.[2] ?? 0);
    const isPort = this.matches("[data-port-id]");
    const internalIndex = node ? Array.from(node.querySelectorAll('[data-port-kind="internal"]')).indexOf(this) : -1;
    const left = x + (isPort ? this.getAttribute("data-side") === "right" ? 140 : 8 : 0);
    const top = y + (isPort ? internalIndex >= 0 ? 100 + internalIndex * 24 : 50 : 0);
    const width = isPort ? 100 : node ? 248 : 2600;
    const height = isPort ? 20 : node ? 320 : 1600;
    if (this.matches("text[data-edge-label]")) return { x: 0, y: 0, left: 0, top: 0, right: 220, bottom: 14, width: 220, height: 14, toJSON: () => ({}) };
    return { x: left, y: top, left, top, right: left + width, bottom: top + height, width, height, toJSON: () => ({}) };
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input); const method = options?.method ?? "GET"; const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    requests.push({ path, method, body });
    if (path === "/api/health" && method === "GET") return response({ status: "ok", hardware_busy: false });
    if (path === "/api/jobs" && method === "GET") return response([]);
    if (path === "/api/experiments" && method === "GET") return response([definition]);
    if (path === `/api/experiments/${TARGET}` && method === "GET") return response(definition);
    if (path === `/api/experiments/${TARGET}/schema` && method === "GET") return response({ schema_version: 2, fields: [], parameter_layout_saved: true, parameter_layout: { basic: [], advanced: [] } });
    if (path === `/api/experiments/${TARGET}/derive` && method === "POST") return response({ ok: true, values: {} });
    if (path === "/api/device-ports" && method === "GET") return response({ schema_version: 1, revision: "ports-r1", devices: [], presets: [] });
    if (path === "/api/wiring" && method === "GET") return response(stored);
    if (path === "/api/wiring" && method === "PUT") {
      await saveWait;
      if (saveFailure) return response({ detail: saveFailure === "conflict" ? "修订冲突" : "保存故障" }, saveFailure === "conflict" ? 409 : 500, saveFailure === "conflict" ? "revision_conflict" : undefined);
      stored = { ...stored, revision: "internal-r2", site: body.site, targets: body.targets }; return response(stored);
    }
    if (path === "/api/wiring/compare" && method === "POST") return response(comparison);
    forbidden.push(`${method} ${path}`); throw new Error(`禁止的未模拟请求：${method} ${path}`);
  }));
});
afterEach(() => {
  cleanup(); expect(forbidden).toEqual([]);
  expect(requests.filter(item => item.method !== "GET").every(item => item.path === "/api/wiring" && item.method === "PUT" || item.path === "/api/wiring/compare" && item.method === "POST" || item.path === `/api/experiments/${TARGET}/derive` && item.method === "POST")).toBe(true);
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});
async function open(embedded = false) {
  const router = createMemoryRouter([{ path: "*", element: <NavigationGuard><App /></NavigationGuard> }], { initialEntries: [embedded ? `/experiments/${TARGET}` : "/instruments/wiring"] });
  render(<RouterProvider router={router} />);
  await screen.findByRole("heading", { level: 1, name: embedded ? definition.title : "实验接线图" }, { timeout: 15000 });
  if (embedded) fireEvent.click(screen.getByRole("tab", { name: "目标接线图" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "添加空白图内器件" })).toBeEnabled());
  if (!embedded) fireEvent.change(screen.getByLabelText("目标实验"), { target: { value: TARGET } });
}
const internal = (name: string, device = "内部锁相") => screen.getByRole("button", { name: `连接内部端口：${device} / ${name}` });
const setting = (name = "高低档") => screen.getByRole("combobox", { name: `图内设置：稳功率器 / ${name}` });
const puts = () => requests.filter(item => item.path === "/api/wiring" && item.method === "PUT");
const lastBody = () => puts().at(-1)!.body as Body;
const unloadBlocked = () => { const event = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; };
async function save(embedded = false) {
  fireEvent.click(screen.getByRole("button", { name: embedded ? "保存当前实验目标" : "保存全部接线图" }));
  await screen.findByText(embedded ? "当前实验目标接线图已保存。" : "全部接线图已保存。");
  await act(async () => {});
}
function pathPoints(d: string | null) {
  const values = (d ?? "").match(/-?\d+(?:\.\d+)?/g)?.map(Number) ?? [];
  return Array.from({ length: Math.floor(values.length / 2) }, (_, index) => ({ x: values[index * 2], y: values[index * 2 + 1] }));
}
function connectInternal() {
  fireEvent.click(internal("Demod0 Y")); fireEvent.click(internal("AuxOut2 信号源"));
  expect(screen.queryByLabelText("新连线光电类型")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "记录连线" }));
}

it("旧图缺内部和设置字段正常读写，不补空字段或使草稿变脏", async () => {
  for (const value of [stored.site, ...Object.values(stored.targets)]) for (const node of value.nodes) { delete node.internal_ports; delete node.settings; }
  await open();
  expect(screen.queryByRole("button", { name: /^连接内部端口/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: /^图内设置：/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled(); expect(unloadBlocked()).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "移动器件：内部锁相" }));
  fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "只改名称" } }); await save();
  expect(lastBody().site.nodes[0]).not.toHaveProperty("internal_ports"); expect(lastBody().site.nodes[0]).not.toHaveProperty("settings");
  expect(lastBody().targets[TARGET].nodes[1]).not.toHaveProperty("settings");
}, 15000);

it("独立内部区按输入左输出右呈现，建立同机定向路线且不增加物理口或使用字典", async () => {
  await open(); const targets = structuredClone(stored.targets); const original = structuredClone(stored.site.nodes[0]);
  expect(internal("Demod0 Y")).toHaveAttribute("data-side", "right"); expect(internal("AuxOut2 信号源")).toHaveAttribute("data-side", "left");
  expect(internal("Demod0 Y")).toHaveAttribute("data-port-kind", "internal");
  expect(screen.queryByRole("button", { name: "使用状态：内部锁相 / Demod0 Y" })).not.toBeInTheDocument();
  connectInternal();
  const wire = screen.getByRole("button", { name: /^选择连线：.*内部信号（非线缆）/ });
  expect(wire.closest("g")).toHaveClass("signal-internal"); expect(wire.getAttribute("d")).toMatch(/^M .+ L /);
  await save();
  expect(lastBody().site.edges).toEqual([expect.objectContaining({ source: { device_id: "lockin", port_id: "demod-y" }, target: { device_id: "lockin", port_id: "aux-source" }, signal_type: "internal" })]);
  expect(lastBody().site.nodes[0].device.ports).toEqual(original.device.ports); expect(lastBody().site.nodes[0].port_usage).toEqual(original.port_usage);
  expect(lastBody().site.nodes[0].internal_ports).toEqual(original.internal_ports); expect(lastBody().targets).toEqual(targets);
}, 15000);

it("内部路线拒绝混接物理口、跨器件和反方向，不暗自互换端点", async () => {
  await open();
  for (const [source, target, error] of [
    [() => internal("Demod0 Y"), () => screen.getByRole("button", { name: "连接端口：内部锁相 / V" }), /不能跨器件或混接物理端口/],
    [() => internal("Demod0 Y"), () => internal("其他内部输入", "稳功率器"), /不能跨器件或混接物理端口/],
    [() => internal("AuxOut2 信号源"), () => internal("Demod0 Y"), /方向不会自动交换/],
  ] as const) {
    fireEvent.click(source()); fireEvent.click(target()); await screen.findByText(error);
    expect(screen.queryByRole("button", { name: "记录连线" })).not.toBeInTheDocument();
  }
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled(); expect(unloadBlocked()).toBe(false); expect(puts()).toHaveLength(0);
}, 15000);

it("内部路由编辑不能变成外线，删除内部端口须确认且仅删除相关内部路线", async () => {
  stored.site.edges = [route(), { id: "external-kept", source: { device_id: "lockin", port_id: "v" }, target: { device_id: "noise", port_id: "power" }, signal_type: "electrical", label: "保留外线" }];
  await open(); const targets = structuredClone(stored.targets);
  fireEvent.click(screen.getByRole("button", { name: "选择连线记录：内部锁相 / 内部 / Demod0 Y ↔ 内部锁相 / 内部 / AuxOut2 信号源" }));
  expect(screen.queryByLabelText("连线光电类型")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "移动器件：内部锁相" }));
  const editor = screen.getByRole("group", { name: "图内内部端口 1" });
  expect(within(editor).getByLabelText("内部信号方向")).toBeDisabled();
  fireEvent.click(within(editor).getByRole("button", { name: "删除内部端口及相关路由" })); expect(internal("Demod0 Y")).toBeVisible();
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.click(within(editor).getByRole("button", { name: "删除内部端口及相关路由" })); await save();
  expect(lastBody().site.edges.map(edge => edge.id)).toEqual(["external-kept"]);
  expect(lastBody().site.nodes[0].internal_ports?.map(port => port.id)).toEqual(["aux-source", "aux-value", "rf-input"]);
  expect(lastBody().site.nodes[0].device.ports.map(port => port.id)).toEqual(["v"]); expect(lastBody().targets).toEqual(targets);
}, 15000);

it("内部线只在器件盒内走线，外部线仍绕过盒，内部线可点选并删除", async () => {
  stored.site.edges = [route(), { id: "external-kept", source: { device_id: "lockin", port_id: "v" }, target: { device_id: "noise", port_id: "power" }, signal_type: "electrical", label: "保留外线" }];
  stored.site.nodes[1].port_usage = { power: "used" };
  await open();
  const card = screen.getByRole("group", { name: "器件快照：内部锁相" }).getBoundingClientRect();
  const internalPath = screen.getByRole("button", { name: /^选择连线：.*内部信号（非线缆）/ });
  expect(internalPath.closest("g")).toHaveClass("signal-internal");
  expect(internalPath.closest("[data-internal-route='true']")).not.toBeNull();
  const internalPoints = pathPoints(internalPath.getAttribute("d"));
  expect(internalPoints.length).toBeGreaterThan(1);
  for (const point of internalPoints) {
    expect(point.x).toBeGreaterThan(0); expect(point.x).toBeLessThan(card.width);
    expect(point.y).toBeGreaterThan(0); expect(point.y).toBeLessThan(card.height);
  }
  const input = internal("AuxOut2 信号源").getBoundingClientRect(); const output = internal("Demod0 Y").getBoundingClientRect();
  for (const point of internalPoints) {
    expect(point.x).toBeGreaterThanOrEqual(input.right - card.left - .01);
    expect(point.x).toBeLessThanOrEqual(output.left - card.left + .01);
  }
  const externalPath = screen.getByRole("button", { name: /^选择连线：.*电学/ });
  const externalPoints = pathPoints(externalPath.getAttribute("d"));
  expect(externalPoints.length).toBeGreaterThan(2);
  // 两端锚点落在端口按钮内，属已有物理线语义；绕行段本身必须避开器件盒。
  expect(externalPoints.slice(1, -1).every(point => point.x <= card.left || point.x >= card.right || point.y <= card.top || point.y >= card.bottom)).toBe(true);
  fireEvent.click(internalPath);
  expect(screen.getByLabelText("高亮连线路径")).toHaveTextContent(/内部信号（非线缆）/);
  expect(screen.getByLabelText("连线标签")).toHaveValue("内部信号源");
  fireEvent.click(screen.getByRole("button", { name: "删除所选连线" }));
  expect(screen.getAllByRole("button", { name: /^选择连线记录：/ })).toHaveLength(1);
  await save();
  expect(lastBody().site.edges.map(edge => edge.id)).toEqual(["external-kept"]);
}, 15000);

it("Noise Eater 档位仅改图内选值，可清为未记录，物理输入仍只有一个", async () => {
  await open(); const original = structuredClone(stored.site.nodes[1]); const targets = structuredClone(stored.targets);
  expect(setting()).toHaveValue("high"); expect(setting("子档")).toHaveValue("1"); expect(puts()).toHaveLength(0);
  fireEvent.change(setting(), { target: { value: "low" } }); fireEvent.change(setting("子档"), { target: { value: "4" } }); await save();
  expect(lastBody().site.nodes[1].settings?.map(entry => entry.value)).toEqual(["low", "4"]);
  expect(lastBody().site.nodes[1].device.ports).toEqual(original.device.ports); expect(lastBody().site.nodes[1].device.ports).toHaveLength(1); expect(lastBody().targets).toEqual(targets);
  fireEvent.change(setting(), { target: { value: "" } }); await save(); expect(lastBody().site.nodes[1].settings?.[0].value).toBeNull();
}, 15000);

it("编辑器可添加内部端口及设置，空名和重复选项阻止保存，选值不会添加物理端口", async () => {
  await open(); fireEvent.click(screen.getByRole("button", { name: "移动器件：稳功率器" }));
  fireEvent.click(screen.getByRole("button", { name: "添加图内内部端口" }));
  const port = screen.getByRole("group", { name: "图内内部端口 2" });
  fireEvent.change(within(port).getByLabelText("内部端口名称"), { target: { value: "" } }); expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled();
  fireEvent.change(within(port).getByLabelText("内部端口名称"), { target: { value: "图内输出" } });
  fireEvent.click(screen.getByRole("button", { name: "添加图内设置" }));
  const editor = screen.getByRole("group", { name: "器件图内设置 3" });
  fireEvent.change(within(editor).getByLabelText("设置名称"), { target: { value: "新设置" } });
  fireEvent.click(within(editor).getByRole("button", { name: "添加设置选项" }));
  const values = within(editor).getAllByLabelText("选项值");
  fireEvent.change(values[1], { target: { value: "1" } }); expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled();
  fireEvent.change(values[1], { target: { value: "2" } });
  fireEvent.change(within(editor).getByLabelText("当前记录值"), { target: { value: "2" } }); await save();
  expect(lastBody().site.nodes[1].internal_ports?.at(-1)).toMatchObject({ name: "图内输出", direction: "out" });
  expect(lastBody().site.nodes[1].settings?.at(-1)).toMatchObject({ name: "新设置", value: "2" }); expect(lastBody().site.nodes[1].device.ports).toHaveLength(1);
}, 15000);

it("内部信号和设置差异独立于外线，编辑设置会清除过期比较", async () => {
  comparison.internal_added = [route()]; comparison.setting_changes = [{ device_id: "noise", device_name: "稳功率器", setting_id: "range", setting_name: "高低档", site_value: "high", target_value: "low" }];
  await open(); fireEvent.click(screen.getByRole("button", { name: "比较现场与当前目标草稿" }));
  const result = await screen.findByLabelText("草稿比较结果");
  expect(within(result).getByRole("heading", { name: "新增 0" })).toBeVisible();
  expect(within(result).getByRole("heading", { name: "内部新增 1" })).toBeVisible();
  expect(within(screen.getByLabelText("内部信号比较")).getByText(/Demod0 Y.*→.*AuxOut2 信号源/)).toBeVisible();
  expect(within(screen.getByLabelText("图内设置比较")).getByText("稳功率器 / 高低档：high → low")).toBeVisible();
  const body = requests.find(item => item.path === "/api/wiring/compare")!.body as CompareBody;
  expect(body.site.nodes[0].internal_ports).toEqual(stored.site.nodes[0].internal_ports); expect(body.target.nodes[1].settings).toEqual(stored.targets[TARGET].nodes[1].settings);
  fireEvent.change(setting(), { target: { value: "low" } }); expect(screen.queryByLabelText("草稿比较结果")).not.toBeInTheDocument(); expect(puts()).toHaveLength(0);
}, 15000);

it.each(["error", "conflict"] as const)("内部路由和设置保存 %s 保草稿和修订，保存中禁改且重新载入先确认", async failure => {
  await open(); const original = structuredClone(stored); connectInternal(); fireEvent.change(setting(), { target: { value: "low" } });
  let release!: () => void; saveWait = new Promise<void>(done => { release = done; }); saveFailure = failure;
  fireEvent.click(screen.getByRole("button", { name: "保存全部接线图" })); expect(setting()).toBeDisabled(); expect(internal("Demod0 Y")).toBeDisabled();
  fireEvent.change(setting(), { target: { value: "high" } });
  await act(async () => release()); await screen.findByText(failure === "conflict" ? /保存冲突（409），全部草稿与基础修订已保留/ : /保存失败，全部草稿与基础修订已保留/);
  expect(setting()).toHaveValue("low"); expect(screen.getByText(/修订 internal-r1/)).toBeVisible(); expect(unloadBlocked()).toBe(true);
  expect(lastBody().site.edges).toHaveLength(1); expect(lastBody().site.nodes[1].settings?.[0].value).toBe("low"); expect(stored).toEqual(original);
  fireEvent.click(screen.getByRole("button", { name: "重新载入" })); expect(requests.filter(item => item.path === "/api/wiring" && item.method === "GET")).toHaveLength(1);
}, 15000);

it("固定实验目标的内部和设置草稿跨 Tab 保留，PUT 只替换当前目标", async () => {
  await open(true); const site = structuredClone(stored.site); const retired = structuredClone(stored.targets.retired);
  connectInternal(); fireEvent.change(setting("子档"), { target: { value: "4" } });
  fireEvent.click(screen.getByRole("tab", { name: "运行" })); fireEvent.click(screen.getByRole("tab", { name: "目标接线图" }));
  expect(setting("子档")).toHaveValue("4"); expect(screen.getAllByRole("button", { name: /^选择连线：/ })).toHaveLength(1); expect(unloadBlocked()).toBe(true);
  await save(true);
  expect(lastBody().targets[TARGET].edges[0].signal_type).toBe("internal"); expect(lastBody().targets[TARGET].nodes[1].settings?.[1].value).toBe("4");
  expect(lastBody().site).toEqual(site); expect(lastBody().targets.retired).toEqual(retired); expect(unloadBlocked()).toBe(false);
}, 15000);
