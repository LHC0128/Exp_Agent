import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
type Port = components["schemas"]["PhysicalPort"];
const TARGET = "overview-branches-test";
const port = (id: string, name: string, direction: "in" | "out"): Port => ({ id, name, direction, group: "", signal_type: "electrical", connector: "BNC", purpose: "", modes: [], selected_mode: null });
const graph = (): Graph => ({ nodes: [
  { device: { id: "source", name: "公共时钟源", model: "Clock", device_library_id: null, ports: [port("out", "10 MHz A", "out"), port("other", "10 MHz B", "out")] }, x: 60, y: 260, follow_mapping: [], port_usage: { out: "used", other: "used" } },
  { device: { id: "a", name: "接收仪器甲", model: "A", device_library_id: null, ports: [port("in", "参考输入", "in")] }, x: 720, y: 60, follow_mapping: [] },
  { device: { id: "b", name: "接收仪器乙", model: "B", device_library_id: null, ports: [port("in", "参考输入", "in")] }, x: 720, y: 460, follow_mapping: [] },
], edges: [
  { id: "edge-a", source: { device_id: "source", port_id: "out" }, target: { device_id: "a", port_id: "in" }, signal_type: "electrical", label: "10 MHz" },
  { id: "edge-b", source: { device_id: "source", port_id: "out" }, target: { device_id: "b", port_id: "in" }, signal_type: "electrical", label: "10 MHz" },
] });
const definition: ExperimentDefinition = { id: TARGET, title: "总览分支测试实验", description: "无硬件视图验证", archived: false, category: "test", category_label: "测试", family: "test", variant: "test", required_devices: [], required_mapping_keys: [], execution_mode: "typed_workflow", acquisition_program: "test", analysis_program: null, wiring_notes: [], safety_notes: [], supports_cancel: true, can_analyze: false };
const response = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
let stored: Document;
let captureOwner: Element | null = null;
let browserPrecision = false;
let intrinsicLabels = false;
let fontScale = 1;
let screenLabelMeasurements = 0;
const originalGetBBox = Object.getOwnPropertyDescriptor(SVGElement.prototype, "getBBox");
const labelMetrics = (element: Element) => ({ width: Array.from(element.textContent ?? "").length * 10 * fontScale * (element.parentElement?.classList.contains("selected") ? 1.25 : 1), height: 16.0045 * fontScale });
const requests: Array<{ path: string; method: string; body?: Body }> = [];
const forbidden: string[] = [];
const rect = (left: number, top: number, width: number, height: number): DOMRect => ({ x: left, y: top, left, top, width, height, right: left + width, bottom: top + height, toJSON: () => ({}) });
beforeEach(() => {
  stored = { schema_version: 1, revision: "overview-r1", site: graph(), targets: { [TARGET]: graph(), retired: { nodes: [], edges: [] } }, diagnostics: { site: [], targets: {} } };
  requests.length = 0; forbidden.length = 0; captureOwner = null; browserPrecision = false; intrinsicLabels = false; fontScale = 1; screenLabelMeasurements = 0; localStorage.clear();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  class TestPointerEvent extends MouseEvent {
    pointerId: number;
    constructor(type: string, options: PointerEventInit = {}) { super(type, options); this.pointerId = options.pointerId ?? 1; }
  }
  vi.stubGlobal("PointerEvent", TestPointerEvent);
  Object.defineProperties(Element.prototype, {
    setPointerCapture: { configurable: true, value: vi.fn(function (this: Element) { captureOwner = this; }) },
    hasPointerCapture: { configurable: true, value: vi.fn(function (this: Element) { return captureOwner === this; }) },
    releasePointerCapture: { configurable: true, value: vi.fn(function (this: Element) { if (captureOwner === this) captureOwner = null; }) },
  });
  Object.defineProperty(document, "elementFromPoint", { configurable: true, value: vi.fn(() => null) });
  vi.spyOn(Element.prototype, "clientWidth", "get").mockImplementation(function (this: Element) { return this.matches(".wiring-viewport") ? this.closest(".wiring-canvas-expanded") ? 1200 : 900 : 0; });
  vi.spyOn(Element.prototype, "clientHeight", "get").mockImplementation(function (this: Element) { return this.matches(".wiring-viewport") ? this.closest(".wiring-canvas-expanded") ? 800 : 620 : 0; });
  // jsdom 无布局：以实际 transform、滚动和节点样式模拟屏幕矩形，不固定为 zoom=1。
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const plane = this.closest<HTMLElement>(".wiring-plane"); const view = plane?.closest<HTMLElement>(".wiring-viewport");
    if (this.matches(".wiring-viewport")) return rect(100, 100, this.clientWidth, this.clientHeight);
    if (!plane) return rect(0, 0, 100, 20);
    const zoom = Number(plane.dataset.zoom ?? 1); const origin = { x: 100 - (view?.scrollLeft ?? 0), y: 100 - (view?.scrollTop ?? 0) };
    if (this === plane) return rect(origin.x, origin.y, Number.parseFloat(plane.style.width) * zoom, Number.parseFloat(plane.style.height) * zoom);
    if (this.matches("text[data-edge-label]")) {
      screenLabelMeasurements++;
      const x = Number(this.getAttribute("x")); const y = Number(this.getAttribute("y"));
      if (intrinsicLabels) {
        const size = labelMetrics(this);
        const left = Math.fround(origin.x + (x - size.width / 2) * zoom); const top = Math.fround(origin.y + (y - size.height + 1.78575) * zoom);
        const right = Math.fround(origin.x + (x + size.width / 2) * zoom); const bottom = Math.fround(origin.y + (y + 1.78575) * zoom);
        return rect(left, top, right - left, bottom - top);
      }
      if (browserPrecision) {
        // Chromium 的 SVG 包围盒由单精度端点相减；尺寸会随标签位置有微小变化。
        const left = Math.fround(origin.x + (x - 73.147) * zoom); const top = Math.fround(origin.y + (y - 14.21875) * zoom);
        const right = Math.fround(origin.x + (x + 73.147) * zoom); const bottom = Math.fround(origin.y + (y + 1.78575) * zoom);
        return rect(left, top, right - left, bottom - top);
      }
      return rect(origin.x + (x - 90) * zoom, origin.y + (y - 14) * zoom, 180 * zoom, 14 * zoom);
    }
    const node = this.closest<HTMLElement>(".wiring-node");
    if (!node) return rect(origin.x, origin.y, 1, 1);
    const inset = node.style.transform.match(/translate\(([-\d.]+)px, ([-\d.]+)px\)/);
    const x = Number.parseFloat(node.style.left) + Number(inset?.[1] ?? 0); const y = Number.parseFloat(node.style.top) + Number(inset?.[2] ?? 0);
    if (this.matches("[data-port-id]")) {
      const siblings = Array.from(this.closest(".wiring-node-ports")!.querySelectorAll("[data-port-id]"));
      const offset = this.getAttribute("data-side") === "right" ? 140 : 8;
      return rect(origin.x + (x + offset) * zoom, origin.y + (y + 50 + siblings.indexOf(this) * 36) * zoom, 100 * zoom, 20 * zoom);
    }
    return rect(origin.x + x * zoom, origin.y + y * zoom, 248 * zoom, 180 * zoom);
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
    if (path === "/api/wiring" && method === "PUT") { stored = { ...stored, revision: "overview-r2", site: body.site, targets: body.targets }; return response(stored); }
    forbidden.push(`${method} ${path}`); throw new Error(`禁止的未模拟请求：${method} ${path}`);
  }));
});
afterEach(() => {
  cleanup(); expect(forbidden).toEqual([]);
  expect(requests.filter(item => item.method !== "GET").every(item => item.method === "PUT" && item.path === "/api/wiring" || item.method === "POST" && item.path === `/api/experiments/${TARGET}/derive`)).toBe(true);
  delete (Element.prototype as Partial<Element>).setPointerCapture; delete (Element.prototype as Partial<Element>).hasPointerCapture; delete (Element.prototype as Partial<Element>).releasePointerCapture;
  if (originalGetBBox) Object.defineProperty(SVGElement.prototype, "getBBox", originalGetBBox); else Reflect.deleteProperty(SVGElement.prototype, "getBBox");
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});
async function open(embedded = false) {
  const router = createMemoryRouter([{ path: "*", element: <NavigationGuard><App /></NavigationGuard> }], { initialEntries: [embedded ? `/experiments/${TARGET}` : "/instruments/wiring"] });
  render(<RouterProvider router={router} />);
  await screen.findByRole("heading", { level: 1, name: embedded ? definition.title : "实验接线图" }, { timeout: 15000 });
  if (embedded) fireEvent.click(screen.getByRole("tab", { name: "目标接线图" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "添加空白图内器件" })).toBeEnabled());
}
const view = () => screen.getByLabelText("可滚动接线平面");
const plane = () => screen.getByTestId("wiring-plane");
const zoom = () => Number(plane().dataset.zoom);
const node = (name = "公共时钟源") => screen.getByRole("button", { name: `移动器件：${name}` });
const endpoint = (name: string) => screen.getByRole("button", { name: `连接端口：${name}` });
const puts = () => requests.filter(item => item.method === "PUT");
const unloadBlocked = () => { const event = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; };
const clean = (embedded = false) => { expect(screen.getByRole("button", { name: embedded ? "保存当前实验目标" : "保存全部接线图" })).toBeDisabled(); expect(unloadBlocked()).toBe(false); expect(puts()).toHaveLength(0); };
const ids = (element: Element) => JSON.parse(element.getAttribute("data-edge-ids")!) as string[];
async function save(embedded = false) {
  fireEvent.click(screen.getByRole("button", { name: embedded ? "保存当前实验目标" : "保存全部接线图" }));
  await screen.findByText(embedded ? "当前实验目标接线图已保存。" : "全部接线图已保存。");
  await act(async () => {});
  return puts().at(-1)!.body!;
}
function drag(element: Element, dx: number, dy: number) {
  const start = element.getBoundingClientRect();
  fireEvent.pointerDown(element, { pointerId: 7, button: 0, clientX: start.left + 5, clientY: start.top + 5 });
  fireEvent.pointerMove(captureOwner!, { pointerId: 7, clientX: start.left + 5 + dx, clientY: start.top + 5 + dy });
  fireEvent.pointerUp(captureOwner!, { pointerId: 7, clientX: start.left + 5 + dx, clientY: start.top + 5 + dy });
}

it("同源公共段仅画一次、真实分支点可见，物理反向存储仍识别实际输出源", async () => {
  const first = stored.site.edges[0]; [first.source, first.target] = [first.target, first.source];
  const before = structuredClone(stored); await open();
  const common = Array.from(plane().querySelectorAll(".wiring-shared-segment")).filter(segment => ids(segment).length > 1);
  expect(common.length).toBeGreaterThan(0); expect(common.every(segment => ids(segment).join(",") === "edge-a,edge-b")).toBe(true);
  const paths = common.map(segment => segment.querySelector(".wiring-line-stroke")!.getAttribute("d")); expect(new Set(paths).size).toBe(paths.length);
  expect(plane().querySelectorAll(".wiring-junction").length).toBeGreaterThan(0);
  expect(plane().querySelectorAll("[data-edge-id] > .wiring-line-stroke")).toHaveLength(0);
  expect(screen.getAllByRole("button", { name: /^选择连线：/ })).toHaveLength(2); clean(); expect(stored).toEqual(before);
}, 15000);

it.each(["different-output", "different-signal", "unknown"])("%s 不按10MHz标签猜同源，既不共段也不画伪连接点", async mode => {
  if (mode === "different-output") stored.site.edges[1].source.port_id = "other";
  if (mode === "different-signal") stored.site.edges[1].signal_type = "optical";
  if (mode === "unknown") stored.site.nodes[0].device.ports[0].direction = null;
  await open(); expect(plane().querySelectorAll(".wiring-shared-segment")).toHaveLength(0); expect(plane().querySelectorAll(".wiring-junction")).toHaveLength(0);
  expect(plane().querySelectorAll(".wiring-line-stroke")).toHaveLength(2); clean();
}, 15000);

it("公共段明确选择支路；悬停、鼠标和键盘选择高亮完整路径与两个端口但不改图", async () => {
  await open(); const before = structuredClone(stored);
  const common = screen.getAllByRole("button", { name: "选择公共段：2 条支路" })[0];
  fireEvent.pointerEnter(common);
  expect(plane().querySelectorAll("[data-edge-id].selected")).toHaveLength(2); expect(plane().querySelectorAll(".wiring-port-highlight")).toHaveLength(3);
  expect(screen.getByRole("status", { name: "高亮连线路径" })).toHaveTextContent("接收仪器甲");
  fireEvent.pointerLeave(common); fireEvent.click(common);
  expect(screen.queryByLabelText("连线标签")).not.toBeInTheDocument();
  const choices = screen.getByRole("group", { name: "公共段支路选择" });
  fireEvent.click(within(choices).getByRole("button", { name: /接收仪器甲/ }));
  expect(screen.queryByRole("group", { name: "公共段支路选择" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("连线标签")).toHaveValue("10 MHz"); expect(plane().querySelector('[data-edge-id="edge-a"]')).toHaveClass("selected");
  expect(endpoint("公共时钟源 / 10 MHz A")).toHaveClass("wiring-port-highlight"); expect(endpoint("接收仪器甲 / 参考输入")).toHaveClass("wiring-port-highlight");
  expect(endpoint("接收仪器乙 / 参考输入")).not.toHaveClass("wiring-port-highlight");
  const keyboard = screen.getByRole("button", { name: /^选择连线：.*接收仪器乙/ }); fireEvent.keyDown(keyboard, { key: "Enter" });
  expect(plane().querySelector('[data-edge-id="edge-b"]')).toHaveClass("selected"); expect(endpoint("接收仪器乙 / 参考输入")).toHaveClass("wiring-port-highlight");
  fireEvent.keyDown(common, { key: " " }); expect(screen.getByRole("group", { name: "公共段支路选择" })).toBeVisible(); fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("group", { name: "公共段支路选择" })).not.toBeInTheDocument(); clean(); expect(stored).toEqual(before);
}, 15000);

it("展开、适合实际全图、缩放和空格平移仅改视图，Esc还原焦点与body滚动", async () => {
  await open(); const before = structuredClone(stored); const titles = stored.site.nodes.map(item => item.device.name);
  const expand = screen.getByRole("button", { name: "展开画布" }); fireEvent.click(expand);
  expect(screen.getByRole("dialog", { name: "接线画布" })).toHaveAttribute("aria-modal", "true"); expect(document.body.style.overflow).toBe("hidden");
  fireEvent.click(screen.getByRole("button", { name: "适合全图" })); expect(zoom()).toBeGreaterThan(.5); expect(zoom()).toBeLessThanOrEqual(1);
  for (const title of titles) { const box = node(title).getBoundingClientRect(); expect(box.left).toBeGreaterThanOrEqual(100); expect(box.right).toBeLessThanOrEqual(1300); expect(box.top).toBeGreaterThanOrEqual(100); expect(box.bottom).toBeLessThanOrEqual(900); }
  for (const label of plane().querySelectorAll("text[data-edge-label]")) { const box = label.getBoundingClientRect(); expect(box.left).toBeGreaterThanOrEqual(100); expect(box.right).toBeLessThanOrEqual(1300); expect(box.top).toBeGreaterThanOrEqual(100); expect(box.bottom).toBeLessThanOrEqual(900); }
  fireEvent.click(screen.getByRole("button", { name: "100%" })); expect(zoom()).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "缩小画布" })); expect(zoom()).toBe(.8);
  view().scrollLeft = 220; view().scrollTop = 140; view().focus(); fireEvent.keyDown(view(), { key: " ", code: "Space" });
  drag(view(), 60, 40); fireEvent.keyUp(window, { key: " ", code: "Space" }); expect(view().scrollLeft).toBe(160); expect(view().scrollTop).toBe(100);
  expect(screen.queryByRole("group", { name: "待建立连线" })).not.toBeInTheDocument();
  fireEvent.keyDown(window, { key: "Escape" }); expect(screen.queryByRole("dialog", { name: "接线画布" })).not.toBeInTheDocument(); expect(document.body.style.overflow).toBe(""); expect(expand).toHaveFocus();
  clean(); expect(stored).toEqual(before);
}, 15000);

it("Ctrl滚轮围绕鼠标缩放，普通滚轮不截获且1%到200%有界", async () => {
  await open(); view().scrollLeft = 200; view().scrollTop = 100;
  const focus = { x: 250, y: 180 }; const logical = { x: (view().scrollLeft + focus.x) / zoom(), y: (view().scrollTop + focus.y) / zoom() };
  const ctrl = new WheelEvent("wheel", { bubbles: true, cancelable: true, ctrlKey: true, deltaY: -50, clientX: 100 + focus.x, clientY: 100 + focus.y });
  fireEvent(view(), ctrl); expect(ctrl.defaultPrevented).toBe(true); expect(zoom()).toBeGreaterThan(1);
  expect((view().scrollLeft + focus.x) / zoom()).toBeCloseTo(logical.x, 5); expect((view().scrollTop + focus.y) / zoom()).toBeCloseTo(logical.y, 5);
  const previous = zoom(); const ordinary = new WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: 60 }); fireEvent(view(), ordinary);
  expect(ordinary.defaultPrevented).toBe(false); expect(zoom()).toBe(previous);
  for (let index = 0; index < 5; index++) fireEvent.click(screen.getByRole("button", { name: "放大画布" })); expect(zoom()).toBe(2); expect(screen.getByRole("button", { name: "放大画布" })).toBeDisabled();
  for (let index = 0; index < 25; index++) fireEvent.click(screen.getByRole("button", { name: "缩小画布" })); expect(zoom()).toBe(.01); expect(screen.getByRole("button", { name: "缩小画布" })).toBeDisabled(); clean();
}, 15000);

it("全连线双视图不省略任何网络，进入总览适合完整图并恢复接线视角", async () => {
  stored.site.edges[0].label = "完整时钟路径说明".repeat(12);
  const before = structuredClone(stored); await open();
  const wiring = screen.getByRole("button", { name: "接线视图" }); const overview = screen.getByRole("button", { name: "总览视图" });
  expect(wiring).toHaveAttribute("aria-pressed", "true"); expect(overview).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(screen.getByRole("button", { name: "展开画布" })); fireEvent.click(screen.getByRole("button", { name: "缩小画布" }));
  const wiringZoom = zoom();
  view().scrollLeft = 230; view().scrollTop = 170;
  const snapshot = () => ({ edges: [...plane().querySelectorAll("[data-edge-id]")].map(element => element.getAttribute("data-edge-id")), paths: [...plane().querySelectorAll(".wiring-line-stroke")].map(element => element.getAttribute("d")), junctions: plane().querySelectorAll(".wiring-junction").length });
  const allLines = snapshot(); fireEvent.click(overview);
  expect(overview).toHaveAttribute("aria-pressed", "true"); expect(snapshot()).toEqual(allLines);
  for (const element of plane().querySelectorAll(".wiring-node, text[data-edge-label]")) {
    const box = element.getBoundingClientRect(); expect(box.left).toBeGreaterThanOrEqual(100); expect(box.right).toBeLessThanOrEqual(1300); expect(box.top).toBeGreaterThanOrEqual(100); expect(box.bottom).toBeLessThanOrEqual(900);
  }
  fireEvent.click(screen.getByRole("button", { name: "缩小画布" })); view().scrollLeft = 70; view().scrollTop = 90;
  fireEvent.click(overview); expect(view().scrollLeft).toBe(70); expect(view().scrollTop).toBe(90); // 同一视图不反复自动取景。
  fireEvent.click(wiring); expect(zoom()).toBe(wiringZoom); expect(view().scrollLeft).toBe(230); expect(view().scrollTop).toBe(170); expect(snapshot()).toEqual(allLines);
  fireEvent.click(overview); fireEvent.click(wiring); expect(zoom()).toBe(wiringZoom); expect(view().scrollLeft).toBe(230); expect(view().scrollTop).toBe(170);
  fireEvent.keyDown(window, { key: "Escape" }); clean(); expect(stored).toEqual(before);
}, 15000);

it("首次进入图纸自动适合全图一次，切回已看过的图不再重复取景", async () => {
  const before = structuredClone(stored); await open();
  const fitted = zoom();
  expect(fitted).toBeLessThan(1); expect(fitted).toBeGreaterThan(.1);
  for (const element of plane().querySelectorAll(".wiring-node, text[data-edge-label]")) {
    const box = element.getBoundingClientRect(); expect(box.left).toBeGreaterThanOrEqual(100); expect(box.right).toBeLessThanOrEqual(1000); expect(box.top).toBeGreaterThanOrEqual(100); expect(box.bottom).toBeLessThanOrEqual(720);
  }
  fireEvent.change(screen.getByLabelText("目标实验"), { target: { value: TARGET } });
  fireEvent.click(screen.getByRole("button", { name: "实验目标图" }));
  expect(zoom()).toBeCloseTo(fitted, 5);
  fireEvent.click(screen.getByRole("button", { name: "共享现场图" }));
  expect(zoom()).toBe(1);
  clean(); expect(stored).toEqual(before);
}, 15000);

it.each(["optical", "unknown", "internal"] as const)("总览不折叠 %s 连线，仍可选中完整路径", async type => {
  if (type === "internal") {
    stored.site.nodes[0].internal_ports = [{ id: "internal-out", name: "内部源", direction: "out" }, { id: "internal-in", name: "内部接收", direction: "in" }];
    stored.site.edges = [{ id: "internal-edge", source: { device_id: "source", port_id: "internal-out" }, target: { device_id: "source", port_id: "internal-in" }, signal_type: "internal", label: "内部完整路径" }];
  } else if (type === "unknown") { stored.site.nodes[0].device.ports[0].direction = null; stored.site.nodes[0].device.ports[0].signal_type = null; }
  else stored.site.edges.forEach(edge => { edge.signal_type = type; });
  const before = structuredClone(stored); await open(); fireEvent.click(screen.getByRole("button", { name: "总览视图" }));
  expect(plane().querySelectorAll("[data-edge-id]")).toHaveLength(stored.site.edges.length);
  // 内部标签只保留在连线列表与提示中，卡片内不再绘制文字；路径本身仍可聚焦选择。
  expect(plane().querySelectorAll("text[data-edge-label]")).toHaveLength(type === "internal" ? 0 : stored.site.edges.length);
  if (type === "internal") {
    expect(plane().querySelectorAll("[data-internal-route]")).toHaveLength(stored.site.edges.length);
    const records = screen.getAllByRole("button", { name: /^选择连线记录：/ });
    expect(records).toHaveLength(stored.site.edges.length); expect(records[0]).toHaveTextContent("内部完整路径");
  }
  const first = screen.getAllByRole("button", { name: /^选择连线：/ })[0]; fireEvent.keyDown(first, { key: "Enter" });
  expect(plane().querySelectorAll(".wiring-port-highlight")).toHaveLength(2); expect(screen.getByRole("status", { name: "高亮连线路径" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "接线视图" })); clean(); expect(stored).toEqual(before);
}, 15000);

it("切换双视图保留目标草稿、已选线和待确认新连线，保存仍仅当前目标", async () => {
  await open(true); const before = structuredClone(stored); fireEvent.click(node());
  fireEvent.keyDown(node(), { key: "ArrowRight" });
  fireEvent.keyDown(screen.getByRole("button", { name: /^选择连线：.*接收仪器甲/ }), { key: "Enter" });
  fireEvent.click(screen.getByRole("button", { name: "总览视图" })); expect(screen.getByLabelText("连线标签")).toHaveValue("10 MHz");
  fireEvent.click(endpoint("公共时钟源 / 10 MHz B"));
  fireEvent.click(screen.getByRole("button", { name: "接线视图" }));
  fireEvent.click(endpoint("接收仪器甲 / 参考输入"));
  const pending = screen.getByRole("group", { name: "待建立连线" }); expect(pending).toBeVisible();
  fireEvent.change(screen.getByLabelText("新连线光电类型"), { target: { value: "electrical" } }); fireEvent.change(screen.getByLabelText("新连线标签"), { target: { value: "待确认支路" } });
  fireEvent.click(screen.getByRole("button", { name: "总览视图" })); fireEvent.click(screen.getByRole("tab", { name: "运行" })); fireEvent.click(screen.getByRole("tab", { name: "目标接线图" }));
  expect(screen.getByRole("button", { name: "总览视图" })).toHaveAttribute("aria-pressed", "true"); expect(pending).toBeVisible(); expect(screen.getByLabelText("新连线标签")).toHaveValue("待确认支路");
  expect(node()).toBeVisible(); expect(unloadBlocked()).toBe(true); expect(puts()).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "记录连线" })); const body = await save(true);
  expect(body.site).toEqual(before.site); expect(body.targets.retired).toEqual(before.targets.retired);
  expect(body.targets[TARGET].nodes[0].x).toBe(80); expect(body.targets[TARGET].edges.slice(0, 2)).toEqual(before.targets[TARGET].edges);
  expect(body.targets[TARGET].edges[2]).toMatchObject({ source: { device_id: "source", port_id: "other" }, target: { device_id: "a", port_id: "in" }, label: "待确认支路" });
  expect(Object.keys(body.targets[TARGET]).sort()).toEqual(["edges", "nodes"]);
}, 15000);

it("空图双视图切换不创建记录，同缩放切回仍恢复滚动且Tab返回不重取景", async () => {
  stored.targets[TARGET] = { nodes: [], edges: [] }; const before = structuredClone(stored); await open(true);
  view().scrollLeft = 80; view().scrollTop = 65; fireEvent.click(screen.getByRole("button", { name: "总览视图" }));
  expect(screen.getByRole("button", { name: "适合全图" })).toBeDisabled(); expect(zoom()).toBe(1);
  view().scrollLeft = 25; view().scrollTop = 15; fireEvent.click(screen.getByRole("tab", { name: "运行" })); fireEvent.click(screen.getByRole("tab", { name: "目标接线图" }));
  expect(view().scrollLeft).toBe(25); expect(view().scrollTop).toBe(15);
  fireEvent.click(screen.getByRole("button", { name: "接线视图" })); expect(zoom()).toBe(1); expect(view().scrollLeft).toBe(80); expect(view().scrollTop).toBe(65);
  clean(true); expect(stored).toEqual(before);
}, 15000);

it("SVG单精度包围盒在布局反馈边界收敛且不产生草稿", async () => {
  browserPrecision = true;
  const before = structuredClone(stored); await open();
  for (let index = 0; index < 3; index++) {
    fireEvent.click(screen.getByRole("button", { name: "适合全图" }));
    fireEvent.click(screen.getByRole("button", { name: "100%" }));
    fireEvent.click(screen.getByRole("button", { name: "缩小画布" }));
    fireEvent(window, new Event("resize"));
    expect(screen.queryByText("页面加载失败，请刷新页面重试。")).not.toBeInTheDocument();
    expect(plane().querySelectorAll("[data-edge-id]")).toHaveLength(2);
  }
  clean(); expect(stored).toEqual(before);
}, 15000);

it.each(["zero", "throw", "nonfinite"])("SVG本地测量 %s 时回退仍收敛，1%%缩放不制造草稿", async mode => {
  browserPrecision = true;
  Object.defineProperty(SVGElement.prototype, "getBBox", { configurable: true, value: () => {
    if (mode === "throw") throw new Error("模拟不可测量的 SVG");
    return mode === "zero" ? rect(0, 0, 0, 0) : rect(0, 0, Number.NaN, Number.POSITIVE_INFINITY);
  } });
  const before = structuredClone(stored); await open(); expect(screenLabelMeasurements).toBeGreaterThan(0);
  for (let index = 0; index < 25; index++) fireEvent.click(screen.getByRole("button", { name: "缩小画布" }));
  expect(zoom()).toBe(.01); fireEvent(window, new Event("resize"));
  fireEvent.click(screen.getByRole("button", { name: "100%" }));
  expect(plane().querySelectorAll("text[data-edge-label]")).toHaveLength(2); clean(); expect(stored).toEqual(before);
}, 15000);

it("SVG本地尺寸经观察器更新，高亮不触发几何重测，屏幕缩放不回传字形尺寸", async () => {
  intrinsicLabels = true;
  stored.site.edges[0].label = "完整端口名和信号说明".repeat(8);
  const observed: Array<{ elements: Set<Element>; update: () => void; disconnected: boolean }> = [];
  vi.stubGlobal("ResizeObserver", class {
    record: typeof observed[number];
    constructor(update: () => void) { this.record = { elements: new Set(), update, disconnected: false }; observed.push(this.record); }
    observe(element: Element) { this.record.elements.add(element); }
    disconnect() { this.record.disconnected = true; }
  });
  const local = vi.fn(function (this: Element) { const size = labelMetrics(this); return rect(-12000, -4000, size.width, size.height); });
  Object.defineProperty(SVGElement.prototype, "getBBox", { configurable: true, value: local });
  const before = structuredClone(stored); await open();
  const text = () => plane().querySelector('text[data-edge-label="edge-a"]')!;
  const x = () => Number(text().getAttribute("x")); const initialX = x();
  const latestObserver = () => [...observed].reverse().find(item => !item.disconnected)!;
  expect(local).toHaveBeenCalled(); expect(screenLabelMeasurements).toBe(0);
  const common = screen.getAllByRole("button", { name: "选择公共段：2 条支路" })[0];
  fireEvent.pointerEnter(common); expect(x()).toBe(initialX);
  fireEvent.pointerLeave(common); expect(x()).toBe(initialX);
  fireEvent.click(common); expect(x()).toBe(initialX);
  fireEvent.click(within(screen.getByRole("group", { name: "公共段支路选择" })).getByRole("button", { name: /接收仪器甲/ }));
  expect(x()).toBe(initialX);
  expect(latestObserver().elements.has(text())).toBe(true);
  fontScale = 1.1; act(() => latestObserver().update()); expect(x()).toBeGreaterThan(initialX);
  fireEvent.click(screen.getByRole("button", { name: "展开画布" })); fireEvent.click(screen.getByRole("button", { name: "适合全图" }));
  expect(screenLabelMeasurements).toBe(0);
  for (const element of plane().querySelectorAll(".wiring-node, text[data-edge-label]")) {
    const box = element.getBoundingClientRect(); expect(box.left).toBeGreaterThanOrEqual(100); expect(box.right).toBeLessThanOrEqual(1300); expect(box.top).toBeGreaterThanOrEqual(100); expect(box.bottom).toBeLessThanOrEqual(900);
  }
  fireEvent.keyDown(window, { key: "Escape" }); clean(); expect(stored).toEqual(before);
  fireEvent.keyDown(screen.getByRole("button", { name: /^选择连线：.*接收仪器甲/ }), { key: "Enter" });
  const previousX = x(); const longer = `${before.site.edges[0].label}增加的完整标签`.repeat(2);
  fireEvent.change(screen.getByLabelText("连线标签"), { target: { value: longer } });
  expect(text()).toHaveTextContent(longer); expect(x()).toBeGreaterThan(previousX);
  expect(unloadBlocked()).toBe(true); expect(puts()).toHaveLength(0); expect(stored).toEqual(before);
}, 15000);

it("缩放后标题拖动按逻辑20网格保存，raw端点与其他目标不变", async () => {
  await open(); const before = structuredClone(stored); fireEvent.click(screen.getByRole("button", { name: "100%" })); fireEvent.click(screen.getByRole("button", { name: "缩小画布" }));
  drag(node(), 64, 32); expect(unloadBlocked()).toBe(true);
  const body = await save(); expect(body.base_revision).toBe("overview-r1"); expect(body.site.nodes[0]).toEqual({ ...before.site.nodes[0], x: 140, y: 300 });
  expect(body.site.nodes.slice(1)).toEqual(before.site.nodes.slice(1)); expect(body.site.edges).toEqual(before.site.edges); expect(body.targets).toEqual(before.targets);
}, 15000);

it("缩放后端口拖线按屏幕落点建立记录，embedded保存仅当前目标且视图不入数据", async () => {
  await open(true); const before = structuredClone(stored); fireEvent.click(screen.getByRole("button", { name: "缩小画布" }));
  vi.mocked(document.elementFromPoint).mockReturnValue(endpoint("接收仪器甲 / 参考输入")); drag(endpoint("公共时钟源 / 10 MHz B"), 500, -80);
  expect(screen.getByRole("group", { name: "待建立连线" })).toBeVisible(); fireEvent.change(screen.getByLabelText("新连线光电类型"), { target: { value: "electrical" } });
  fireEvent.click(screen.getByRole("button", { name: "记录连线" })); const body = await save(true);
  expect(body.site).toEqual(before.site); expect(body.targets.retired).toEqual(before.targets.retired); expect(body.targets[TARGET].nodes).toEqual(before.targets[TARGET].nodes);
  expect(body.targets[TARGET].edges.slice(0, 2)).toEqual(before.targets[TARGET].edges); expect(body.targets[TARGET].edges[2]).toMatchObject({ source: { device_id: "source", port_id: "other" }, target: { device_id: "a", port_id: "in" }, signal_type: "electrical" });
  expect(Object.keys(body.targets[TARGET]).sort()).toEqual(["edges", "nodes"]);
}, 15000);

it("目标Tab隐藏时退出展开并释放滚动，草稿仍保留；重新进入不自动适合全图", async () => {
  await open(true); const before = structuredClone(stored); fireEvent.click(screen.getByRole("button", { name: "100%" })); fireEvent.click(screen.getByRole("button", { name: "缩小画布" }));
  fireEvent.click(node()); fireEvent.keyDown(node(), { key: "ArrowRight" });
  fireEvent.click(screen.getByRole("button", { name: "展开画布" })); expect(document.body.style.overflow).toBe("hidden");
  fireEvent.click(screen.getByRole("tab", { name: "运行" })); expect(document.body.style.overflow).toBe("");
  fireEvent.click(screen.getByRole("tab", { name: "目标接线图" })); expect(node()).toBeVisible(); expect(zoom()).toBe(.8); expect(screen.queryByRole("dialog", { name: "接线画布" })).not.toBeInTheDocument();
  expect(unloadBlocked()).toBe(true); expect(puts()).toHaveLength(0); expect(stored).toEqual(before);
}, 15000);

it("空图可展开和缩放但适合全图禁用，卸载恢复原有body滚动且不创建目标", async () => {
  stored.site = { nodes: [], edges: [] }; stored.targets = {}; await open(); document.body.style.overflow = "clip";
  fireEvent.click(screen.getByRole("button", { name: "展开画布" })); expect(screen.getByRole("button", { name: "适合全图" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "缩小画布" })); clean(); cleanup(); expect(document.body.style.overflow).toBe("clip"); document.body.style.overflow = ""; expect(stored.targets).toEqual({});
}, 15000);
