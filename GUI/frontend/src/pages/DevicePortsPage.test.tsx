import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "../App";
import { NavigationGuard } from "../components/NavigationGuard";
import type { DeviceLibraryDocument } from "../types/api";
import type { components } from "../types/openapi";

type PhysicalPort = components["schemas"]["PhysicalPort"];
type DevicePortProfile = components["schemas"]["DevicePortProfile"];
type DevicePortsResponse = components["schemas"]["DevicePortsResponse"];
const port = (id: string, name: string, direction: PhysicalPort["direction"] = null): PhysicalPort => ({
  id, name, group: "", signal_type: null, connector: "", purpose: "", direction, modes: [], selected_mode: null,
});
const presets: DevicePortsResponse["presets"] = [
  { id: "dg4000", name: "DG4000 端口预置", model: "DG4000", ports: [
    { ...port("ch1", "CH1", "out"), signal_type: "electrical" as const, connector: "BNC" },
    { ...port("clock", "10 MHz In/Out", "switchable"), signal_type: "electrical" as const,
      modes: [{ id: "ref-in", name: "外部参考", direction: "in" as const }, { id: "ref-out", name: "内部参考输出", direction: "out" as const }] },
  ] },
  { id: "dg900", name: "DG900 端口预置", model: "DG900", ports: [port("ch1", "CH1", "out")] },
  { id: "hf2", name: "HF2 端口预置", model: "HF2", ports: [port("sig-in", "Signal Input 1", "in")] },
  { id: "mfli13", name: "MFLI13 端口预置", model: "MFLI13", ports: [
    { ...port("signal-v", "V", "in"), group: "Signal Input", signal_type: "electrical" },
    { ...port("aux-v", "V", "in"), group: "Aux Input", signal_type: "electrical" },
    { ...port("ref", "10 MHz", "switchable"), signal_type: "electrical", modes: [
      { id: "ref-in", name: "参考输入", direction: "in" }, { id: "ref-out", name: "参考输出", direction: "out" },
    ] },
  ] }, 
];
const initial = (): DevicePortsResponse => ({ schema_version: 1, revision: "ports-r1", devices: [], presets: structuredClone(presets) });
const device = (id = "device-a", name = "已有设备", ports: PhysicalPort[] = []): DevicePortProfile => ({ id, name, model: "", device_library_id: null, ports });
let stored: ReturnType<typeof initial>;
let library: DeviceLibraryDocument;
let readFailure = false;
let libraryFailure = false;
let saveFailure: "" | "error" | "conflict" = "";
let pendingSave: Promise<void> | undefined;
const requests: Array<{ path: string; method: string; body?: { base_revision: string; devices: ReturnType<typeof initial>["devices"] } }> = [];
const forbidden: string[] = [];
const response = (body: unknown, status = 200, code?: string) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json", ...(code ? { "X-Error-Code": code } : {}) },
});

beforeEach(() => {
  stored = initial(); readFailure = false; libraryFailure = false; saveFailure = ""; pendingSave = undefined;
  requests.length = 0; forbidden.length = 0;
  localStorage.clear();
  library = { schema_version: 1, revision: "library-r1", network_resources: [], devices: {
    "library-generator": { instrument: "signal_generator", model: "DG4000", label: "主信号源", resource: "USB::fake", reference_clock: "EXT", connection: {}, capabilities: {} },
    "library-lockin": { instrument: "lockin_amplifier", model: "HF2", label: "锁相放大器", resource: null, reference_clock: "EXT", connection: { device_id: "dev-not-library-key" }, capabilities: {} },
    "library-other": { instrument: "passive", model: "未知光学器件", label: "分束器", resource: null, reference_clock: null, connection: {}, capabilities: {} },
  } };
  vi.spyOn(window, "confirm").mockReturnValue(false);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input);
    const method = options?.method ?? "GET";
    const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    requests.push({ path, method, body });
    if (path === "/api/health" && method === "GET") return response({ status: "ok", hardware_busy: false });
    if (path === "/api/jobs" && method === "GET") return response([
      { id: "passive-job", kind: "clock-sync", status: "running", message: "全局任务横幅保留", percent: 20 },
    ]);
    if (path === "/api/control-targets" && method === "GET") return response({ device_library_revision: "library-r1", physical_mapping_revision: "mapping-r1", targets: [] });
    if (path === "/api/device-library" && method === "GET") return libraryFailure ? response({ detail: "设备库暂不可用" }, 503) : response(library);
    if (path === "/api/device-ports" && method === "GET") return readFailure ? response({ detail: "档案读取失败" }, 500) : response(stored);
    if (path === "/api/device-ports" && method === "PUT") {
      await pendingSave;
      if (saveFailure === "error") return response({ detail: "磁盘写入失败" }, 500);
      if (saveFailure === "conflict") return response({ detail: "其他窗口已修改档案" }, 409, "revision_conflict");
      stored = { ...stored, revision: "ports-r2", devices: body.devices };
      return response(stored);
    }
    forbidden.push(`${method} ${path}`);
    throw new Error(`禁止的未模拟请求：${method} ${path}`);
  }));
});
afterEach(() => {
  cleanup();
  expect(forbidden).toEqual([]);
  expect(requests.every(item => item.method === "GET" || (item.method === "PUT" && item.path === "/api/device-ports"))).toBe(true);
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});

async function openPage(path = "/instruments/ports") {
  const router = createMemoryRouter([{ path: "*", element: <NavigationGuard><App /></NavigationGuard> }], { initialEntries: [path] });
  render(<RouterProvider router={router} />);
  if (path === "/instruments/ports") await screen.findByRole("heading", { name: "设备端口建档", level: 1 }, { timeout: 15000 });
  else await screen.findByRole("heading", { name: "仪器控制", level: 1 }, { timeout: 15000 });
  return router;
}
async function ready() {
  await waitFor(() => expect(screen.getByRole("button", { name: "新建独立设备" })).toBeEnabled());
}
async function saveAll() {
  fireEvent.click(screen.getByRole("button", { name: "保存全部档案" }));
  await screen.findByText("全部设备端口档案已保存；没有连接硬件或下发任何设置。");
}
function editPort(number = 1) {
  return within(screen.getByRole("group", { name: `端口 ${number} 编辑` }));
}
function preview(label: "输入端口" | "输出端口" | "待确认端口") {
  return within(screen.getByRole("region", { name: label }));
}
const puts = () => requests.filter(item => item.method === "PUT");

it("真实仪器页入口和直接路由均加载端口建档，并保留全局横幅和返回入口", async () => {
  const router = await openPage("/instruments");
  fireEvent.click(screen.getByRole("link", { name: "设备端口建档" }));
  expect(await screen.findByRole("heading", { name: "设备端口建档", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("全局任务横幅保留")).toBeVisible();
  expect(screen.getByText(/非硬件实际状态/)).toBeVisible();
  fireEvent.click(screen.getByRole("link", { name: "返回仪器控制" }));
  expect(await screen.findByRole("heading", { name: "仪器控制", level: 1 })).toBeInTheDocument();
  await act(async () => router.navigate("/instruments/ports"));
  expect(await screen.findByRole("heading", { name: "设备端口建档", level: 1 })).toBeVisible();
  await waitFor(() => expect(screen.getByRole("button", { name: "新建独立设备" })).toBeEnabled());
  expect(requests.some(item => item.path === "/api/device-ports" && item.method === "GET")).toBe(true);
});

it.each(["dg4000", "dg900", "hf2", "mfli13"])("独立设备复制 %s 服务端预置，模式初始未知且保存重载保留稳定ID", async presetId => {
  await openPage(); await ready();
  const preset = presets.find(item => item.id === presetId)!;
  fireEvent.change(screen.getByLabelText("新设备预置"), { target: { value: presetId } });
  fireEvent.click(screen.getByRole("button", { name: "新建独立设备" }));
  expect(screen.getByLabelText("设备型号")).toHaveValue(preset.model);
  expect(screen.getAllByRole("group", { name: /端口 \d+ 编辑/ })).toHaveLength(preset.ports.length);
  if (presetId === "dg4000") {
    expect(editPort(2).getByLabelText("当前记录模式（仅档案）")).toHaveValue("");
    expect(preview("待确认端口").getByRole("button", { name: "定位端口：10 MHz In/Out" })).toBeInTheDocument();
  }
  if (presetId === "mfli13") {
    expect(preview("输入端口").getByRole("button", { name: "定位端口：Signal Input / V" })).toBeInTheDocument();
    expect(preview("输入端口").getByRole("button", { name: "定位端口：Aux Input / V" })).toBeInTheDocument();
    expect(editPort(3).getByLabelText("当前记录模式（仅档案）")).toHaveValue("");
    expect(preview("待确认端口").getByRole("button", { name: "定位端口：10 MHz" })).toBeInTheDocument();
  }
  await saveAll();
  const saved = structuredClone(stored.devices[0]);
  expect(saved.device_library_id).toBeNull();
  expect(saved.ports[0].id).not.toBe(preset.ports[0].id);
  expect(saved.ports.map(item => ({ ...item, id: "" , modes: item.modes.map(mode => ({ ...mode, id: "" })) }))).toEqual(
    preset.ports.map(item => ({ ...item, id: "", selected_mode: null, modes: item.modes.map(mode => ({ ...mode, id: "" })) })),
  );
  fireEvent.click(screen.getByRole("button", { name: "重新载入" })); await ready();
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "重命名设备" } });
  fireEvent.change(editPort().getByLabelText("端口名称"), { target: { value: "重命名端口" } });
  await saveAll();
  expect(stored.devices[0].id).toBe(saved.id);
  expect(stored.devices[0].ports[0].id).toBe(saved.ports[0].id);
  expect(stored.devices[0].ports[0].modes.map(mode => mode.id)).toEqual(saved.ports[0].modes.map(mode => mode.id));
});

it("库候选多选按稳定库ID各建一档，未知型号空白，重复来源禁用，单设备预置不覆盖旧档", async () => {
  stored.devices = [device("old", "手工已有档案", [port("old-port", "手工端口", "inout")])];
  await openPage(); await ready();
  fireEvent.click(screen.getByRole("checkbox", { name: /主信号源/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /锁相放大器/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /分束器/ }));
  fireEvent.click(screen.getByRole("button", { name: "为所选设备建档" }));
  expect(screen.getByRole("checkbox", { name: /主信号源/ })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: /锁相放大器/ })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: /分束器/ })).toBeDisabled();
  await saveAll();
  expect(stored.devices).toHaveLength(4);
  expect(stored.devices[0]).toEqual(device("old", "手工已有档案", [port("old-port", "手工端口", "inout")]));
  expect(stored.devices.map(item => item.device_library_id)).toEqual([null, "library-generator", "library-lockin", "library-other"]);
  expect(stored.devices[2].ports.map(item => item.name)).toEqual(["Signal Input 1"]);
  expect(stored.devices[3].ports).toEqual([]);
  expect(JSON.stringify(puts().at(-1)?.body)).not.toContain("dev-not-library-key");
  fireEvent.change(screen.getByLabelText("新设备预置"), { target: { value: "dg900" } });
  fireEvent.click(screen.getByRole("button", { name: "新建独立设备" }));
  await saveAll();
  expect(stored.devices).toHaveLength(5);
  expect(stored.devices[0].ports[0].name).toBe("手工端口");
  expect(stored.devices[4].model).toBe("DG900");
});

it("旧双向物理端口和模式只回显禁选历史项并归待确认，未编辑端口原样保存重载", async () => {
  const legacyPorts: PhysicalPort[] = [
    { ...port("legacy-port", "旧光口", "inout"), group: "旧面板", signal_type: "optical", connector: "旧接口", purpose: "保留人工记录" },
    { ...port("legacy-switch", "旧功能口", "switchable"), modes: [
      { id: "legacy-mode", name: "旧功能", direction: "inout" },
      { id: "one-way-mode", name: "输入字样不决定方向", direction: "out" },
    ], selected_mode: "legacy-mode" },
  ];
  stored.devices = [device("legacy-device", "旧设备", structuredClone(legacyPorts))];
  const originalPorts = JSON.stringify(legacyPorts);
  await openPage(); await ready();
  expect(screen.getByRole("button", { name: "保存全部档案" })).toBeDisabled();
  const cleanUnload = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(cleanUnload); expect(cleanUnload.defaultPrevented).toBe(false);
  expect(puts()).toHaveLength(0);
  const physicalDirection = editPort().getByLabelText("端口方向");
  expect(physicalDirection).toHaveValue("inout");
  expect(within(physicalDirection).getByRole("option", { name: "历史双向记录（请确认单向）", selected: true })).toBeDisabled();
  const modeDirection = editPort(2).getAllByLabelText("模式方向")[0];
  expect(modeDirection).toHaveValue("inout");
  expect(within(modeDirection).getByRole("option", { name: "历史双向记录（请确认单向）", selected: true })).toBeDisabled();
  expect(editPort(2).getByLabelText("当前记录模式（仅档案）")).toHaveValue("legacy-mode");
  expect(within(editPort(2).getByLabelText("端口方向")).queryByRole("option", { name: /历史双向/ })).not.toBeInTheDocument();
  expect(within(editPort(2).getAllByLabelText("模式方向")[1]).queryByRole("option", { name: /历史双向/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "双向端口" })).not.toBeInTheDocument();
  expect(preview("待确认端口").getByRole("button", { name: "定位端口：旧光口" })).toHaveTextContent("历史双向记录");
  expect(preview("待确认端口").getByRole("button", { name: "定位端口：旧功能口" })).toHaveTextContent("记录模式：旧功能");
  expect(preview("输入端口").queryByRole("button")).not.toBeInTheDocument();
  expect(preview("输出端口").queryByRole("button")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "只改设备名称" } });
  await saveAll();
  expect(puts()[0].body).toEqual({ base_revision: "ports-r1", devices: [device("legacy-device", "只改设备名称", legacyPorts)] });
  expect(JSON.stringify(puts()[0].body?.devices[0].ports)).toBe(originalPorts);
  fireEvent.click(screen.getByRole("button", { name: "重新载入" })); await ready();
  expect(editPort().getByLabelText("端口方向")).toHaveValue("inout");
  expect(editPort(2).getAllByLabelText("模式方向")[0]).toHaveValue("inout");
  expect(editPort(2).getByLabelText("当前记录模式（仅档案）")).toHaveValue("legacy-mode");
  expect(screen.getByRole("button", { name: "保存全部档案" })).toBeDisabled();
});

it.each(["in", "out"] as const)("旧双向记录仅显式转为 %s，不删未编辑模式且保留全部稳定ID与所选功能", async direction => {
  const legacyPorts: PhysicalPort[] = [port("legacy-port", "旧物理口", "inout"), {
    ...port("legacy-switch", "旧可切换口", "switchable"), modes: [
      { id: "selected-legacy", name: "当前旧功能", direction: "inout" },
      { id: "untouched-legacy", name: "未编辑旧功能", direction: "inout" },
      { id: "output-mode", name: "保留单向功能", direction: "out" },
    ], selected_mode: "selected-legacy",
  }];
  stored.devices = [device("legacy-device", "旧档案", structuredClone(legacyPorts))];
  await openPage(); await ready();
  fireEvent.change(editPort().getByLabelText("端口方向"), { target: { value: direction } });
  expect(within(editPort().getByLabelText("端口方向")).getAllByRole("option").map(option => (option as HTMLOptionElement).value)).toEqual(["", "in", "out", "switchable"]);
  expect(editPort(2).getAllByLabelText("模式方向")[0]).toHaveValue("inout");
  expect(preview("待确认端口").getByRole("button", { name: "定位端口：旧可切换口" })).toBeInTheDocument();
  fireEvent.change(editPort(2).getAllByLabelText("模式方向")[0], { target: { value: direction } });
  expect(within(editPort(2).getAllByLabelText("模式方向")[0]).getAllByRole("option").map(option => (option as HTMLOptionElement).value)).toEqual(["in", "out"]);
  expect(editPort(2).getAllByLabelText("模式方向")[1]).toHaveValue("inout");
  expect(within(editPort(2).getAllByLabelText("模式方向")[1]).getByRole("option", { name: "历史双向记录（请确认单向）", selected: true })).toBeDisabled();
  expect(editPort(2).getByLabelText("当前记录模式（仅档案）")).toHaveValue("selected-legacy");
  const confirmed = preview(direction === "in" ? "输入端口" : "输出端口");
  expect(confirmed.getByRole("button", { name: "定位端口：旧物理口" })).toBeInTheDocument();
  expect(confirmed.getByRole("button", { name: "定位端口：旧可切换口" })).toHaveTextContent("记录模式：当前旧功能");
  expect(preview("待确认端口").queryByRole("button")).not.toBeInTheDocument();
  await saveAll();
  expect(puts()[0].body?.devices).toEqual([device("legacy-device", "旧档案", [
    { ...legacyPorts[0], direction },
    { ...legacyPorts[1], modes: [{ id: "selected-legacy", name: "当前旧功能", direction }, ...legacyPorts[1].modes.slice(1)] },
  ])]);
  fireEvent.click(screen.getByRole("button", { name: "重新载入" })); await ready();
  expect(editPort().getByLabelText("端口方向")).toHaveValue(direction);
  expect(editPort(2).getAllByLabelText("模式方向")[0]).toHaveValue(direction);
  expect(editPort(2).getAllByLabelText("模式方向")[1]).toHaveValue("inout");
  expect(editPort(2).getByLabelText("当前记录模式（仅档案）")).toHaveValue("selected-legacy");
});

it("光学空白设备可增改删端口，新端口无双向选项，预览按确认单向排列且点击聚焦编辑", async () => {
  await openPage(); await ready();
  fireEvent.click(screen.getByRole("button", { name: "新建独立设备" }));
  expect(screen.getByLabelText("设备型号")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "无源分束器" } });
  fireEvent.click(screen.getByRole("button", { name: "添加端口" }));
  expect(within(editPort().getByLabelText("端口方向")).getAllByRole("option").map(option => (option as HTMLOptionElement).value)).toEqual(["", "in", "out", "switchable"]);
  const opticalName = "很长的光学入口名称可以换行而不应被截断";
  fireEvent.change(editPort().getByLabelText("端口名称"), { target: { value: opticalName } });
  fireEvent.change(editPort().getByLabelText("端口分组"), { target: { value: "光路" } });
  fireEvent.change(editPort().getByLabelText("物理接口"), { target: { value: "自由空间" } });
  fireEvent.change(editPort().getByLabelText("端口用途"), { target: { value: "入射光" } });
  expect(preview("待确认端口").getByText("光/电待确认")).toBeInTheDocument();
  fireEvent.change(editPort().getByLabelText("光电类型"), { target: { value: "optical" } });
  fireEvent.change(editPort().getByLabelText("端口方向"), { target: { value: "in" } });
  const diagramPort = preview("输入端口").getByRole("button", { name: `定位端口：${opticalName}` });
  expect(within(diagramPort).getByText("光学")).toBeInTheDocument();
  fireEvent.click(diagramPort);
  expect(editPort().getByLabelText("端口名称")).toHaveFocus();
  fireEvent.click(screen.getByRole("button", { name: "添加端口" }));
  fireEvent.change(editPort(2).getByLabelText("端口名称"), { target: { value: "待确认光口" } });
  fireEvent.change(editPort(2).getByLabelText("光电类型"), { target: { value: "optical" } });
  expect(within(editPort(2).getByLabelText("端口方向")).queryByRole("option", { name: /双向/ })).not.toBeInTheDocument();
  expect(preview("待确认端口").getByRole("button", { name: "定位端口：待确认光口" })).toHaveTextContent("光学");
  fireEvent.change(editPort(2).getByLabelText("端口方向"), { target: { value: "out" } });
  expect(preview("输出端口").getByRole("button", { name: "定位端口：待确认光口" })).toBeInTheDocument();
  expect(preview("待确认端口").queryByRole("button")).not.toBeInTheDocument();
  await saveAll();
  const id = stored.devices[0].ports[0].id;
  fireEvent.click(editPort(2).getByRole("button", { name: "删除端口" }));
  fireEvent.change(editPort().getByLabelText("端口名称"), { target: { value: "入口改名" } });
  await saveAll();
  expect(stored.devices[0].ports).toEqual([{ ...port(id, "入口改名", "in"), group: "光路", connector: "自由空间", purpose: "入射光", signal_type: "optical" }]);
  fireEvent.click(screen.getByRole("button", { name: "重新载入" })); await ready();
  expect(editPort().getByLabelText("端口名称")).toHaveValue("入口改名");
});

it("模式名称方向编辑与选择实时投影，删除选中模式恢复未知，固定方向清空模式且无硬件设置", async () => {
  stored.devices = [device("arbitrary", "自定义黑盒", [{ ...port("custom", "自定义切换口", "switchable"), modes: [
    { id: "mode-a", name: "参考接收", direction: "in" }, { id: "mode-b", name: "参考输出", direction: "out" },
  ] }])];
  await openPage(); await ready();
  const name = "定位端口：自定义切换口";
  expect(preview("待确认端口").getByRole("button", { name })).toBeInTheDocument();
  fireEvent.change(editPort().getByLabelText("当前记录模式（仅档案）"), { target: { value: "mode-a" } });
  expect(preview("输入端口").getByRole("button", { name })).toBeInTheDocument();
  fireEvent.change(editPort().getByLabelText("当前记录模式（仅档案）"), { target: { value: "mode-b" } });
  expect(preview("输出端口").getByRole("button", { name })).toBeInTheDocument();
  const second = within(editPort().getByRole("group", { name: "模式 2" }));
  fireEvent.change(second.getByLabelText("模式名称"), { target: { value: "名称含输出仍按定义输入" } });
  fireEvent.change(second.getByLabelText("模式方向"), { target: { value: "in" } });
  expect(within(second.getByLabelText("模式方向")).getAllByRole("option").map(option => (option as HTMLOptionElement).value)).toEqual(["in", "out"]);
  expect(preview("输入端口").getByRole("button", { name })).toHaveTextContent("记录模式：名称含输出仍按定义输入");
  await saveAll();
  expect(stored.devices[0].ports[0].modes[1].id).toBe("mode-b");
  fireEvent.click(second.getByRole("button", { name: "删除模式" }));
  expect(editPort().getByLabelText("当前记录模式（仅档案）")).toHaveValue("");
  expect(preview("待确认端口").getByRole("button", { name })).toBeInTheDocument();
  expect(editPort().getByRole("button", { name: "删除模式" })).toBeDisabled();
  fireEvent.click(editPort().getByRole("button", { name: "添加模式" }));
  expect(editPort().getAllByLabelText("模式名称")).toHaveLength(2);
  for (const direction of editPort().getAllByLabelText("模式方向")) {
    expect(within(direction).getAllByRole("option").map(option => (option as HTMLOptionElement).value)).toEqual(["in", "out"]);
  }
  fireEvent.change(editPort().getByLabelText("端口方向"), { target: { value: "out" } });
  expect(editPort().queryByLabelText("当前记录模式（仅档案）")).not.toBeInTheDocument();
  await saveAll();
  expect(stored.devices[0].ports[0]).toMatchObject({ direction: "out", modes: [], selected_mode: null });
});

it("新建可切换口的功能从服务端模式定义复制，MOD含AM/FM且选择按单向投影并保存重载回显", async () => {
  const functionalPreset: DevicePortsResponse["presets"][number] = {
    id: "dg900-functions", name: "DG900 功能预置", model: "DG900", ports: [{
      ...port("ch1_multi", "CH1 Multi", "switchable"), purpose: "同步输出或触发 / 门控 / MOD（AM / FM）输入", modes: [
        { id: "sync", name: "同步", direction: "out" },
        { id: "trigger", name: "触发", direction: "in" },
        { id: "gate", name: "门控", direction: "in" },
        { id: "am", name: "AM", direction: "in" },
        { id: "fm", name: "FM", direction: "in" },
      ],
    }],
  };
  stored.presets = [structuredClone(functionalPreset)];
  await openPage(); await ready();
  fireEvent.change(screen.getByLabelText("新设备预置"), { target: { value: "dg900-functions" } });
  fireEvent.click(screen.getByRole("button", { name: "新建独立设备" }));
  expect(editPort().getByLabelText("端口方向")).toHaveValue("switchable");
  expect(within(editPort().getByLabelText("端口方向")).getAllByRole("option").map(option => (option as HTMLOptionElement).value)).toEqual(["", "in", "out", "switchable"]);
  expect(editPort().getAllByLabelText("模式名称").map(input => (input as HTMLInputElement).value)).toEqual(["同步", "触发", "门控", "AM", "FM"]);
  for (const direction of editPort().getAllByLabelText("模式方向")) {
    expect(within(direction).getAllByRole("option").map(option => (option as HTMLOptionElement).value)).toEqual(["in", "out"]);
  }
  expect(editPort().getAllByLabelText("模式方向").map(select => (select as HTMLSelectElement).value)).toEqual(["out", "in", "in", "in", "in"]);
  const selection = editPort().getByLabelText("当前记录模式（仅档案）");
  expect(selection).toHaveValue("");
  expect(preview("待确认端口").getByRole("button", { name: "定位端口：CH1 Multi" })).toBeInTheDocument();
  const copiedModeIds = within(selection).getAllByRole("option").slice(1).map(option => (option as HTMLOptionElement).value);
  expect(new Set(copiedModeIds).size).toBe(5);
  expect(copiedModeIds.every(id => !["sync", "trigger", "gate", "am", "fm"].includes(id))).toBe(true);
  for (const [index, name] of ["同步", "触发", "门控", "AM", "FM"].entries()) {
    fireEvent.change(selection, { target: { value: copiedModeIds[index] } });
    expect(selection).toHaveValue(copiedModeIds[index]);
    expect(within(selection).getByRole("option", { name, selected: true })).toBeInTheDocument();
    expect(preview(index === 0 ? "输出端口" : "输入端口").getByRole("button", { name: "定位端口：CH1 Multi" })).toHaveTextContent(`记录模式：${name}`);
    expect(preview("待确认端口").queryByRole("button")).not.toBeInTheDocument();
  }
  await saveAll();
  const savedPort = structuredClone(stored.devices[0].ports[0]);
  expect(savedPort.id).not.toBe("ch1_multi");
  expect(savedPort).toMatchObject({ direction: "switchable", selected_mode: copiedModeIds[4] });
  expect(savedPort.modes.map(mode => mode.id)).toEqual(copiedModeIds);
  expect(savedPort.modes.map(({ name, direction }) => ({ name, direction }))).toEqual([
    { name: "同步", direction: "out" }, { name: "触发", direction: "in" }, { name: "门控", direction: "in" }, { name: "AM", direction: "in" }, { name: "FM", direction: "in" },
  ]);
  expect(stored.presets).toEqual([functionalPreset]);
  fireEvent.click(screen.getByRole("button", { name: "重新载入" })); await ready();
  expect(editPort().getByLabelText("当前记录模式（仅档案）")).toHaveValue(copiedModeIds[4]);
  expect(within(editPort().getByLabelText("当前记录模式（仅档案）")).getByRole("option", { name: "FM", selected: true })).toBeInTheDocument();
  expect(preview("输入端口").getByRole("button", { name: "定位端口：CH1 Multi" })).toHaveTextContent("记录模式：FM");
  expect(screen.getByRole("button", { name: "保存全部档案" })).toBeDisabled();
  const fm = within(editPort().getByRole("group", { name: "模式 5" }));
  fireEvent.change(fm.getByLabelText("模式名称"), { target: { value: "名称含输出仍是输入功能" } });
  expect(preview("输入端口").getByRole("button", { name: "定位端口：CH1 Multi" })).toHaveTextContent("记录模式：名称含输出仍是输入功能");
  await saveAll();
  expect(stored.devices[0].ports[0]).toEqual({ ...savedPort, modes: [...savedPort.modes.slice(0, 4), { ...savedPort.modes[4], name: "名称含输出仍是输入功能" }] });
});

it("全档案dirty保护离开和同页切换、新建及删除，确认切换保留其它设备草稿，放弃还原全部", async () => {
  stored.devices = [device("a", "设备甲"), device("b", "设备乙")];
  await openPage(); await ready();
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "设备甲草稿" } });
  fireEvent.click(screen.getByRole("link", { name: "返回仪器控制" }));
  await waitFor(() => expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("设备端口档案")));
  expect(screen.getByLabelText("设备名称")).toHaveValue("设备甲草稿");
  const unload = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(unload); expect(unload.defaultPrevented).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: /设备乙.*型号待填/ }));
  expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining("切换设备将保留全部未保存修改"));
  expect(screen.getByLabelText("设备名称")).toHaveValue("设备甲草稿");
  fireEvent.click(screen.getByRole("button", { name: "新建独立设备" }));
  expect(screen.getByLabelText("设备名称")).toHaveValue("设备甲草稿");
  expect(screen.queryByRole("button", { name: /新设备.*型号待填/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "删除设备" }));
  expect(screen.getByLabelText("设备名称")).toHaveValue("设备甲草稿");
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: /设备乙.*型号待填/ }));
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "设备乙草稿" } });
  fireEvent.click(screen.getByRole("button", { name: /设备甲草稿.*型号待填/ }));
  expect(screen.getByLabelText("设备名称")).toHaveValue("设备甲草稿");
  await saveAll();
  expect(stored.devices.map(item => item.name)).toEqual(["设备甲草稿", "设备乙草稿"]);
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "甲二次草稿" } });
  fireEvent.click(screen.getByRole("button", { name: /设备乙草稿.*型号待填/ }));
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "乙二次草稿" } });
  fireEvent.click(screen.getByRole("button", { name: "放弃全部草稿" }));
  expect(screen.getByLabelText("设备名称")).toHaveValue("设备乙草稿");
  fireEvent.click(screen.getByRole("button", { name: /设备甲草稿.*型号待填/ }));
  expect(screen.getByLabelText("设备名称")).toHaveValue("设备甲草稿");
  expect(screen.getByRole("button", { name: "保存全部档案" })).toBeDisabled();
  const cleanUnload = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(cleanUnload); expect(cleanUnload.defaultPrevented).toBe(false);
  vi.mocked(window.confirm).mockClear();
  fireEvent.click(screen.getByRole("link", { name: "返回仪器控制" }));
  expect(await screen.findByRole("heading", { name: "仪器控制", level: 1 })).toBeVisible();
  expect(window.confirm).not.toHaveBeenCalled();
});

it("确认新建保留全档案草稿，确认删除只影响选中设备，重新载入取消不读且失败不覆盖草稿", async () => {
  stored.devices = [device("a", "已有设备")];
  await openPage(); await ready();
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "已有设备草稿" } });
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "新建独立设备" }));
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "独立设备草稿" } });
  fireEvent.click(screen.getByRole("button", { name: /已有设备草稿.*型号待填/ }));
  expect(screen.getByLabelText("设备名称")).toHaveValue("已有设备草稿");
  fireEvent.click(screen.getByRole("button", { name: /独立设备草稿.*型号待填/ }));
  fireEvent.click(screen.getByRole("button", { name: "删除设备" }));
  expect(screen.getByLabelText("设备名称")).toHaveValue("已有设备草稿");
  expect(screen.queryByRole("button", { name: /独立设备草稿.*型号待填/ })).not.toBeInTheDocument();
  const reads = () => requests.filter(item => item.path === "/api/device-ports" && item.method === "GET").length;
  const before = reads();
  vi.mocked(window.confirm).mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: "重新载入" }));
  expect(reads()).toBe(before);
  expect(screen.getByLabelText("设备名称")).toHaveValue("已有设备草稿");
  vi.mocked(window.confirm).mockReturnValue(true); readFailure = true;
  fireEvent.click(screen.getByRole("button", { name: "重新载入" }));
  expect(await screen.findByText(/重新载入失败，现有草稿未被覆盖/)).toBeInTheDocument();
  expect(screen.getByText(/可继续查看和编辑草稿，但保存已暂停/)).toBeVisible();
  expect(screen.getByLabelText("设备名称")).toHaveValue("已有设备草稿");
  expect(screen.getByLabelText("设备名称")).toBeEnabled();
  expect(screen.getByRole("button", { name: "保存全部档案" })).toBeDisabled();
  readFailure = false; stored = { ...stored, revision: "ports-external", devices: [device("a", "服务端新设备")] };
  fireEvent.click(screen.getByRole("button", { name: "重试读取档案" }));
  await waitFor(() => expect(screen.getByLabelText("设备名称")).toHaveValue("服务端新设备"));
  expect(screen.getByRole("button", { name: "保存全部档案" })).toBeDisabled();
  expect(screen.getByText(/修订 ports-external/)).toBeInTheDocument();
});

it("JSON草稿回到基线立即变clean，名称空值阻止保存，确认离开丢弃草稿", async () => {
  stored.devices = [device("a", "原始名称", [port("p", "原始端口")])];
  await openPage(); await ready();
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "" } });
  expect(screen.getByText("设备名称不能为空。")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存全部档案" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "原始名称" } });
  expect(screen.getByText("全部档案已保存")).toBeInTheDocument();
  fireEvent.change(editPort().getByLabelText("端口名称"), { target: { value: "" } });
  expect(screen.getByRole("button", { name: "保存全部档案" })).toBeDisabled();
  fireEvent.change(editPort().getByLabelText("端口名称"), { target: { value: "原始端口" } });
  expect(screen.getByText("全部档案已保存")).toBeInTheDocument();
  fireEvent.change(editPort().getByLabelText("端口用途"), { target: { value: "未保存用途" } });
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.click(screen.getByRole("link", { name: "返回仪器控制" }));
  expect(await screen.findByRole("heading", { name: "仪器控制", level: 1 })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "设备端口建档" })); await ready();
  expect(editPort().getByLabelText("端口用途")).toHaveValue("");
  expect(puts()).toHaveLength(0);
});

it("保存期间禁用所有草稿编辑和破坏性动作，响应成功不覆盖新草稿", async () => {
  stored.devices = [device("a", "已有设备", [port("p", "已有端口")]), device("b", "第二设备")];
  let finish!: () => void;
  pendingSave = new Promise<void>(resolve => { finish = resolve; });
  await openPage(); await ready();
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "保存前名称" } });
  fireEvent.click(screen.getByRole("button", { name: "保存全部档案" }));
  expect(screen.getByRole("button", { name: "正在保存…" })).toBeDisabled();
  for (const control of [screen.getByLabelText("设备名称"), screen.getByLabelText("设备型号"), editPort().getByLabelText("端口名称"),
    editPort().getByLabelText("端口方向"), screen.getByLabelText("新设备预置"), screen.getByRole("button", { name: "新建独立设备" }),
    screen.getByRole("button", { name: "删除设备" }), screen.getByRole("button", { name: "添加端口" }),
    screen.getByRole("button", { name: /第二设备.*型号待填/ }), screen.getByRole("button", { name: "重新载入" }),
    screen.getByRole("button", { name: "放弃全部草稿" }), screen.getByRole("checkbox", { name: /主信号源/ })]) expect(control).toBeDisabled();
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "不应接受" } });
  expect(screen.getByLabelText("设备名称")).toHaveValue("保存前名称");
  await act(async () => finish());
  await screen.findByText("全部设备端口档案已保存；没有连接硬件或下发任何设置。");
  expect(stored.devices[0].name).toBe("保存前名称");
  expect(screen.getByLabelText("设备名称")).toBeEnabled();
});

it.each(["error", "conflict"] as const)("PUT %s失败保留全部草稿和基础修订，冲突重新载入必须确认", async failure => {
  stored.devices = [device("a", "原始设备", [port("p", "原始端口")])];
  await openPage(); await ready();
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "失败后保留草稿" } });
  fireEvent.change(editPort().getByLabelText("端口用途"), { target: { value: "保留用途" } });
  saveFailure = failure;
  fireEvent.click(screen.getByRole("button", { name: "保存全部档案" }));
  if (failure === "conflict") {
    expect(await screen.findByText(/保存冲突（409）/)).toBeInTheDocument();
    const reads = requests.filter(item => item.path === "/api/device-ports" && item.method === "GET").length;
    fireEvent.click(screen.getByRole("button", { name: "确认重新载入最新档案" }));
    expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining("丢弃全部设备的未保存修改"));
    expect(requests.filter(item => item.path === "/api/device-ports" && item.method === "GET")).toHaveLength(reads);
  } else expect(await screen.findByText(/保存失败，草稿与基础修订已保留/)).toBeInTheDocument();
  expect(screen.getByLabelText("设备名称")).toHaveValue("失败后保留草稿");
  expect(editPort().getByLabelText("端口用途")).toHaveValue("保留用途");
  expect(screen.getByText(/修订 ports-r1/)).toBeInTheDocument();
  expect(stored.devices[0].name).toBe("原始设备");
  fireEvent.click(screen.getByRole("button", { name: "保存全部档案" }));
  await waitFor(() => expect(puts()).toHaveLength(2));
  expect(puts().map(item => item.body?.base_revision)).toEqual(["ports-r1", "ports-r1"]);
  await screen.findByText(failure === "conflict" ? /保存冲突（409）/ : /保存失败，草稿与基础修订已保留/);
  if (failure === "conflict") {
    stored = { ...stored, revision: "ports-new", devices: [device("a", "其它窗口的档案")] };
    vi.mocked(window.confirm).mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "确认重新载入最新档案" }));
    await waitFor(() => expect(screen.getByLabelText("设备名称")).toHaveValue("其它窗口的档案"));
    expect(screen.getByText(/修订 ports-new/)).toBeInTheDocument();
  } else {
    saveFailure = ""; await saveAll();
    expect(puts().at(-1)?.body?.base_revision).toBe("ports-r1");
    expect(stored.devices[0].name).toBe("失败后保留草稿");
  }
});

it("初始档案读取失败不能以空档保存或新建，明确重试成功才开放编辑", async () => {
  readFailure = true;
  await openPage();
  expect(await screen.findByText(/端口档案读取失败，已禁止新建和保存/)).toBeInTheDocument();
  expect(screen.getByText("档案尚未载入")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存全部档案" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "新建独立设备" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "为所选设备建档" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "保存全部档案" }));
  expect(puts()).toHaveLength(0);
  readFailure = false; stored.devices = [device("restored", "重试后的既有档案")];
  fireEvent.click(screen.getByRole("button", { name: "重试读取档案" }));
  await ready();
  expect(screen.getByLabelText("设备名称")).toHaveValue("重试后的既有档案");
});

it("库请求失败不妨碍既有档案读取与保存，只禁用库建档并保留警告", async () => {
  libraryFailure = true;
  stored.devices = [{ ...device("saved", "已有档案"), model: "HF2", device_library_id: "library-lockin" }];
  await openPage(); await ready();
  expect(await screen.findByText(/设备库读取失败：设备库暂不可用/)).toBeInTheDocument();
  expect(screen.getByLabelText("设备名称")).toHaveValue("已有档案");
  expect(screen.getByRole("button", { name: "为所选设备建档" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "新建独立设备" })).toBeEnabled();
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "库失效仍可编辑" } });
  await saveAll();
  expect(screen.getByText(/设备库读取失败/)).toBeInTheDocument();
  expect(stored.devices[0].device_library_id).toBe("library-lockin");
});

it("来源库缺失或型号变化只警告，不删档案、不重新生成端口", async () => {
  stored.devices = [
    { ...device("missing", "来源丢失档案", [port("m-port", "手工光口", "in")]), device_library_id: "removed-library-key", model: "原型号" },
    { ...device("changed", "型号变更档案", [port("c-port", "保留端口", "inout")]), device_library_id: "library-generator", model: "旧型号" },
  ];
  await openPage(); await ready();
  expect(screen.getByText(/来源设备已不在设备库中/)).toBeInTheDocument();
  expect(editPort().getByLabelText("端口名称")).toHaveValue("手工光口");
  fireEvent.click(screen.getByRole("button", { name: /型号变更档案.*旧型号/ }));
  expect(screen.getByText(/来源设备库型号现为 DG4000/)).toBeInTheDocument();
  expect(screen.getByLabelText("设备型号")).toHaveValue("旧型号");
  expect(editPort().getByLabelText("端口名称")).toHaveValue("保留端口");
  fireEvent.change(editPort().getByLabelText("端口用途"), { target: { value: "核对前保留" } });
  await saveAll();
  expect(stored.devices).toHaveLength(2);
  expect(stored.devices[0].ports[0].id).toBe("m-port");
  expect(stored.devices[1].ports).toHaveLength(1);
  expect(stored.devices[1].ports[0].id).toBe("c-port");
  expect(stored.devices[1].device_library_id).toBe("library-generator");
});
