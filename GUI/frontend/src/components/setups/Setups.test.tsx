import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobActivityProvider } from "../JobActivity";
import { InstrumentsPage } from "../../pages/InstrumentsPage";
import { SetupProvider, useSetups, type SetupRegistry } from "./SetupContext";
import { SetupSwitcher } from "./SetupSwitcher";
import { SetupsView } from "./SetupsView";

const initial = (): SetupRegistry => ({ revision: "registry-1", active: "default", stored: true, setups: [
  { id: "default", label: "默认装置", archived: false },
  { id: "second", label: "备用装置", archived: false },
  { id: "archived", label: "归档装置", archived: true },
] });
const response = (data: unknown, status = 200, code?: string) => new Response(JSON.stringify(data), {
  status, headers: { "Content-Type": "application/json", ...(code ? { "X-Error-Code": code } : {}) },
});
let registry: SetupRegistry;
let hardwareBusy: boolean;
let failure: "" | "hardware_busy" | "revision_conflict" | "validation";
const requests: Array<{ path: string; method: string; body?: Record<string, unknown> }> = [];
const writes = () => requests.filter(item => item.method !== "GET");
function Epoch() { return <output aria-label="装置重载代数">{useSetups().setupEpoch}</output>; }
function renderSetups(content = <><SetupSwitcher /><SetupsView /><Epoch /></>, path = "/instruments") {
  return render(<MemoryRouter initialEntries={[path]}><JobActivityProvider><SetupProvider>{content}</SetupProvider></JobActivityProvider></MemoryRouter>);
}
beforeEach(() => {
  registry = initial(); hardwareBusy = false; failure = ""; requests.length = 0;
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input); const method = options?.method ?? "GET";
    const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    requests.push({ path, method, body });
    if (path === "/api/health") return response({ status: "ok", hardware_busy: hardwareBusy });
    if (path === "/api/jobs") return response([]);
    if (path === "/api/setups" && method === "GET") return response(registry);
    if (method !== "GET" && path.startsWith("/api/setups")) {
      if (failure === "hardware_busy" || failure === "revision_conflict") return response({ detail: "操作被拒绝" }, 409, failure);
      if (failure === "validation") return response({ detail: [{ msg: "装置名称已存在" }] }, 422);
      expect(body.base_revision).toBe(registry.revision);
      if (path === "/api/setups/active") registry = { ...registry, active: body.setup_id };
      else if (method === "POST") registry = { ...registry, stored: true, setups: [...registry.setups, { id: "created", label: body.label, archived: false }] };
      else registry = { ...registry, setups: registry.setups.map(setup => setup.id === decodeURIComponent(path.split("/").at(-1)!) ? { ...setup, ...(body.label !== undefined ? { label: body.label } : {}), ...(body.archived !== undefined ? { archived: body.archived } : {}) } : setup) };
      registry = { ...registry, revision: `${registry.revision}-next` };
      return response(registry);
    }
    throw new Error(`未预期的接口：${method} ${path}`);
  }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function openMenu() { fireEvent.click(await screen.findByRole("button", { name: /当前装置：默认装置/ })); }

describe("顶栏装置切换器", () => {
  it("列出未归档装置、标记当前，切换成功更新共享标签与 epoch", async () => {
    renderSetups(<><SetupSwitcher /><Epoch /></>);
    await openMenu();
    const list = screen.getByLabelText("装置列表");
    expect(within(list).queryByText("归档装置")).not.toBeInTheDocument();
    expect(within(list).getByRole("button", { name: /默认装置/ })).toHaveAttribute("aria-current", "true");
    expect(within(list).getByRole("link", { name: "管理装置…" })).toHaveAttribute("href", "/instruments?view=setups");
    fireEvent.click(within(list).getByRole("button", { name: "备用装置" }));
    expect(window.confirm).toHaveBeenCalledWith("切换装置会重新载入物理量映射与现场接线图；尚未保存的映射或接线草稿请先保存。继续切换到“备用装置”？");
    expect(await screen.findByRole("button", { name: /当前装置：备用装置/ })).toBeInTheDocument();
    expect(screen.getByText("已切换到 备用装置")).toHaveAttribute("role", "status");
    expect(screen.getByLabelText("装置重载代数")).toHaveTextContent("1");
    expect(writes()).toEqual([{ path: "/api/setups/active", method: "PUT", body: { setup_id: "second", base_revision: "registry-1" } }]);
  });
  it("硬件忙时禁用切换并显示原因", async () => {
    hardwareBusy = true; renderSetups(<SetupSwitcher />); await openMenu();
    expect(screen.getByRole("button", { name: "备用装置" })).toBeDisabled();
    expect(screen.getByText("硬件任务运行中，结束后再切换")).toBeInTheDocument();
    expect(writes()).toEqual([]);
  });
  it("hardware_busy 409 保留标签与 epoch，显示硬件忙提示", async () => {
    failure = "hardware_busy"; renderSetups(<><SetupSwitcher /><Epoch /></>); await openMenu();
    fireEvent.click(screen.getByRole("button", { name: "备用装置" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("硬件忙，未切换");
    expect(screen.getByRole("button", { name: /当前装置：默认装置/ })).toBeInTheDocument();
    expect(screen.getByLabelText("装置重载代数")).toHaveTextContent("0");
  });
  it("取消确认不发写请求，Escape 收起并恢复焦点", async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    renderSetups(<SetupSwitcher />); await openMenu();
    fireEvent.click(screen.getByRole("button", { name: "备用装置" }));
    expect(writes()).toEqual([]);
    const trigger = screen.getByRole("button", { name: /当前装置：默认装置/ });
    fireEvent.keyDown(screen.getByLabelText("装置列表"), { key: "Escape" });
    expect(trigger).toHaveAttribute("aria-expanded", "false"); expect(trigger).toHaveFocus();
  });
  it("切换前迟到的刷新不回退成功切换后的共享登记", async () => {
    renderSetups();
    await screen.findByText("默认装置", { selector: "td" });
    const before = structuredClone(registry);
    const handler = vi.mocked(fetch).getMockImplementation()!;
    let resolve!: (value: Response) => void;
    vi.mocked(fetch).mockImplementation((input, options) => String(input) === "/api/setups" && (options?.method ?? "GET") === "GET"
      ? new Promise<Response>(done => { resolve = done; }) : handler(input, options));
    fireEvent.click(screen.getByRole("button", { name: "刷新装置" }));
    await openMenu();
    fireEvent.click(screen.getByRole("button", { name: "备用装置" }));
    await screen.findByRole("button", { name: /当前装置：备用装置/ });
    await act(async () => { resolve(response(before)); });
    expect(screen.getByRole("button", { name: /当前装置：备用装置/ })).toBeInTheDocument();
    expect(screen.getByLabelText("装置重载代数")).toHaveTextContent("1");
  });

  it("revision_conflict 自动刷新修订并提示，下一次操作使用新修订", async () => {
    renderSetups(<SetupSwitcher />); await openMenu();
    registry = { ...registry, revision: "registry-from-other-window" }; failure = "revision_conflict";
    fireEvent.click(screen.getByRole("button", { name: "备用装置" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("装置登记已变化，已刷新，请核对后重试");
    expect(requests.filter(item => item.path === "/api/setups" && item.method === "GET")).toHaveLength(2);
    failure = ""; fireEvent.click(screen.getByRole("button", { name: "备用装置" }));
    await screen.findByRole("button", { name: /当前装置：备用装置/ });
    expect(writes().at(-1)?.body?.base_revision).toBe("registry-from-other-window");
  });
});

describe("装置管理", () => {
  it("新建默认复制当前装置、改名与归档成功，写回共享登记", async () => {
    renderSetups();
    const form = await screen.findByRole("form", { name: "新建装置" });
    await waitFor(() => expect(screen.getByLabelText("复制来源")).toHaveValue("default"));
    fireEvent.change(within(form).getByLabelText("装置名称"), { target: { value: "新实验装置" } });
    fireEvent.submit(form);
    expect(await screen.findByText("新实验装置", { selector: "td" })).toBeInTheDocument();
    expect(writes()[0]).toEqual({ path: "/api/setups", method: "POST", body: { label: "新实验装置", copy_from: "default", base_revision: "registry-1" } });
    expect(screen.getByRole("button", { name: /当前装置：默认装置/ })).toBeInTheDocument();
    expect(screen.getByLabelText("装置重载代数")).toHaveTextContent("0");
    const row = screen.getByText("新实验装置", { selector: "td" }).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "改名" }));
    fireEvent.change(screen.getByLabelText("新实验装置 新名称"), { target: { value: "改名装置" } });
    fireEvent.click(within(row).getByRole("button", { name: "保存名称" }));
    await screen.findByText("改名装置", { selector: "td" });
    expect(writes().at(-1)?.body).toEqual({ label: "改名装置", base_revision: "registry-1-next" });
    fireEvent.click(within(row).getByRole("button", { name: "归档" }));
    await waitFor(() => expect(within(row).getByText("已归档")).toBeInTheDocument());
    expect(writes().at(-1)?.body).toEqual({ archived: true, base_revision: "registry-1-next-next" });
    fireEvent.click(within(row).getByRole("button", { name: "取消归档" }));
    await waitFor(() => expect(within(row).getByText("可用")).toBeInTheDocument());
    expect(writes().at(-1)?.body?.archived).toBe(false);
  });
  it("当前装置不可归档，显示原因和延迟落盘说明", async () => {
    registry = { ...initial(), stored: false, setups: [initial().setups[0]] };
    renderSetups(<SetupsView />);
    const cell = await screen.findByText("默认装置", { selector: "td" });
    expect(within(cell.closest("tr")!).getByRole("button", { name: "归档" })).toBeDisabled();
    expect(screen.getByText("当前装置不可归档，请先切换到其他装置")).toBeInTheDocument();
    expect(screen.getByText("目前只有默认装置；新建第二个装置后才会生成 params/setups.yaml")).toBeInTheDocument();
    expect(screen.getByText("新建会复制来源装置的物理量映射与现场接线图，不改变当前装置")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
  });
  it("行内设为当前沿用确认及成功提示", async () => {
    renderSetups(<SetupsView />);
    const cell = await screen.findByText("备用装置", { selector: "td" });
    fireEvent.click(within(cell.closest("tr")!).getByRole("button", { name: "设为当前" }));
    expect(await screen.findByText("已切换到 备用装置")).toBeInTheDocument();
    expect(within(cell.closest("tr")!).getByText("当前")).toBeInTheDocument();
  });
  it("422 展示 API 参数错误并保留名称输入", async () => {
    failure = "validation"; renderSetups(<SetupsView />);
    await screen.findByText("默认装置", { selector: "td" });
    fireEvent.change(screen.getByLabelText("装置名称"), { target: { value: "重复名称" } });
    fireEvent.click(screen.getByRole("button", { name: "创建装置" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("装置名称已存在");
    expect(screen.getByLabelText("装置名称")).toHaveValue("重复名称");
  });
  it("真实 InstrumentsPage 从 URL 直接进入装置标签", async () => {
    renderSetups(<InstrumentsPage />, "/instruments?view=setups");
    expect(await screen.findByRole("region", { name: "装置管理" })).toBeInTheDocument();
    await screen.findByText("默认装置", { selector: "td" });
    expect(screen.getByRole("button", { name: "装置" })).toHaveClass("active");
    expect(requests.some(item => item.path === "/api/control-targets")).toBe(false);
  });
});
