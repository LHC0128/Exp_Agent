import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WiringChecklist } from "./WiringChecklist";
import type { components } from "../../types/openapi";
import type { WiringGraph } from "./wiringTypes";

const port = (value: Partial<components["schemas"]["PhysicalPort"]> & Pick<components["schemas"]["PhysicalPort"], "id" | "name">) => ({
  group: "", signal_type: null, connector: "", purpose: "", direction: null, modes: [], selected_mode: null, ...value,
});

function graph(): WiringGraph {
  return {
    nodes: [
      { x: 0, y: 0, device: { id: "dev-a", name: "激光器", model: "DL-100", device_library_id: "lib-a", ports: [
        port({ id: "a-out", name: "光输出", signal_type: "optical", direction: "out" }),
        port({ id: "a-in", name: "调制输入", signal_type: "electrical", direction: "in" }),
        port({ id: "a-spare", name: "备用", group: "Aux" }),
      ] }, internal_ports: [{ id: "a-int-out", name: "内部射频输出", direction: "out" }, { id: "a-int-in", name: "内部射频输入", direction: "in" }] },
      { x: 0, y: 0, device: { id: "dev-b", name: "锁相放大器", model: "HF2", device_library_id: null, ports: [
        port({ id: "b-out", name: "信号输出", signal_type: "electrical", direction: "switchable", selected_mode: "mode-in",
          modes: [{ id: "mode-out", name: "输出模式", direction: "out" }, { id: "mode-in", name: "输入模式", direction: "in" }] }),
        port({ id: "b-in", name: "光输入", signal_type: "optical", direction: "in" }),
      ] } },
    ],
    edges: [
      { id: "e1", source: { device_id: "dev-a", port_id: "a-out" }, target: { device_id: "dev-b", port_id: "b-in" }, signal_type: "optical", label: "光纤 1" },
      { id: "e2", source: { device_id: "dev-b", port_id: "b-out" }, target: { device_id: "dev-a", port_id: "a-in" }, signal_type: "electrical", label: "BNC 2" },
      { id: "e3", source: { device_id: "dev-a", port_id: "a-int-out" }, target: { device_id: "dev-a", port_id: "a-int-in" }, signal_type: "internal", label: "内部路由" },
    ],
  };
}

const props = () => ({ graph: graph(), viewName: "实验目标 · RF 灵敏度", revision: "rev-42", dirty: true });

function printRoot(): HTMLElement {
  const root = document.querySelector(".wiring-print-root");
  if (!(root instanceof HTMLElement)) throw new Error("未找到打印清单根节点");
  return root;
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("WiringChecklist", () => {
  it("按器件列出端口、方向 / 模式、对端、光 / 电类型与连线标签", () => {
    render(<WiringChecklist {...props()} />);
    const list = within(printRoot());

    expect(list.getByText("实验目标 · RF 灵敏度 · 修订号 rev-42 · 含未保存草稿修改")).toBeInTheDocument();
    expect(list.getByText(/人工记录，非硬件状态或安全判定/)).toBeInTheDocument();
    expect(list.getByText("共 2 个器件，3 条连线。")).toBeInTheDocument();
    expect(list.getByText("激光器（DL-100）")).toBeInTheDocument();
    expect(list.getByText("锁相放大器（HF2）")).toBeInTheDocument();

    expect(list.getAllByText("光纤 1")).toHaveLength(2);
    expect(list.getByText("锁相放大器 / 光输入")).toBeInTheDocument();
    expect(list.getAllByText("BNC 2")).toHaveLength(2);
    expect(list.getByText("锁相放大器 / 信号输出")).toBeInTheDocument();
    expect(list.getByText("模式：输入模式（输入）")).toBeInTheDocument();
    expect(list.getAllByText("内部路由")).toHaveLength(2);
    expect(list.getByText("激光器 / 内部射频输入（内部）")).toBeInTheDocument();
    expect(list.getByText("内部输出")).toBeInTheDocument();
    expect(list.getByText("未连接")).toBeInTheDocument();
    expect(list.getAllByText("光学")).toHaveLength(2);
    expect(list.getAllByText("电学")).toHaveLength(2);
    expect(list.getAllByText("内部信号（非线缆）")).toHaveLength(2);
    expect(list.getByText("光/电待确认")).toBeInTheDocument();
  });

  it("点击按钮只调用 window.print，不请求后端", () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<WiringChecklist {...props()} />);
    fireEvent.click(screen.getByRole("button", { name: "导出/打印核对清单" }));

    expect(print).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("未激活时不挂载打印内容，避免隐藏页混入其他视图", () => {
    render(<WiringChecklist {...props()} active={false} />);
    expect(document.querySelector(".wiring-print-root")).toBeNull();
  });

  it("草稿尚未载入时禁用导出按钮", () => {
    render(<WiringChecklist {...props()} revision="" disabled />);
    expect(screen.getByRole("button", { name: "导出/打印核对清单" })).toBeDisabled();
    expect(within(printRoot()).getByText(/修订号 尚未载入/)).toBeInTheDocument();
  });
});
