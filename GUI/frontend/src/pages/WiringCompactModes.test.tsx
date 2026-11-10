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
type Port = components["schemas"]["PhysicalPort"];
type Body = components["schemas"]["WiringBody"];
type CompareBody = components["schemas"]["WiringCompareBody"];
const TARGET = "compact-mode-test";
const port = (id: string, name: string, direction: Port["direction"]): Port => ({ id, name, group: "", signal_type: "electrical", connector: "", purpose: "", direction, modes: [], selected_mode: null });
const graph = (): Graph => ({ nodes: [
  { device: { id: "source", name: "紧凑信号源", model: "MODEL-A", device_library_id: null, ports: [
    port("out", "CH1", "out"), port("bidir", "DIO", "inout"),
    { ...port("ref", "Reference", "switchable"), modes: [
      { id: "ref-in", name: "外部参考接收", direction: "in" },
      { id: "ref-out-a", name: "时钟输出 A", direction: "out" },
      { id: "ref-out-b", name: "时钟输出 B", direction: "out" },
    ] }, port("unknown", "未知方向", null),
  ] }, x: 60, y: 60, follow_mapping: [], port_directions: {}, port_usage: { out: "used", bidir: "used", ref: "used", unknown: "used" } },
  { device: { id: "receiver", name: "紧凑接收机", model: "MODEL-B", device_library_id: null, ports: [port("in", "Signal", "in")] }, x: 480, y: 60, follow_mapping: [], port_directions: {} },
], edges: [{ id: "kept-line", source: { device_id: "source", port_id: "out" }, target: { device_id: "receiver", port_id: "in" }, signal_type: "electrical", label: "已有连接" }] });
const definition: ExperimentDefinition = { id: TARGET, title: "紧凑模式实验", description: "无硬件回归", archived: false, category: "test", category_label: "测试", family: "test", variant: "test", required_devices: [], required_mapping_keys: [], execution_mode: "typed_workflow", acquisition_program: "test", analysis_program: null, wiring_notes: [], safety_notes: [], supports_cancel: true, can_analyze: false };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
let stored: Document;
let saveWait: Promise<void> | undefined;
let saveFailure: boolean;
const requests: Array<{ path: string; method: string; body?: Body | CompareBody }> = [];
const forbidden: string[] = [];
let measuringHidden = false;
beforeEach(() => {
  stored = { schema_version: 1, revision: "compact-r1", site: graph(), targets: { [TARGET]: graph(), retired: graph() }, diagnostics: { site: [], targets: {} } };
  saveWait = undefined; saveFailure = false; measuringHidden = false; requests.length = 0; forbidden.length = 0; localStorage.clear();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  // 从真实渲染按钮读取锚点；隐藏 Tab 的按钮按浏览器行为返回零尺寸。
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const node = this.closest<HTMLElement>(".wiring-node");
    const hidden = measuringHidden && !!this.closest('[role="tabpanel"][hidden]');
    const translation = node?.style.transform.match(/translate\(([-\d.]+)px, ([-\d.]+)px\)/);
    const x = hidden ? 0 : (Number.parseInt(node?.style.left ?? "0") || 0) + Number(translation?.[1] ?? 0);
    const y = hidden ? 0 : (Number.parseInt(node?.style.top ?? "0") || 0) + Number(translation?.[2] ?? 0);
    const isPort = this.matches("[data-port-id]");
    const left = x + (isPort && !hidden ? this.getAttribute("data-side") === "right" ? 140 : 8 : 0);
    const top = y + (isPort && !hidden ? 50 : 0);
    const width = hidden ? 0 : isPort ? 100 : node ? 248 : 2600;
    const height = hidden ? 0 : isPort ? 20 : node ? 180 : 1600;
    if (this.matches("text[data-edge-label]")) return { x: 0, y: 0, left: 0, top: 0, right: 100, bottom: 14, width: 100, height: 14, toJSON: () => ({}) };
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
      if (saveFailure) return response({ detail: "保存故障，原图未变" }, 500);
      stored = { ...stored, revision: "compact-r2", site: body.site, targets: body.targets }; return response(stored);
    }
    if (path === "/api/wiring/compare" && method === "POST") return response({ retained: body.site.edges, removed: [], added: [], mode_changes: [], site_warnings: [], target_warnings: [] });
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
}
const shortcut = (direction: "输入" | "输出", name: string) => screen.getByRole("button", { name: `记录${direction}：紧凑信号源 / ${name}` });
const endpoint = (name: string) => screen.getByRole("button", { name: `连接端口：紧凑信号源 / ${name}` });
const puts = () => requests.filter(item => item.method === "PUT");
const lastBody = () => puts().at(-1)!.body as Body;
async function save(embedded = false) {
  fireEvent.click(screen.getByRole("button", { name: embedded ? "保存当前实验目标" : "保存全部接线图" }));
  await screen.findByText(embedded ? "当前实验目标接线图已保存。" : "全部接线图已保存。");
  await act(async () => {});
}

it("真实路由呈现顶部一行名称型号和下方两列端口，未知方向明确且不渲染空分组或重复光电文字", async () => {
  await open();
  const title = screen.getByRole("button", { name: "移动器件：紧凑信号源" });
  expect(Array.from(title.children).map(child => child.textContent)).toEqual(["紧凑信号源", "MODEL-A"]);
  const node = title.closest(".wiring-node")!;
  expect(node.children[0]).toBe(title);
  expect(endpoint("CH1")).toHaveAttribute("data-side", "right");
  expect(endpoint("DIO")).toHaveAttribute("data-side", "left");
  expect(endpoint("Reference")).toHaveAttribute("data-side", "left");
  expect(within(node as HTMLElement).getAllByText("? 方向待确认")).toHaveLength(2);
  expect(within(node as HTMLElement).getByText("历史方向待确认")).toBeVisible();
  expect(within(node as HTMLElement).queryByText("暂无")).not.toBeInTheDocument();
  expect(within(node as HTMLElement).queryByText(/图内快照|电学 ·|坐标/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled(); expect(puts()).toHaveLength(0);
}, 15000);

it("双向口快捷方向只改当前图override，保存保物理能力、连线、目标和退役图", async () => {
  await open(); const targets = structuredClone(stored.targets); const edges = structuredClone(stored.site.edges);
  fireEvent.change(screen.getByLabelText("目标实验"), { target: { value: TARGET } });
  fireEvent.click(shortcut("输出", "DIO"));
  expect(endpoint("DIO")).toHaveAttribute("data-side", "right"); expect(shortcut("输出", "DIO")).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "比较现场与当前目标草稿" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "比较现场与当前目标草稿" }));
  await screen.findByLabelText("草稿比较结果");
  const comparison = requests.find(item => item.path === "/api/wiring/compare")!.body as CompareBody;
  expect(comparison.site.nodes[0].port_directions).toEqual({ bidir: "out" });
  expect(comparison.target.nodes[0].port_directions).toEqual({});
  await save();
  expect(lastBody().site.nodes[0].device.ports.find(item => item.id === "bidir")?.direction).toBe("inout");
  expect(lastBody().site.nodes[0].port_directions).toEqual({ bidir: "out" });
  expect(lastBody().site.edges).toEqual(edges); expect(lastBody().targets).toEqual(targets);
  fireEvent.click(shortcut("输入", "DIO")); expect(endpoint("DIO")).toHaveAttribute("data-side", "left"); await save();
  expect(lastBody().site.nodes[0].port_directions).toEqual({ bidir: "in" });
}, 15000);

it("复用口只选功能决定单向，全部模式明确选择，取消和Esc不猜选", async () => {
  await open();
  const choose = () => fireEvent.click(screen.getByRole("button", { name: "选择图内模式：紧凑信号源 / Reference" }));
  expect(screen.queryByRole("button", { name: "记录输出：紧凑信号源 / Reference" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "记录输入：紧凑信号源 / Reference" })).not.toBeInTheDocument();
  choose();
  const selector = screen.getByRole("combobox", { name: "确认图内模式：紧凑信号源 / Reference" });
  expect(selector).toHaveValue("");
  expect(within(selector).getAllByRole("option").map(item => item.textContent)).toEqual(["请明确选择", "外部参考接收（输入）", "时钟输出 A（输出）", "时钟输出 B（输出）"]);
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "取消模式选择：紧凑信号源 / Reference" }));
  expect(screen.queryByRole("combobox", { name: /^确认图内模式/ })).not.toBeInTheDocument();
  choose(); fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("combobox", { name: /^确认图内模式/ })).not.toBeInTheDocument();
  choose();
  fireEvent.change(screen.getByRole("combobox", { name: "确认图内模式：紧凑信号源 / Reference" }), { target: { value: "ref-out-b" } });
  expect(endpoint("Reference")).toHaveAttribute("data-side", "right");
  await save(); expect(lastBody().site.nodes[0].device.ports.find(item => item.id === "ref")?.selected_mode).toBe("ref-out-b");
  choose(); fireEvent.change(screen.getByRole("combobox", { name: "确认图内模式：紧凑信号源 / Reference" }), { target: { value: "ref-in" } });
  expect(endpoint("Reference")).toHaveAttribute("data-side", "left");
  await save(); expect(lastBody().site.nodes[0].device.ports.find(item => item.id === "ref")?.selected_mode).toBe("ref-in");
}, 15000);

it("模式切换后真实端口锚点和连线身份更新，标题键盘摆放仍吸附网格", async () => {
  stored.site.edges[0].source.port_id = "bidir";
  await open();
  const line = screen.getByRole("button", { name: /^选择连线：/ });
  const before = line.getAttribute("d"); expect(before).toMatch(/^M 108 160 L/);
  fireEvent.click(shortcut("输出", "DIO"));
  await waitFor(() => expect(line.getAttribute("d")).toMatch(/^M 340 160 L/));
  expect(screen.getAllByRole("button", { name: /^选择连线：/ })).toHaveLength(1);
  fireEvent.keyDown(screen.getByRole("button", { name: "移动器件：紧凑信号源" }), { key: "ArrowRight" });
  expect(screen.getByRole("button", { name: "移动器件：紧凑信号源" }).closest(".wiring-node")).toHaveStyle({ left: "80px" });
  await save(); expect(lastBody().site.edges[0].id).toBe("kept-line"); expect(lastBody().site.edges[0].source).toEqual({ device_id: "source", port_id: "bidir" });
}, 15000);

it("同机左右端口的连线绕开整个盒子，标签不落在器件文字上", async () => {
  stored.site.edges = [{ ...stored.site.edges[0], target: { device_id: "source", port_id: "bidir" } }];
  await open();
  const line = screen.getByRole("button", { name: /^选择连线：/ });
  const d = line.getAttribute("d")!;
  const values = d.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
  const samples: Array<{ x: number; y: number }> = [];
  if (d.includes(" C ")) {
    const [ax, ay, cx, cy, dx, dy, bx, by] = values;
    for (let step = 0; step <= 40; step++) {
      const t = step / 40; const s = 1 - t;
      samples.push({ x: s ** 3 * ax + 3 * s ** 2 * t * cx + 3 * s * t ** 2 * dx + t ** 3 * bx,
        y: s ** 3 * ay + 3 * s ** 2 * t * cy + 3 * s * t ** 2 * dy + t ** 3 * by });
    }
  } else {
    for (let index = 2; index < values.length; index += 2) for (let step = 0; step <= 20; step++) {
      const t = step / 20;
      samples.push({ x: values[index - 2] + (values[index] - values[index - 2]) * t, y: values[index - 1] + (values[index + 1] - values[index - 1]) * t });
    }
  }
  const insideBody = (point: { x: number; y: number }) => point.x > 110 && point.x < 338 && point.y > 100 && point.y < 280;
  expect(samples.filter(insideBody)).toEqual([]);
  const label = line.closest("g")!.querySelector("text")!;
  expect(insideBody({ x: Number(label.getAttribute("x")), y: Number(label.getAttribute("y")) })).toBe(false);
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled(); expect(puts()).toHaveLength(0);
}, 15000);

it("零坐标同机回连的完整线段和标签仍在可滚动画布范围内，不改变保存坐标", async () => {
  stored.site.nodes[0].x = 0; stored.site.nodes[0].y = 0;
  stored.site.edges = [{ ...stored.site.edges[0], target: { device_id: "source", port_id: "bidir" } }];
  await open();
  const line = screen.getByRole("button", { name: /^选择连线：/ });
  const values = line.getAttribute("d")!.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
  expect(values.every(value => value >= 0)).toBe(true);
  const label = line.closest("g")!.querySelector("text")!;
  expect(Number(label.getAttribute("x"))).toBeGreaterThanOrEqual(0);
  expect(Number(label.getAttribute("y"))).toBeGreaterThanOrEqual(14);
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled();
  expect(stored.site.nodes[0]).toMatchObject({ x: 0, y: 0 }); expect(puts()).toHaveLength(0);
}, 15000);

it("保存中禁用快捷模式，失败保留工作方向草稿和基础修订", async () => {
  await open(); fireEvent.click(shortcut("输出", "DIO"));
  let release!: () => void; saveWait = new Promise<void>(done => { release = done; }); saveFailure = true;
  fireEvent.click(screen.getByRole("button", { name: "保存全部接线图" }));
  expect(shortcut("输入", "DIO")).toBeDisabled(); fireEvent.click(shortcut("输入", "DIO"));
  await act(async () => release()); await screen.findByText(/保存失败，全部草稿与基础修订已保留/);
  expect(endpoint("DIO")).toHaveAttribute("data-side", "right"); expect(screen.getByText(/修订 compact-r1/)).toBeVisible();
  expect(lastBody().site.nodes[0].port_directions).toEqual({ bidir: "out" }); expect(stored.site.nodes[0].port_directions).toEqual({});
}, 15000);

it("目标模式草稿跨Tab保留且重新显示重算锚点，保存只改当前实验", async () => {
  measuringHidden = true; await open(true); const site = structuredClone(stored.site); const retired = structuredClone(stored.targets.retired);
  fireEvent.click(shortcut("输出", "DIO"));
  const before = screen.getByRole("button", { name: /^选择连线：/ }).getAttribute("d");
  fireEvent.click(screen.getByRole("tab", { name: "运行" })); fireEvent(window, new Event("resize"));
  fireEvent.click(screen.getByRole("tab", { name: "目标接线图" }));
  await waitFor(() => expect(screen.getByRole("button", { name: /^选择连线：/ })).toHaveAttribute("d", before));
  expect(endpoint("DIO")).toHaveAttribute("data-side", "right"); expect(shortcut("输出", "DIO")).toHaveAttribute("aria-pressed", "true");
  await save(true); expect(lastBody().site).toEqual(site); expect(lastBody().targets.retired).toEqual(retired);
  expect(lastBody().targets[TARGET].nodes[0].port_directions).toEqual({ bidir: "out" });
}, 15000);
