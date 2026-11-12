import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { JobActivityProvider } from "../JobActivity";
import { PhysicalMappingsView } from "../instruments/PhysicalMappingsView";
import { WiringEditor } from "../wiring/WiringEditor";
import type { WiringDocument } from "../wiring/wiringTypes";
import { SetupProvider, type SetupRegistry } from "./SetupContext";
import { SetupSwitcher } from "./SetupSwitcher";

let registry: SetupRegistry;
const response = (data: unknown) => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
const requests: Array<{ path: string; method: string }> = [];
const document = (id: string): WiringDocument => ({
  schema_version: 1, revision: "same-revision", setup_id: id,
  site: { nodes: [{ device: { id: `device-${id}`, name: `${id} 现场器件`, model: "人工器件", device_library_id: null, ports: [] }, x: 60, y: 60 }], edges: [] },
  targets: {}, diagnostics: { site: [], targets: {} }, mapping_groups: [], follow_status: { site: [], targets: {} },
});
let delayedRead: Promise<Response> | undefined;
function renderPage(content: React.ReactNode) {
  return render(<MemoryRouter><JobActivityProvider><SetupProvider><SetupSwitcher />{content}</SetupProvider></JobActivityProvider></MemoryRouter>);
}
beforeEach(() => {
  registry = { revision: "registry-1", active: "a", stored: true, setups: [{ id: "a", label: "装置甲", archived: false }, { id: "b", label: "装置乙", archived: false }] };
  requests.length = 0; delayedRead = undefined;
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input); const method = options?.method ?? "GET"; requests.push({ path, method });
    if (path === "/api/health") return response({ status: "ok", hardware_busy: false });
    if (path === "/api/jobs") return response([]);
    if (path === "/api/setups" && method === "GET") return response(registry);
    if (path === "/api/setups/active" && method === "PUT") {
      registry = { ...registry, revision: `${registry.revision}-next`, active: JSON.parse(String(options?.body)).setup_id }; return response(registry);
    }
    if (path === "/api/device-ports") return response({ schema_version: 1, revision: "ports", devices: [], presets: [] });
    if (path === "/api/experiments") return response([]);
    if (path.startsWith("/api/experiments/")) return response({ id: "experiment", wiring_notes: [], required_devices: [], required_mapping_keys: [] });
    if (path === "/api/wiring/revision") return response({ revision: "same-revision" });
    if (path === "/api/wiring" && method === "GET") return delayedRead ?? response(document(registry.active));
    if (path === "/api/device-library") return response({ schema_version: 1, revision: "library", network_resources: [], devices: {} });
    if (path === "/api/physical-mappings") return response({ schema_version: 2, revision: "mapping", mapping: {}, constraints: { colocation_groups: {}, shared_channel_groups: {} }, setup_id: registry.active, setup_label: registry.setups.find(setup => setup.id === registry.active)!.label });
    throw new Error(`未预期的接口：${method} ${path}`);
  }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function switchTo(label = "装置乙") {
  fireEvent.click(await screen.findByRole("button", { name: /当前装置：/ }));
  fireEvent.click(await screen.findByRole("button", { name: label }));
  await screen.findByRole("button", { name: new RegExp(`当前装置：${label}`) });
}
const wiringReads = () => requests.filter(item => item.path === "/api/wiring" && item.method === "GET");
const warning = "已切换装置，当前草稿属于原装置；请先保存或放弃后重新载入";

it("物理量映射标注响应装置，切换后按 epoch 重载", async () => {
  renderPage(<PhysicalMappingsView />);
  expect(await screen.findByText("正在编辑：装置甲")).toBeInTheDocument();
  await switchTo();
  expect(await screen.findByText("正在编辑：装置乙")).toBeInTheDocument();
  expect(requests.filter(item => item.path === "/api/physical-mappings")).toHaveLength(2);
});
it("接线图无草稿时重载，即使两个装置的修订相同", async () => {
  renderPage(<WiringEditor />);
  await screen.findByText("装置：装置甲");
  await switchTo();
  expect(await screen.findByText("装置：装置乙")).toBeInTheDocument();
  expect(wiringReads()).toHaveLength(2);
  expect(screen.queryByText(warning, { exact: false })).not.toBeInTheDocument();
});
it.each([undefined, "experiment"])("接线草稿跨装置保留、禁止保存和后台覆盖，确认重载才采用新装置（%s）", async experimentId => {
  renderPage(<WiringEditor experimentId={experimentId} experimentTitle="测试实验" />);
  await screen.findByText("装置：装置甲");
  await waitFor(() => expect(screen.getByRole("button", { name: "添加空白图内器件" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "添加空白图内器件" }));
  const saveName = experimentId ? "保存当前实验目标" : "保存全部接线图";
  expect(screen.getByRole("button", { name: saveName })).toBeEnabled();
  await switchTo();
  expect(screen.getByText(warning, { exact: false })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "移动器件：新器件" })).toBeInTheDocument();
  expect(screen.getByText("装置：装置甲")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: saveName })).toBeDisabled();
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  if (!experimentId) fireEvent.click(screen.getByRole("button", { name: "检查用途与接线图更新" }));
  expect(wiringReads()).toHaveLength(1);
  expect(requests.filter(item => item.method === "PUT" && item.path === "/api/wiring")).toEqual([]);
  vi.mocked(window.confirm).mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: "确认重新载入当前装置" }));
  expect(screen.getByRole("button", { name: "移动器件：新器件" })).toBeInTheDocument();
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "确认重新载入当前装置" }));
  await screen.findByText("装置：装置乙");
  expect(screen.queryByRole("button", { name: "移动器件：新器件" })).not.toBeInTheDocument();
  expect(wiringReads()).toHaveLength(2);
});
it("放弃旧草稿后仍保持归属隔离，直到显式重新载入", async () => {
  renderPage(<WiringEditor />);
  await screen.findByText("装置：装置甲");
  fireEvent.click(screen.getByRole("button", { name: "添加空白图内器件" }));
  await switchTo();
  fireEvent.click(screen.getByRole("button", { name: "放弃全部草稿" }));
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(screen.getByText(warning, { exact: false })).toBeInTheDocument();
  expect(screen.getByText("装置：装置甲")).toBeInTheDocument();
  expect(wiringReads()).toHaveLength(1);
});
it("切回原装置保留草稿并恢复原装置保存", async () => {
  const fetchBefore = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, options) => {
    if (String(input) === "/api/wiring" && options?.method === "PUT") {
      expect(registry.active).toBe("a");
      return response({ ...document("a"), ...JSON.parse(String(options.body)), revision: "saved-original" });
    }
    return fetchBefore(input, options);
  });
  renderPage(<WiringEditor />);
  await screen.findByText("装置：装置甲");
  fireEvent.click(screen.getByRole("button", { name: "添加空白图内器件" }));
  await switchTo();
  fireEvent.click(screen.getByRole("button", { name: "当前装置：装置乙" }));
  await switchTo("装置甲");
  expect(screen.getByRole("button", { name: "移动器件：新器件" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存全部接线图" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "保存全部接线图" }));
  await screen.findByText("全部接线图已保存。");
  expect(wiringReads()).toHaveLength(1);
});

it("切换前迟到的接线 GET 不覆盖新装置", async () => {
  let resolve!: (value: Response) => void;
  delayedRead = new Promise<Response>(done => { resolve = done; });
  renderPage(<WiringEditor />);
  await screen.findByRole("button", { name: /当前装置：装置甲/ });
  delayedRead = undefined;
  await switchTo();
  await screen.findByText("装置：装置乙");
  await act(async () => { resolve(response(document("a"))); });
  expect(screen.getByText("装置：装置乙")).toBeInTheDocument();
  expect(screen.queryByText("装置：装置甲")).not.toBeInTheDocument();
});
