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
type Profile = components["schemas"]["DevicePortProfile"];
type Body = components["schemas"]["WiringBody"];
const TARGET = "follow-test-experiment";
const keys = ["Heat_Control", "Temp_Switch"];
const group = { keys, label: "温控用途", device_library_id: "library-old" };
const profile = (id = "logical-device", name = "原温控"): Profile => ({ id, name, model: "DG900", device_library_id: "library-old", ports: [] });
const graph = (name: string): Graph => ({ nodes: [{ device: profile("logical-device", name), x: 60, y: 60, follow_mapping: [...keys], port_directions: {} }], edges: [] });
const definition: ExperimentDefinition = { id: TARGET, title: "用途跟随测试实验", description: "只读测试", archived: false, category: "test", category_label: "测试", family: "test", variant: "test", required_devices: [], required_mapping_keys: [], execution_mode: "typed_workflow", acquisition_program: "test", analysis_program: null, wiring_notes: [], safety_notes: [], supports_cancel: true, can_analyze: false };
const response = (data: unknown, status = 200, code?: string) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json", ...(code ? { "X-Error-Code": code } : {}) } });
let stored: Document;
let profiles: Profile[];
let readFailure: boolean;
let revisionSupported: boolean;
let readWait: Promise<void> | undefined;
let saveWait: Promise<void> | undefined;
let saveConflict: boolean;
const requests: Array<{ path: string; method: string; body?: Body; signal?: AbortSignal | null }> = [];
const forbidden: string[] = [];
beforeEach(() => {
  stored = { schema_version: 1, revision: "combined-r1", site: graph("原温控"), targets: { [TARGET]: graph("目标原温控"), retired: graph("退役目标原温控") }, diagnostics: { site: [], targets: {} }, mapping_groups: [group], follow_status: { site: [{ device_id: "logical-device", state: "bound", mapping_keys: [...keys], message: "已绑定温控用途" }], targets: {} } };
  profiles = [profile("new-profile", "新建档案")]; readFailure = false; revisionSupported = true; readWait = undefined; saveWait = undefined; saveConflict = false;
  requests.length = 0; forbidden.length = 0; localStorage.clear();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input); const method = options?.method ?? "GET";
    const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    requests.push({ path, method, body, signal: options?.signal });
    if (path === "/api/health" && method === "GET") return response({ status: "ok", hardware_busy: false });
    if (path === "/api/jobs" && method === "GET") return response([]);
    if (path === "/api/experiments" && method === "GET") return response([definition]);
    if (path === `/api/experiments/${TARGET}` && method === "GET") return response(definition);
    if (path === `/api/experiments/${TARGET}/schema` && method === "GET") return response({ schema_version: 2, fields: [], parameter_layout_saved: true, parameter_layout: { basic: [], advanced: [] } });
    if (path === `/api/experiments/${TARGET}/derive` && method === "POST") return response({ ok: true, values: {} });
    if (path === "/api/device-ports" && method === "GET") return response({ schema_version: 1, revision: "ports-r1", devices: profiles, presets: [] });
    if (path === "/api/wiring/revision" && method === "GET") return revisionSupported ? response({ revision: stored.revision }) : response({ detail: "Not Found" }, 404);
    if (path === "/api/wiring" && method === "GET") { const result = structuredClone(stored); await readWait; return readFailure ? response({ detail: "映射配置暂不可读" }, 500) : response(result); }
    if (path === "/api/wiring" && method === "PUT") {
      await saveWait;
      if (saveConflict) return response({ detail: "组合修订已更新" }, 409, "revision_conflict");
      stored = { ...stored, revision: "combined-saved", site: body.site, targets: body.targets };
      return response(stored);
    }
    forbidden.push(`${method} ${path}`); throw new Error(`禁止的未模拟请求：${method} ${path}`);
  }));
});
afterEach(() => {
  cleanup(); expect(forbidden).toEqual([]);
  expect(requests.filter(item => item.method !== "GET").every(item => item.path === "/api/wiring" && item.method === "PUT" || item.path === `/api/experiments/${TARGET}/derive` && item.method === "POST")).toBe(true);
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});
const puts = () => requests.filter(item => item.method === "PUT");
const reads = () => requests.filter(item => item.method === "GET" && item.path === "/api/wiring");
const revisions = () => requests.filter(item => item.method === "GET" && item.path === "/api/wiring/revision");
const snapshot = (name: string) => screen.getByRole("button", { name: `移动器件：${name}` });
async function open(embedded = false) {
  const router = createMemoryRouter([{ path: "*", element: <NavigationGuard><App /></NavigationGuard> }], { initialEntries: [embedded ? `/experiments/${TARGET}` : "/instruments/wiring"] });
  render(<RouterProvider router={router} />);
  await screen.findByRole("heading", { level: 1, name: embedded ? definition.title : "实验接线图" }, { timeout: 15000 });
  if (embedded) fireEvent.click(screen.getByRole("tab", { name: "目标接线图" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "添加空白图内器件" })).toBeEnabled());
  return router;
}
async function check() {
  fireEvent.click(screen.getByRole("button", { name: "检查用途与接线图更新" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "检查用途与接线图更新" })).toBeEnabled());
}
function replaceMapping() {
  stored = { ...stored, revision: "combined-r2", site: graph("王湾温控"), targets: { [TARGET]: graph("目标王湾温控"), retired: graph("退役目标王湾温控") }, follow_status: { site: [{ device_id: "logical-device", state: "updated", mapping_keys: [...keys], message: "已从原温控跟随到王湾温控，仅更新记录" }], targets: {} } };
}
function rename(value: string) { fireEvent.click(snapshot("原温控")); fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value } }); }
async function saveAll() {
  fireEvent.click(screen.getByRole("button", { name: "保存全部接线图" }));
  await screen.findByText("全部接线图已保存。");
  await act(async () => {});
}

it("干净页面聚焦时按组合修订刷新现场和所有目标，不写配置或接线文件", async () => {
  await open(); expect(reads()).toHaveLength(1); replaceMapping();
  fireEvent(window, new Event("focus"));
  await screen.findByRole("button", { name: "移动器件：王湾温控" });
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "实验目标图" }));
  fireEvent.change(screen.getByLabelText("目标实验"), { target: { value: TARGET } });
  expect(snapshot("目标王湾温控")).toBeVisible();
  fireEvent.change(screen.getByLabelText("目标实验"), { target: { value: "retired" } });
  expect(snapshot("退役目标王湾温控")).toBeVisible();
  expect(puts()).toHaveLength(0); expect(reads()).toHaveLength(2); expect(revisions()).toHaveLength(1);
}, 15000);

it("组合修订未变时只请求修订，不重新拉取完整接线文档", async () => {
  await open(); expect(reads()).toHaveLength(1);
  fireEvent(window, new Event("focus"));
  await waitFor(() => expect(revisions()).toHaveLength(1));
  await act(async () => {});
  expect(reads()).toHaveLength(1);
  expect(puts()).toHaveLength(0);
}, 15000);

it("旧后端缺少修订接口时静默回退一次全量读取，再次检查不重复请求缺失接口", async () => {
  revisionSupported = false;
  await open(); replaceMapping();
  await check();
  expect(revisions()).toHaveLength(1);
  expect(reads()).toHaveLength(2);
  await screen.findByRole("button", { name: "移动器件：王湾温控" });
  expect(screen.queryByText(/用途更新检查失败/)).not.toBeInTheDocument();
  stored = { ...stored, revision: "combined-r3", site: graph("第二次更新") };
  await check();
  expect(revisions()).toHaveLength(1);
  expect(reads()).toHaveLength(3);
  await screen.findByRole("button", { name: "移动器件：第二次更新" });
}, 15000);

it("未保存草稿收到用途更新不被覆盖，旧基础修订和409保留到用户明确重新载入", async () => {
  await open(); rename("必须保留的草稿"); replaceMapping(); await check();
  expect(screen.getByText(/设备用途或接线图已有更新，当前草稿与基础修订已保留/)).toBeVisible();
  expect(snapshot("必须保留的草稿")).toBeVisible();
  expect(screen.getByText(/修订 combined-r1/)).toBeInTheDocument();
  saveConflict = true; fireEvent.click(screen.getByRole("button", { name: "保存全部接线图" }));
  await screen.findByText(/保存冲突（409），全部草稿与基础修订已保留/);
  expect(puts()[0].body?.base_revision).toBe("combined-r1");
  fireEvent.click(screen.getByRole("button", { name: "确认重新载入用途更新" }));
  expect(snapshot("必须保留的草稿")).toBeVisible();
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "确认重新载入用途更新" }));
  await screen.findByRole("button", { name: "移动器件：王湾温控" });
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeDisabled();
}, 15000);

it("检查发起后新编辑也保留，更新检查失败不把现有图当空或改基础修订", async () => {
  await open(); replaceMapping();
  let release!: () => void; readWait = new Promise<void>(done => { release = done; });
  fireEvent.click(screen.getByRole("button", { name: "检查用途与接线图更新" }));
  rename("检查过程中的草稿");
  await act(async () => release());
  await screen.findByText(/设备用途或接线图已有更新，当前草稿与基础修订已保留/);
  expect(snapshot("检查过程中的草稿")).toBeVisible();
  readWait = undefined; readFailure = true; await check();
  expect(screen.getByText(/用途更新检查失败，现有草稿和基础修订保留/)).toBeVisible();
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeEnabled();
  expect(screen.getByText(/修订 combined-r1/)).toBeInTheDocument();
}, 15000);

it("保存中阻止版本检查并忽略较早的迟到回执，保存结果不倒退", async () => {
  await open(); replaceMapping();
  let releaseRead!: () => void; readWait = new Promise<void>(done => { releaseRead = done; });
  fireEvent.click(screen.getByRole("button", { name: "检查用途与接线图更新" }));
  await act(async () => {});
  rename("保存结果应保留");
  let releaseSave!: () => void; saveWait = new Promise<void>(done => { releaseSave = done; });
  fireEvent.click(screen.getByRole("button", { name: "保存全部接线图" }));
  expect(reads().at(-1)?.signal?.aborted).toBe(true);
  fireEvent(window, new Event("focus")); expect(reads()).toHaveLength(2);
  await act(async () => releaseSave()); await screen.findByText(/全部接线图已保存。/);
  await act(async () => releaseRead());
  expect(snapshot("保存结果应保留")).toBeVisible();
  expect(screen.queryByText(/设备用途或接线图已有更新，当前草稿/)).not.toBeInTheDocument();
  expect(screen.getByText(/修订 combined-saved/)).toBeInTheDocument();
}, 15000);

it("唯一用途新档案自动绑定，多用途保持未选且必须显式选择；独立快照可关闭跟随", async () => {
  stored.site = { nodes: [], edges: [] }; await open();
  fireEvent.click(within(screen.getByRole("group", { name: "加入器件" })).getByRole("button", { name: /新建档案/ }));
  expect(screen.getByLabelText("用途跟随（当前图器件）")).toHaveValue(JSON.stringify(keys));
  await saveAll(); expect(puts()[0].body?.site.nodes[0].follow_mapping).toEqual(keys);
  cleanup(); requests.length = 0; stored.site = { nodes: [], edges: [] };
  stored.mapping_groups = [group, { keys: ["Pump_laser_power"], label: "泵浦用途", device_library_id: "library-old" }];
  await open(); fireEvent.click(within(screen.getByRole("group", { name: "加入器件" })).getByRole("button", { name: /新建档案/ }));
  expect(screen.getByLabelText("用途跟随（当前图器件）")).toHaveValue("auto");
  fireEvent.change(screen.getByLabelText("用途跟随（当前图器件）"), { target: { value: JSON.stringify(keys) } });
  await saveAll(); expect(puts()[0].body?.site.nodes[0].follow_mapping).toEqual(keys);
  fireEvent.change(screen.getByLabelText("用途跟随（当前图器件）"), { target: { value: "independent" } });
  await saveAll(); expect(puts().at(-1)?.body?.site.nodes[0].follow_mapping).toEqual([]);
}, 15000);

it("不能同步时保留原器件并显示未同步原因，自动识别的旧图可明确绑定后保存", async () => {
  stored.site.nodes[0].follow_mapping = null;
  stored.follow_status = { site: [{ device_id: "logical-device", state: "blocked", mapping_keys: keys, message: "未同步：新设备缺少完整对应端口；整张图保持原样" }], targets: {} };
  await open(); expect(snapshot("原温控")).toBeVisible();
  expect(screen.queryByRole("region", { name: "当前图用途跟随状态" })).not.toBeInTheDocument();
  const preflight = screen.getByRole("region", { name: "接线预检清单" });
  expect(within(preflight).getByText(/原温控：未同步：新设备缺少完整对应端口/)).toBeVisible();
  fireEvent.click(snapshot("原温控"));
  fireEvent.change(screen.getByLabelText("用途跟随（当前图器件）"), { target: { value: JSON.stringify(keys) } });
  await saveAll(); expect(puts()[0].body?.site.nodes[0].follow_mapping).toEqual(keys);
}, 15000);

it("用途跟随只列当前设备的用途组，跨设备已绑定项保留但禁用", async () => {
  const foreign = { keys: ["Pump_laser_power"], label: "泵浦用途", device_library_id: "library-other" };
  stored.mapping_groups = [group, foreign];
  stored.site.nodes[0].follow_mapping = [...foreign.keys];
  await open();
  fireEvent.click(snapshot("原温控"));
  const select = screen.getByLabelText("用途跟随（当前图器件）");
  expect(within(select).getAllByRole("option").map(option => option.textContent)).not.toContain("泵浦用途");
  expect(within(select).getByRole("option", { name: "温控用途" })).toBeEnabled();
  expect(within(select).getByRole("option", { name: /用途组属于其他设备，请改选或设为独立/ })).toBeDisabled();
  expect(select).toHaveValue(JSON.stringify(foreign.keys));
}, 15000);

it("手工节点没有设备库身份，用途跟随只提供独立快照", async () => {
  stored.site = { nodes: [], edges: [] };
  await open();
  fireEvent.click(screen.getByRole("button", { name: "添加空白图内器件" }));
  const select = screen.getByLabelText("用途跟随（当前图器件）");
  expect(within(select).getAllByRole("option").map(option => option.textContent)).toEqual(["独立快照，不跟随换机"]);
  expect(select).toHaveValue("independent");
  expect(screen.getByText(/手工节点没有设备库身份/)).toBeVisible();
}, 15000);

it("用途组键顺序不同仍识别为同一绑定，改选后按组内规范顺序回填", async () => {
  const ordered = ["Temp_Switch", "Heat_Control"];
  stored.mapping_groups = [{ keys: ordered, label: "温控用途", device_library_id: "library-old" }];
  stored.site.nodes[0].follow_mapping = [...ordered].reverse();
  await open();
  fireEvent.click(snapshot("原温控"));
  const select = screen.getByLabelText("用途跟随（当前图器件）");
  expect(select).toHaveValue(JSON.stringify([...ordered].sort()));
  expect(within(select).queryByRole("option", { name: /当前配置中未找到/ })).not.toBeInTheDocument();
  expect((select as HTMLSelectElement).selectedOptions[0].textContent).toBe("温控用途");
  fireEvent.change(select, { target: { value: "independent" } });
  fireEvent.change(select, { target: { value: JSON.stringify([...ordered].sort()) } });
  await saveAll();
  expect(puts()[0].body?.site.nodes[0].follow_mapping).toEqual(ordered);
}, 15000);

it("实验目标重新显示时检查版本；干净刷新、脏草稿提示且保存仅覆盖当前目标", async () => {
  await open(true); expect(snapshot("目标原温控")).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "运行" })); replaceMapping();
  fireEvent(window, new Event("focus")); expect(reads()).toHaveLength(1);
  fireEvent.click(screen.getByRole("tab", { name: "目标接线图" }));
  await screen.findByRole("button", { name: "移动器件：目标王湾温控" });
  fireEvent.click(snapshot("目标王湾温控"));
  fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "当前目标草稿" } });
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  stored = { ...stored, revision: "combined-r3", site: graph("更新后的现场"), targets: { ...stored.targets, retired: graph("更新后的退役目标") } };
  fireEvent.click(screen.getByRole("tab", { name: "目标接线图" }));
  await screen.findByText(/设备用途或接线图已有更新，当前草稿与基础修订已保留/);
  expect(snapshot("当前目标草稿")).toBeVisible();
  saveConflict = true;
  fireEvent.click(screen.getByRole("button", { name: "保存当前实验目标" }));
  await screen.findByText(/保存冲突（409），当前目标草稿与基础修订已保留/);
  expect(snapshot("当前目标草稿")).toBeVisible();
  expect(stored.revision).toBe("combined-r3");
  expect(stored.site.nodes[0].device.name).toBe("更新后的现场");
  expect(stored.targets.retired.nodes[0].device.name).toBe("更新后的退役目标");
  const sent = puts()[0].body!;
  expect(sent.base_revision).toBe("combined-r2");
  expect(sent.site.nodes[0].device.name).toBe("王湾温控");
  expect(sent.targets.retired.nodes[0].device.name).toBe("退役目标王湾温控");
  expect(sent.targets[TARGET].nodes[0].device.name).toBe("当前目标草稿");
}, 15000);

it("目标检查回执到达时已隐藏则不替换图，回到目标后再采用最新用途", async () => {
  await open(true); replaceMapping();
  let release!: () => void; readWait = new Promise<void>(done => { release = done; });
  // 实验内嵌页不再提供手动检查按钮，窗口重新获得焦点时自动检查。
  fireEvent(window, new Event("focus"));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  await act(async () => release());
  const hiddenPanel = screen.getByRole("button", { name: "移动器件：目标原温控", hidden: true }).closest<HTMLElement>('[role="tabpanel"]')!;
  expect(hiddenPanel).not.toBeVisible();
  expect(within(hiddenPanel).queryByRole("button", { name: "移动器件：目标王湾温控", hidden: true })).not.toBeInTheDocument();
  expect(puts()).toHaveLength(0);
  readWait = undefined;
  fireEvent.click(screen.getByRole("tab", { name: "目标接线图" }));
  await screen.findByRole("button", { name: "移动器件：目标王湾温控" });
  expect(reads()).toHaveLength(3);
  expect(screen.getByRole("button", { name: "保存当前实验目标" })).toBeDisabled();
}, 15000);

it("草稿有修改时预检中的跟随与诊断标注基于最近读取，不冒充当前状态", async () => {
  stored.follow_status = { site: [], targets: { [TARGET]: [{ device_id: "logical-device", state: "blocked", mapping_keys: [...keys], message: "未同步：新设备缺少完整对应端口" }] } };
  stored.diagnostics = { site: [], targets: { [TARGET]: [{ edge_id: "missing-edge", code: "type", message: "旧的光电类型提示" }] } };
  await open(true);
  const region = screen.getByRole("region", { name: "接线预检清单" });
  expect(within(region).getByText("目标原温控：未同步：新设备缺少完整对应端口（Heat_Control / Temp_Switch）")).toBeInTheDocument();
  expect(within(region).getByText("连线提示：旧的光电类型提示")).toBeInTheDocument();
  fireEvent.click(snapshot("目标原温控"));
  fireEvent.change(screen.getByLabelText("图内器件名称"), { target: { value: "草稿改名" } });
  expect(within(region).getByText("草稿改名：未同步：新设备缺少完整对应端口（Heat_Control / Temp_Switch）（基于最近读取/保存，草稿尚未保存）")).toBeInTheDocument();
  expect(within(region).getByText("连线提示：旧的光电类型提示（基于最近读取/保存，草稿尚未保存）")).toBeInTheDocument();
  expect(puts()).toHaveLength(0);
}, 15000);

it("旧后端未声明跟随契约时保持原快照，不发隐式版本检查请求", async () => {
  delete stored.mapping_groups; delete stored.follow_status;
  await open(); fireEvent(window, new Event("focus"));
  expect(reads()).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "检查用途与接线图更新" })).not.toBeInTheDocument();
  expect(snapshot("原温控")).toBeVisible();
}, 15000);
