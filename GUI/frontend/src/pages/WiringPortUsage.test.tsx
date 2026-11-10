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
const TARGET = "port-usage-test";
const graph = (): Graph => ({ nodes: [
  { device: { id: "source", name: "使用状态信号源", model: "DG900", device_library_id: null, ports: [
    { id: "out", name: "CH1", direction: "out", group: "", signal_type: "electrical", connector: "BNC", purpose: "", modes: [], selected_mode: null },
    { id: "mod", name: "MOD", direction: "switchable", group: "", signal_type: "electrical", connector: "BNC", purpose: "MOD（AM / FM）", modes: [
      { id: "sync", name: "同步", direction: "out" }, { id: "am", name: "AM", direction: "in" }, { id: "fm", name: "FM", direction: "in" },
    ], selected_mode: "am" },
  ] }, x: 60, y: 60, follow_mapping: [] },
  { device: { id: "receiver", name: "使用状态接收机", model: "REC", device_library_id: null, ports: [
    { id: "in", name: "Signal", direction: "in", group: "", signal_type: "electrical", connector: "BNC", purpose: "", modes: [], selected_mode: null },
  ] }, x: 480, y: 60, follow_mapping: [] },
], edges: [{ id: "kept-line", source: { device_id: "source", port_id: "out" }, target: { device_id: "receiver", port_id: "in" }, signal_type: "electrical", label: "已有连线" }] });
const definition: ExperimentDefinition = { id: TARGET, title: "端口使用实验", description: "无硬件路由验证", archived: false, category: "test", category_label: "测试", family: "test", variant: "test", required_devices: [], required_mapping_keys: [], execution_mode: "typed_workflow", acquisition_program: "test", analysis_program: null, wiring_notes: [], safety_notes: [], supports_cancel: true, can_analyze: false };
const response = (value: unknown, status = 200, code?: string) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json", ...(code ? { "X-Error-Code": code } : {}) } });
let stored: Document;
let saveFailure: "error" | "conflict" | undefined;
let saveWait: Promise<void> | undefined;
let comparison: Comparison;
const requests: Array<{ path: string; method: string; body?: Body | CompareBody }> = [];
const forbidden: string[] = [];
beforeEach(() => {
  stored = { schema_version: 1, revision: "usage-r1", site: graph(), targets: { [TARGET]: graph(), retired: graph() }, diagnostics: { site: [], targets: {} } };
  saveFailure = undefined; saveWait = undefined; requests.length = 0; forbidden.length = 0; localStorage.clear();
  comparison = { retained: stored.site.edges, removed: [], added: [], mode_changes: [], usage_changes: [], site_warnings: [], target_warnings: [] };
  vi.spyOn(window, "confirm").mockReturnValue(false);
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
      stored = { ...stored, revision: "usage-r2", site: body.site, targets: body.targets }; return response(stored);
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
const usage = (name = "MOD") => screen.getByRole("button", { name: `使用状态：使用状态信号源 / ${name}` });
const endpoint = (name = "MOD") => screen.queryByRole("button", { name: `连接端口：使用状态信号源 / ${name}` });
const unused = () => screen.getByRole("button", { name: "未用端口：使用状态信号源（1）" });
const puts = () => requests.filter(item => item.path === "/api/wiring" && item.method === "PUT");
const lastBody = () => puts().at(-1)!.body as Body;
function enableMod() { fireEvent.click(unused()); fireEvent.click(usage()); }
async function save(embedded = false) {
  fireEvent.click(screen.getByRole("button", { name: embedded ? "保存当前实验目标" : "保存全部接线图" }));
  await screen.findByText(embedded ? "当前实验目标接线图已保存；现场和其他实验目标保持不变，未操作硬件。" : "全部现场和实验目标接线图已保存；未连接硬件、未改软件映射或原设备端口档案。");
  await act(async () => {});
}
const unloadBlocked = () => { const event = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; };

it("旧图缺省和待确认无线端口默认未用，有线使用，切图不补写或变脏", async () => {
  stored.targets[TARGET].nodes[0].port_usage = { mod: "unconfirmed" };
  stored.targets.retired.nodes[0].port_usage = { mod: "used" };
  await open();
  expect(unused()).toHaveAttribute("aria-expanded", "false"); expect(endpoint()).not.toBeInTheDocument();
  expect(usage("CH1")).toHaveAttribute("aria-pressed", "true"); expect(usage("CH1")).toBeDisabled();
  fireEvent.click(unused()); expect(usage()).toHaveAttribute("aria-pressed", "false"); expect(usage()).toHaveTextContent("未用");
  expect(screen.queryByRole("combobox", { name: /^使用状态/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled(); expect(unloadBlocked()).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "实验目标图" }));
  expect(unused()).toHaveAttribute("aria-expanded", "false"); expect(endpoint()).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("目标实验"), { target: { value: "retired" } });
  expect(usage()).toHaveAttribute("aria-pressed", "true"); expect(endpoint()).toBeVisible();
  expect(puts()).toHaveLength(0); expect(stored.site.nodes[0]).not.toHaveProperty("port_usage");
  expect(stored.targets[TARGET].nodes[0].port_usage).toEqual({ mod: "unconfirmed" }); expect(unloadBlocked()).toBe(false);
}, 15000);

it("单按钮切换使用与未用，折叠灰显无连接锚点，保存仅写当前图", async () => {
  await open(); const targets = structuredClone(stored.targets); const ports = structuredClone(stored.site.nodes[0].device.ports); const edges = structuredClone(stored.site.edges);
  enableMod(); expect(endpoint()).toHaveAttribute("data-side", "left"); expect(unloadBlocked()).toBe(true);
  expect(screen.queryByRole("button", { name: /^未用端口/ })).not.toBeInTheDocument();
  fireEvent.click(usage()); expect(endpoint()).not.toBeInTheDocument();
  const list = screen.getByLabelText("使用状态信号源未用端口");
  expect(within(list).getByText("MOD").closest(".wiring-unused-item")).toBeInTheDocument();
  expect(list.querySelector("[data-port-id]")).toBeNull(); expect(usage()).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(unused()); expect(unused()).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(unused()); fireEvent.click(usage()); expect(endpoint()).toBeVisible(); fireEvent.click(usage()); await save();
  expect(lastBody().site.nodes[0].port_usage).toEqual({ mod: "unused" });
  expect(lastBody().site.nodes[0].device.ports).toEqual(ports); expect(lastBody().site.edges).toEqual(edges); expect(lastBody().targets).toEqual(targets); expect(unloadBlocked()).toBe(false);
}, 15000);

it("已有连线在画布和编辑区均禁切未用，强制点击也不删线或改变图", async () => {
  await open();
  expect(usage("CH1")).toBeDisabled(); fireEvent.click(usage("CH1"));
  expect(usage("CH1")).toHaveAttribute("aria-pressed", "true"); expect(endpoint("CH1")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "移动器件：使用状态信号源" }));
  const editor = screen.getByRole("group", { name: "图内端口 1" });
  const toggle = within(editor).getByRole("button", { name: "图内端口使用状态：使用状态信号源 / CH1" });
  expect(toggle).toBeDisabled(); fireEvent.click(toggle);
  expect(screen.getAllByRole("button", { name: /^选择连线：/ })).toHaveLength(1); expect(toggle).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled(); expect(puts()).toHaveLength(0);
}, 15000);

it("旧响应有线未用矛盾时投影为使用并保留原线，读取仍不悄悄改写", async () => {
  stored.site.nodes[0].port_usage = { out: "unused" };
  await open();
  expect(endpoint("CH1")).toBeEnabled(); expect(usage("CH1")).toHaveAttribute("aria-pressed", "true"); expect(usage("CH1")).toBeDisabled();
  expect(screen.getAllByRole("button", { name: /^选择连线：/ })).toHaveLength(1);
  expect(unused()).toBeVisible(); expect(puts()).toHaveLength(0); expect(stored.site.nodes[0].port_usage).toEqual({ out: "unused" });
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled(); expect(unloadBlocked()).toBe(false);
}, 15000);

it("连线起点改未用会取消手势，恢复使用后连线保留原记录，拆线不自动切未用", async () => {
  await open(); enableMod();
  fireEvent.click(endpoint()!); expect(screen.getByRole("button", { name: "取消连线" })).toBeVisible();
  fireEvent.click(usage());
  expect(screen.queryByRole("button", { name: "取消连线" })).not.toBeInTheDocument(); expect(endpoint()).not.toBeInTheDocument();
  fireEvent.click(usage());
  fireEvent.click(endpoint()!); fireEvent.click(screen.getByRole("button", { name: "连接端口：使用状态接收机 / Signal" }));
  fireEvent.change(screen.getByLabelText("新连线光电类型"), { target: { value: "electrical" } }); fireEvent.click(screen.getByRole("button", { name: "记录连线" }));
  expect(usage()).toHaveAttribute("aria-pressed", "true"); expect(usage()).toBeDisabled(); await save();
  expect(lastBody().site.nodes[0].port_usage).toEqual({ mod: "used" }); expect(lastBody().site.edges).toHaveLength(2); expect(lastBody().site.edges[0].id).toBe("kept-line");
  fireEvent.click(screen.getByRole("button", { name: "选择连线记录：使用状态信号源 / MOD ↔ 使用状态接收机 / Signal" }));
  fireEvent.click(screen.getByRole("button", { name: "删除所选连线" }));
  expect(usage()).toHaveAttribute("aria-pressed", "true"); expect(usage()).toBeEnabled(); expect(endpoint()).toBeVisible();
}, 15000);

it("使用差异与模式和物理连线分列，编辑状态会清除过期比较", async () => {
  stored.targets[TARGET].nodes[0].port_usage = { mod: "used" };
  comparison.usage_changes = [{ device_id: "source", device_name: "使用状态信号源", port_id: "mod", port_name: "MOD", site_usage: "unused", target_usage: "used" }];
  await open();
  fireEvent.click(screen.getByRole("button", { name: "比较现场与当前目标草稿" }));
  const result = await screen.findByLabelText("草稿比较结果");
  expect(within(result).getByText("使用状态信号源 / MOD：未用 → 使用")).toBeVisible();
  expect(within(result).getByText("无模式变化")).toBeVisible();
  const body = requests.find(item => item.path === "/api/wiring/compare")!.body as CompareBody;
  expect(body.site.nodes[0]).not.toHaveProperty("port_usage"); expect(body.target.nodes[0].port_usage).toEqual({ mod: "used" }); expect(body.site.edges).toEqual(body.target.edges);
  enableMod();
  expect(screen.queryByLabelText("草稿比较结果")).not.toBeInTheDocument(); expect(puts()).toHaveLength(0);
}, 15000);

it.each(["error", "conflict"] as const)("使用状态保存 %s 保草稿、基础修订和离开保护，保存中不可修改", async failure => {
  await open(); const original = structuredClone(stored); enableMod();
  let release!: () => void; saveWait = new Promise<void>(done => { release = done; }); saveFailure = failure;
  fireEvent.click(screen.getByRole("button", { name: "保存全部接线图" })); expect(usage()).toBeDisabled();
  fireEvent.click(usage());
  await act(async () => release()); await screen.findByText(failure === "conflict" ? /保存冲突（409），全部草稿与基础修订已保留/ : /保存失败，全部草稿与基础修订已保留/);
  expect(usage()).toHaveAttribute("aria-pressed", "true"); expect(screen.getByText(/修订 usage-r1/)).toBeVisible(); expect(unloadBlocked()).toBe(true);
  expect(lastBody().site.nodes[0].port_usage).toEqual({ mod: "used" }); expect(stored).toEqual(original);
  fireEvent.click(screen.getByRole("button", { name: "重新载入" })); expect(usage()).toHaveAttribute("aria-pressed", "true"); expect(requests.filter(item => item.path === "/api/wiring" && item.method === "GET")).toHaveLength(1);
}, 15000);

it("目标使用按钮草稿跨运行Tab保留，保存只改当前目标而非现场和其他目标", async () => {
  await open(true); const site = structuredClone(stored.site); const retired = structuredClone(stored.targets.retired);
  enableMod(); fireEvent.click(usage());
  fireEvent.click(screen.getByRole("tab", { name: "运行" })); fireEvent.click(screen.getByRole("tab", { name: "目标接线图" }));
  expect(unused()).toHaveAttribute("aria-expanded", "true"); expect(endpoint()).not.toBeInTheDocument(); expect(unloadBlocked()).toBe(true);
  await save(true); expect(lastBody().targets[TARGET].nodes[0].port_usage).toEqual({ mod: "unused" });
  expect(lastBody().site).toEqual(site); expect(lastBody().targets.retired).toEqual(retired); expect(unloadBlocked()).toBe(false);
}, 15000);

it("删除未用端口先确认并清除使用字典，不留下悬空状态或改变其他图", async () => {
  stored.site.nodes[0].port_usage = { mod: "unused" };
  await open(); const targets = structuredClone(stored.targets); const edges = structuredClone(stored.site.edges);
  fireEvent.click(screen.getByRole("button", { name: "移动器件：使用状态信号源" }));
  const editor = screen.getByRole("group", { name: "图内端口 2" });
  fireEvent.click(within(editor).getByRole("button", { name: "删除图内端口及相关连线" }));
  expect(unused()).toBeVisible();
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.click(within(editor).getByRole("button", { name: "删除图内端口及相关连线" })); await save();
  expect(lastBody().site.nodes[0].port_usage).toEqual({}); expect(lastBody().site.nodes[0].device.ports.map(port => port.id)).toEqual(["out"]);
  expect(lastBody().site.edges).toEqual(edges); expect(lastBody().targets).toEqual(targets);
}, 15000);
