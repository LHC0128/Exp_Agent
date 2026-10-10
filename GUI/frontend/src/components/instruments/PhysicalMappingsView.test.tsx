import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import type { DeviceLibraryDocument, PhysicalMappingsDocument } from "../../types/api";
import { PhysicalMappingsView } from "./PhysicalMappingsView";

vi.mock("../../api", () => ({ api: vi.fn() }));
const mockApi = vi.mocked(api);
const library: DeviceLibraryDocument = {
  schema_version: 1, revision: "device-revision", network_resources: [],
  devices: {
    usb: { instrument: "signal_generator", model: "DG4000", label: "USB 信号源",
      resource: "USB0::0x1AB1::0x0641::DG4E123::INSTR", reference_clock: "EXT",
      connection: {}, capabilities: { channels: [1, 2] } },
    lan: { instrument: "signal_generator", model: "DG900", label: "网络信号源",
      resource: "TCPIP0::192.168.124.31::INSTR", reference_clock: "EXT",
      connection: { serial_number: "DG9Q321" }, capabilities: { channels: [1, 2] } },
    narrow: { instrument: "signal_generator", model: "DG900", label: "单通道信号源",
      resource: "TCPIP0::192.168.124.32::INSTR", reference_clock: "EXT",
      connection: { serial_number: "DG9Q322" }, capabilities: { channels: [1] } },
  },
};
const document: PhysicalMappingsDocument = {
  schema_version: 2, revision: "mapping-revision",
  mapping: {
    x: { instrument: "signal_generator", device_id: "usb", endpoint: { kind: "channel", index: 1 } },
    y: { instrument: "signal_generator", device_id: "usb", endpoint: { kind: "channel", index: 2 } },
    rf: { instrument: "signal_generator", device_id: "usb", endpoint: { kind: "channel", index: 2 } },
  },
  constraints: { colocation_groups: { xy: ["x", "y"] }, shared_channel_groups: { y_rf: ["y", "rf"] } },
};
beforeEach(() => {
  mockApi.mockReset();
  mockApi.mockImplementation(async (url, options) => {
    if (url === "/api/device-library") return structuredClone(library) as never;
    if (url === "/api/physical-mappings" && options?.method === "PUT") {
      return { ...JSON.parse(String(options.body)), revision: "saved-revision", schema_version: 2 } as never;
    }
    if (url === "/api/physical-mappings") return structuredClone(document) as never;
    throw new Error(`未预期的接口: ${url}`);
  });
});
afterEach(cleanup);

const switchDevice = async (key: string, id: string) => {
  fireEvent.change(await screen.findByLabelText(`${key} 设备`), { target: { value: id } });
};

describe("物理量设备成组切换", () => {
  it("同一列表显示 USB、网络设备的型号、地址和序列号", async () => {
    render(<PhysicalMappingsView />);
    const select = await screen.findByLabelText("x 设备");
    expect(select).toHaveTextContent("DG4000 · USB0::0x1AB1::0x0641::DG4E123::INSTR · DG4E123");
    expect(select).toHaveTextContent("DG900 · TCPIP0::192.168.124.31::INSTR · DG9Q321");
  });

  it("沿重叠的同机组和共享组联动整组，保留通道并一次保存", async () => {
    render(<PhysicalMappingsView />);
    await switchDevice("x", "lan");
    for (const key of ["x", "y", "rf"]) expect(screen.getByLabelText(`${key} 设备`)).toHaveValue("lan");
    expect(screen.getByLabelText("x 端点")).toHaveValue("1");
    expect(screen.getByLabelText("y 端点")).toHaveValue("2");
    expect(screen.getByLabelText("rf 端点")).toHaveValue("2");
    expect(mockApi.mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "保存全部映射" }));
    await waitFor(() => expect(screen.getByText("修订 saved-revi")).toBeInTheDocument());
    const [, options] = mockApi.mock.calls.find(([, value]) => value?.method === "PUT")!;
    const saved = JSON.parse(String(options?.body));
    expect(saved.base_revision).toBe("mapping-revision");
    expect(saved.mapping).toEqual(Object.fromEntries(Object.entries(document.mapping).map(([key, value]) => [key, { ...value, device_id: "lan" }])));
    expect(saved.constraints).toEqual(document.constraints);
  });

  it("目标缺少其他组员的通道时整个草稿保持原设备", async () => {
    render(<PhysicalMappingsView />);
    await switchDevice("x", "narrow");
    expect(screen.getByText(/缺少 y 所需的 CH2，整组未切换/)).toBeInTheDocument();
    for (const key of ["x", "y", "rf"]) expect(screen.getByLabelText(`${key} 设备`)).toHaveValue("usb");
    expect(screen.getByLabelText("y 端点")).toHaveValue("2");
  });

  it("从共享通道组的成员发起也联动同机组", async () => {
    render(<PhysicalMappingsView />);
    await switchDevice("rf", "lan");
    for (const key of ["x", "y", "rf"]) expect(screen.getByLabelText(`${key} 设备`)).toHaveValue("lan");
  });

  it("同机组成员按物理量键校验，未知成员拒绝切换与保存", async () => {
    render(<PhysicalMappingsView />);
    await screen.findByLabelText("x 设备");
    fireEvent.change(screen.getByDisplayValue("x, y"), { target: { value: "x, usb" } });
    await switchDevice("x", "lan");
    expect(screen.getByText("组成员“usb”不是已存在的物理量键")).toBeInTheDocument();
    expect(screen.getByLabelText("x 设备")).toHaveValue("usb");
    fireEvent.click(screen.getByRole("button", { name: "保存全部映射" }));
    expect(screen.getByText("同机组成员“usb”不是已存在的物理量键")).toBeInTheDocument();
    expect(mockApi.mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
  });

  it("后端保存冲突保留整组草稿和旧修订号", async () => {
    const handler = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (url, options) => {
      if (options?.method === "PUT") throw new Error("修订冲突");
      return handler(url, options);
    });
    render(<PhysicalMappingsView />);
    await switchDevice("x", "lan");
    fireEvent.click(screen.getByRole("button", { name: "保存全部映射" }));
    expect(await screen.findByText("Error: 修订冲突")).toBeInTheDocument();
    expect(screen.getByText("修订 mapping-re")).toBeInTheDocument();
    for (const key of ["x", "y", "rf"]) expect(screen.getByLabelText(`${key} 设备`)).toHaveValue("lan");
  });
});
