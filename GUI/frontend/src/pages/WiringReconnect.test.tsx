import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "../App";
import { NavigationGuard } from "../components/NavigationGuard";
import type { ExperimentDefinition } from "../types/api";
import type { components } from "../types/openapi";
import type { WiringDocument, WiringGraph } from "../components/wiring/wiringTypes";

type Document = WiringDocument;
type Graph = WiringGraph;
type Body = components["schemas"]["WiringBody"];
type CompareBody = components["schemas"]["WiringCompareBody"];
type Port = components["schemas"]["PhysicalPort"];
type InternalPort = components["schemas"]["WiringInternalPort"];
const TARGET = "reconnect-test";
const port = (id: string, name: string, direction: Port["direction"]): Port => ({ id, name, group: "", signal_type: "electrical", connector: "", purpose: "", direction, modes: [], selected_mode: null });
const node = (id: string, name: string, ports: Port[], internal: InternalPort[], x: number): Graph["nodes"][number] => ({
  device: { id, name, model: "手工型号", device_library_id: null, ports },
  x, y: 60, port_usage: Object.fromEntries(ports.map(item => [item.id, "used"])),
  ...(internal.length ? { internal_ports: internal } : {}),
});
const graph = (): Graph => ({ nodes: [
  node("source", "改接信号源", [port("ch1", "CH1", "out"), port("ch2", "CH2", "out")], [], 60),
  node("sink", "改接接收机", [port("signal", "Signal", "in"), port("aux", "Aux", "in")], [], 480),
  node("lockin", "内部锁相", [port("v", "V", "in")], [
    { id: "demod", name: "Demod Y", direction: "out" },
    { id: "aux-in", name: "Aux 输入", direction: "in" },
    { id: "aux-in-2", name: "Aux 输入 2", direction: "in" },
  ], 900),
  node("other", "另一器件", [], [{ id: "other-out", name: "外部输出", direction: "out" }], 1320),
], edges: [
  { id: "edge-keep", source: { device_id: "source", port_id: "ch1" }, target: { device_id: "sink", port_id: "signal" }, signal_type: "electrical", label: "保留标签" },
  { id: "edge-internal", source: { device_id: "lockin", port_id: "demod" }, target: { device_id: "lockin", port_id: "aux-in" }, signal_type: "internal", label: "内部保留" },
] });
const definition: ExperimentDefinition = { id: TARGET, title: "改接测试实验", description: "无硬件改接验证", archived: false, category: "test", category_label: "测试", family: "test", variant: "test", required_devices: [], required_mapping_keys: [], execution_mode: "typed_workflow", acquisition_program: "test", analysis_program: null, wiring_notes: [], safety_notes: [], supports_cancel: true, can_analyze: false };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
let stored: Document;
const requests: Array<{ path: string; method: string; body?: Body | CompareBody }> = [];
const forbidden: string[] = [];
let captureOwner: Element | null = null;
let capturedOn: Element | null = null;

beforeEach(() => {
  stored = { schema_version: 1, revision: "reconnect-r1", site: graph(), targets: {}, diagnostics: { site: [], targets: {} } };
  requests.length = 0; forbidden.length = 0; captureOwner = null; capturedOn = null; localStorage.clear();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  // jsdom 没有 PointerEvent / 捕获；保留真实 pointerdown→pointermove→pointerup→click 序列及捕获目标语义。
  class TestPointerEvent extends MouseEvent {
    pointerId: number;
    constructor(type: string, options: PointerEventInit = {}) { super(type, options); this.pointerId = options.pointerId ?? 1; }
  }
  vi.stubGlobal("PointerEvent", TestPointerEvent);
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const card = this.closest<HTMLElement>(".wiring-node");
    const translation = card?.style.transform.match(/translate\(([-\d.]+)px, ([-\d.]+)px\)/);
    const x = (Number.parseInt(card?.style.left ?? "0") || 0) + (Number(translation?.[1]) || 0);
    const y = (Number.parseInt(card?.style.top ?? "0") || 0) + (Number(translation?.[2]) || 0);
    if (this.matches("text[data-edge-label]")) return { x: 0, y: 0, left: 0, top: 0, right: 160, bottom: 14, width: 160, height: 14, toJSON: () => ({}) };
    const isPort = this.matches("[data-port-id]");
    const internalIndex = card ? Array.from(card.querySelectorAll('[data-port-kind="internal"]')).indexOf(this) : -1;
    const index = Array.from(this.parentElement?.parentElement?.querySelectorAll("[data-port-id]") ?? []).indexOf(this);
    const left = x + (isPort ? this.getAttribute("data-side") === "right" ? 140 : 8 : 0);
    const top = y + (isPort ? internalIndex >= 0 ? 100 + internalIndex * 24 : 50 + index * 30 : 0);
    const width = isPort ? 100 : card ? 248 : 2600;
    const height = isPort ? 20 : card ? 320 : 1600;
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
    if (path === "/api/jobs" && method === "GET") return response([]);
    if (path === "/api/control-targets" && method === "GET") return response({ device_library_revision: "library-r1", physical_mapping_revision: "mapping-r1", targets: [] });
    if (path === "/api/device-library" && method === "GET") return response({ schema_version: 1, revision: "library-r1", network_resources: [], devices: {} });
    if (path === "/api/device-ports" && method === "GET") return response({ schema_version: 1, revision: "ports-r1", devices: [], presets: [] });
    if (path === "/api/experiments" && method === "GET") return response([definition]);
    if (path === "/api/wiring" && method === "GET") return response(stored);
    if (path === "/api/wiring" && method === "PUT") {
      stored = { ...stored, revision: "reconnect-r2", site: body.site, targets: body.targets };
      return response(stored);
    }
    if (path === "/api/wiring/compare" && method === "POST") return response({ retained: [], removed: [], added: [], mode_changes: [], site_warnings: [], target_warnings: [] });
    forbidden.push(`${method} ${path}`); throw new Error(`禁止的未模拟请求：${method} ${path}`);
  }));
});
afterEach(() => {
  cleanup(); expect(forbidden).toEqual([]);
  expect(requests.every(item => item.method === "GET" || item.method === "PUT" && item.path === "/api/wiring" || item.method === "POST" && item.path === "/api/wiring/compare")).toBe(true);
  delete (Element.prototype as Partial<Element>).setPointerCapture; delete (Element.prototype as Partial<Element>).hasPointerCapture; delete (Element.prototype as Partial<Element>).releasePointerCapture;
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});
async function openPage() {
  const router = createMemoryRouter([{ path: "*", element: <NavigationGuard><App /></NavigationGuard> }], { initialEntries: ["/instruments/wiring"] });
  render(<RouterProvider router={router} />);
  await screen.findByRole("heading", { name: "实验接线图", level: 1 }, { timeout: 15000 });
  await waitFor(() => expect(screen.getByRole("button", { name: "添加空白图内器件" })).toBeEnabled());
}
const puts = () => requests.filter(item => item.method === "PUT");
const endpoint = (name: string) => screen.getByRole("button", { name: `连接端口：${name}` });
const internal = (name: string, device = "内部锁相") => screen.getByRole("button", { name: `连接内部端口：${device} / ${name}` });
const snapshot = (name: string) => screen.getByRole("button", { name: `移动器件：${name}` });
const record = (text: string) => screen.getAllByRole("button", { name: /^选择连线记录：/ }).find(button => button.textContent?.includes(text))!;
function selectEdge(text: string) { fireEvent.click(record(text)); }
async function save() {
  fireEvent.click(screen.getByRole("button", { name: "保存全部接线图" }));
  await screen.findByText("全部接线图已保存。");
  await act(async () => {});
}
function pointerClick(element: Element, pointerId = 1) {
  capturedOn = null;
  fireEvent.pointerDown(element, { pointerId, button: 0, clientX: 100, clientY: 100 });
  const clicked = capturedOn ?? element;
  fireEvent.pointerUp(captureOwner ?? element, { pointerId, button: 0, clientX: 100, clientY: 100 });
  fireEvent.click(clicked, { detail: 1 });
}
function drag(handle: Element, target: Element, pointerId: number, clientX = 600, clientY = 320) {
  vi.mocked(document.elementFromPoint).mockReturnValue(target);
  fireEvent.pointerDown(handle, { pointerId, button: 0, clientX: 100, clientY: 100 });
  expect(capturedOn).toBe(handle);
  fireEvent.pointerMove(captureOwner!, { pointerId, clientX, clientY });
  fireEvent.pointerUp(captureOwner!, { pointerId, clientX, clientY });
  fireEvent.click(handle, { detail: 1 });
}

it("选中连线显示两端手柄，拖动起点实时预览并落点改接，保留ID、标签与光电类型", async () => {
  await openPage();
  expect(screen.queryByRole("button", { name: /^拖动改接/ })).not.toBeInTheDocument();
  selectEdge("CH1");
  expect(screen.getByRole("button", { name: "拖动改接起点：改接信号源 / CH1 ↔ 改接接收机 / Signal（电学 · 保留标签）" })).toBeVisible();
  expect(screen.getByRole("button", { name: "拖动改接终点：改接信号源 / CH1 ↔ 改接接收机 / Signal（电学 · 保留标签）" })).toBeVisible();
  const handle = screen.getByRole("button", { name: /^拖动改接起点：/ });
  vi.mocked(document.elementFromPoint).mockReturnValue(endpoint("改接信号源 / CH2"));
  fireEvent.pointerDown(handle, { pointerId: 7, button: 0, clientX: 100, clientY: 100 });
  expect(capturedOn).toBe(handle);
  fireEvent.pointerMove(captureOwner!, { pointerId: 7, clientX: 400, clientY: 500 });
  expect(document.querySelector(".wiring-reconnect-preview")?.getAttribute("d")).toMatch(/^M /);
  fireEvent.pointerUp(captureOwner!, { pointerId: 7, clientX: 400, clientY: 500 });
  fireEvent.click(handle, { detail: 1 });
  expect(document.querySelector(".wiring-reconnect-preview")).toBeNull();
  expect(record("CH2")).toBeVisible();
  expect(screen.queryByRole("group", { name: "待建立连线" })).not.toBeInTheDocument();
  await save();
  expect(stored.site.edges).toHaveLength(2);
  expect(stored.site.edges.find(item => item.id === "edge-keep")).toMatchObject({
    id: "edge-keep", label: "保留标签", signal_type: "electrical",
    source: { device_id: "source", port_id: "ch2" }, target: { device_id: "sink", port_id: "signal" },
  });
});

it("改接落到重复端口对或自身端口时提示并保留原线，且不产生未保存修改", async () => {
  stored.site.edges.push({ id: "edge-dup", source: { device_id: "source", port_id: "ch2" }, target: { device_id: "sink", port_id: "signal" }, signal_type: "electrical", label: "" });
  await openPage(); selectEdge("CH1");
  drag(screen.getByRole("button", { name: /^拖动改接起点：/ }), endpoint("改接信号源 / CH2"), 8);
  expect(screen.getByText(/这对物理端口已有连线（反向也算同一对）/)).toBeVisible();
  expect(record("CH1")).toBeVisible();
  drag(screen.getByRole("button", { name: /^拖动改接起点：/ }), endpoint("改接接收机 / Signal"), 9, 620, 340);
  expect(screen.getByText(/不能连接自身同一物理端口/)).toBeVisible();
  expect(record("CH1")).toBeVisible();
  expect(puts()).toHaveLength(0);
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled();
});

it("内部连线改接仅允许同器件内部输出到输入，拒绝物理口、跨器件与反向", async () => {
  await openPage(); selectEdge("Demod Y");
  drag(screen.getByRole("button", { name: /^拖动改接起点：/ }), endpoint("内部锁相 / V"), 10);
  expect(screen.getByText(/不能跨器件或混接物理端口/)).toBeVisible();
  drag(screen.getByRole("button", { name: /^拖动改接起点：/ }), internal("Aux 输入"), 11);
  expect(screen.getByText(/方向不会自动交换/)).toBeVisible();
  drag(screen.getByRole("button", { name: /^拖动改接起点：/ }), internal("外部输出", "另一器件"), 12);
  expect(screen.getByText(/不能跨器件或混接物理端口/)).toBeVisible();
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled();
  drag(screen.getByRole("button", { name: /^拖动改接终点：/ }), internal("Aux 输入 2"), 13);
  await save();
  expect(stored.site.edges.find(item => item.id === "edge-internal")).toMatchObject({
    id: "edge-internal", label: "内部保留", signal_type: "internal",
    source: { device_id: "lockin", port_id: "demod" }, target: { device_id: "lockin", port_id: "aux-in-2" },
  });
});

it("Esc取消改接，端口起线、节点拖动与空格平移仍按原手势工作", async () => {
  await openPage(); selectEdge("CH1");
  const viewport = screen.getByLabelText("可滚动接线平面");
  const handle = screen.getByRole("button", { name: /^拖动改接起点：/ });
  fireEvent.pointerDown(handle, { pointerId: 14, button: 0, clientX: 100, clientY: 100 });
  fireEvent.pointerMove(captureOwner!, { pointerId: 14, clientX: 400, clientY: 500 });
  expect(document.querySelector(".wiring-reconnect-preview")).not.toBeNull();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(document.querySelector(".wiring-reconnect-preview")).toBeNull();
  expect(record("CH1")).toBeVisible();
  pointerClick(endpoint("改接信号源 / CH2"));
  expect(screen.getByRole("button", { name: "取消连线" })).toBeVisible();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("button", { name: "取消连线" })).not.toBeInTheDocument();
  const card = screen.getByRole("group", { name: "器件快照：改接信号源" });
  fireEvent.pointerDown(snapshot("改接信号源"), { pointerId: 15, button: 0, clientX: 100, clientY: 100 });
  fireEvent.pointerMove(captureOwner!, { pointerId: 15, clientX: 140, clientY: 100 });
  expect(card).toHaveStyle({ left: "100px" });
  fireEvent.pointerUp(captureOwner!, { pointerId: 15, clientX: 140, clientY: 100 });
  viewport.scrollLeft = 0; viewport.scrollTop = 0;
  fireEvent.keyDown(viewport, { code: "Space" });
  fireEvent.pointerDown(viewport, { pointerId: 16, button: 0, clientX: 100, clientY: 100 });
  expect(viewport.className).toContain("panning");
  fireEvent.pointerMove(captureOwner!, { pointerId: 16, clientX: 60, clientY: 100 });
  expect(viewport.scrollLeft).toBe(40);
  fireEvent.pointerUp(captureOwner!, { pointerId: 16, clientX: 60, clientY: 100 });
  fireEvent.keyUp(viewport, { code: "Space" });
  expect(record("CH1")).toBeVisible();
  expect(puts()).toHaveLength(0);
});
