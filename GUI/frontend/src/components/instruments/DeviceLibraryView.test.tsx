import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import type { DeviceLibraryDocument, PhysicalMappingsDocument, VisaDiscovery } from "../../types/api";
import { DeviceLibraryView } from "./DeviceLibraryView";
import { useJobActivity } from "../JobActivity";

vi.mock("../../api", () => ({ api: vi.fn() }));
vi.mock("../JobActivity", () => ({ useJobActivity: vi.fn() }));
const mockApi = vi.mocked(api);
const USB = "USB0::0x1AB1::0x0646::DG9Q123::INSTR";
const LIBRARY: DeviceLibraryDocument = {
  schema_version: 1, revision: "saved-revision",
  network_resources: ["TCPIP0::192.0.2.31::INSTR"],
  devices: {
    source: { instrument: "signal_generator", model: "DG900", label: "已有信号源", resource: USB,
      reference_clock: "EXT", connection: {}, capabilities: { channels: [1, 2] } },
    hf2: { instrument: "lockin_amplifier", model: "HF2", label: "锁相放大器", resource: null,
      reference_clock: "EXT", connection: { device_id: "dev18246", host: "127.0.0.1", port: 8005 },
      capabilities: { channels: [0, 1, 2, 3, 4, 5] } },
  },
};
const MAPPINGS: PhysicalMappingsDocument = { schema_version: 2, revision: "mapping-revision", mapping: {},
  constraints: { colocation_groups: {}, shared_channel_groups: {} } };
const DISCOVERY: VisaDiscovery = {
  devices: [
    { resource: USB, idn: "RIGOL,DG912,DG9Q123,1", instrument: "signal_generator", model: "DG900", serial_number: "DG9Q123" },
    { resource: "TCPIP0::192.0.2.21::INSTR", idn: "RIGOL,DG912,DG9Q123,2", instrument: "signal_generator", model: "DG900", serial_number: "DG9Q123" },
    { resource: null, idn: "Zurich Instruments,HF2LI,dev18246", instrument: "lockin_amplifier", model: "HF2", serial_number: "dev18246", connection: { device_id: "dev18246", host: "127.0.0.1", port: 8005 } },
    { resource: "USB0::0x1AB1::0x0641::DG4ENEW::INSTR", idn: "RIGOL,DG4162,DG4ENEW,1", instrument: "signal_generator", model: "DG4000", serial_number: "DG4ENEW" },
    { resource: "TCPIP0::192.0.2.31::INSTR", idn: "RIGOL,DG4162,DG4ENEW,2", instrument: "signal_generator", model: "DG4000", serial_number: "DG4ENEW" },
  ],
  errors: [{ resource: "TCPIP0::192.0.2.99::INSTR", message: "连接超时" }],
};

beforeEach(() => {
  mockApi.mockReset();
  vi.mocked(useJobActivity).mockReturnValue({ hardwareBusy: false, activeJobs: [] });
  mockApi.mockImplementation(async (url, options) => {
    if (url === "/api/device-library" && options?.method === "PUT") {
      const body = JSON.parse(String(options.body));
      return { ...LIBRARY, revision: "new-revision", devices: body.devices, network_resources: body.network_resources,
        physical_mappings: { ...MAPPINGS, revision: "new-mapping-revision", mapping: body.mapping } } as never;
    }
    if (url === "/api/device-library") return LIBRARY as never;
    if (url === "/api/physical-mappings") return MAPPINGS as never;
    if (url === "/api/device-library/discover-visa") return DISCOVERY as never;
    throw new Error(`未预期的接口: ${url}`);
  });
});
afterEach(cleanup);

async function scan() {
  render(<DeviceLibraryView />);
  fireEvent.click(await screen.findByRole("button", { name: "扫描 USB / 网络 / 锁相放大器" }));
  await screen.findByText("扫描结果");
}
const row = (idn: string) => within(screen.getByText(idn).closest("div")!);

describe("设备库身份与扫描状态", () => {
  it("旧锁相默认自动，可保存 Scope，并在硬件忙时禁止修改", async () => {
    const rendered = render(<DeviceLibraryView />);
    const selector = await screen.findByLabelText("解调采集方式");
    expect(selector).toHaveValue("auto");
    fireEvent.change(selector, { target: { value: "scope" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    await waitFor(() => expect(mockApi.mock.calls.some(([, value]) => value?.method === "PUT")).toBe(true));
    const [, options] = mockApi.mock.calls.find(([, value]) => value?.method === "PUT")!;
    expect(JSON.parse(String(options?.body))).toMatchObject({ base_revision: "saved-revision", devices: { hf2: { acquisition_mode: "scope" } } });
    vi.mocked(useJobActivity).mockReturnValue({ hardwareBusy: true, activeJobs: [] });
    rendered.rerender(<DeviceLibraryView />);
    expect(selector).toBeDisabled();
  });

  it("网络扫描地址与设备库同批保存，空列表也明确保存", async () => {
    render(<DeviceLibraryView />);
    const input = await screen.findByLabelText("网络扫描地址（每行一个 VISA 资源）");
    expect(input).toHaveValue(LIBRARY.network_resources.join("\n"));
    fireEvent.change(input, { target: { value: "" } });
    expect(screen.getByRole("button", { name: "扫描 USB / 网络 / 锁相放大器" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "扫描 USB / 网络 / 锁相放大器" })).toBeEnabled());
    const [, options] = mockApi.mock.calls.find(([, value]) => value?.method === "PUT")!;
    expect(JSON.parse(String(options?.body))).toMatchObject({ network_resources: [], devices: LIBRARY.devices, base_revision: "saved-revision" });
  });

  it("四台新网络信号源按确认的序列号独立加入，不自动修改物理量", async () => {
    const discovered: VisaDiscovery = { devices: [31, 32, 33, 34].map(index => ({
      resource: `TCPIP0::192.168.124.${index}::INSTR`, idn: `RIGOL,DG912 Pro,DG9Q${index},1`,
      instrument: "signal_generator", model: "DG900", serial_number: `DG9Q${index}`,
    })), errors: [] };
    const handler = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (url, options) => url === "/api/device-library/discover-visa" ? discovered as never : handler(url, options));
    await scan();
    for (const item of discovered.devices) fireEvent.click(row(item.idn).getByRole("button", { name: "加入设备库" }));
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    await waitFor(() => expect(row(discovered.devices[3].idn).getByRole("button", { name: "已入库" })).toBeDisabled());
    const [, options] = mockApi.mock.calls.find(([, value]) => value?.method === "PUT")!;
    const saved = JSON.parse(String(options?.body));
    for (const index of [31, 32, 33, 34]) {
      expect(saved.devices[`dg9q${index}`]).toMatchObject({ resource: `TCPIP0::192.168.124.${index}::INSTR`, connection: { serial_number: `DG9Q${index}` } });
    }
    expect(mockApi.mock.calls.some(([url, options]) => url === "/api/physical-mappings" && options?.method === "PUT")).toBe(false);
  });

  it("显示 HF2 设备 ID 和数据服务器地址", async () => {
    render(<DeviceLibraryView />);
    expect(await screen.findByLabelText("设备 ID / 序列号")).toHaveValue("dev18246");
    expect(screen.getByLabelText("数据服务器地址")).toHaveValue("127.0.0.1:8005");
    expect(screen.getByText("HF2 通过 USB 接入数据服务器，此处显示服务器地址。")).toBeInTheDocument();
  });

  it("同一台设备的 USB、网络与 HF2 记录显示已入库并禁止重复加入", async () => {
    await scan();
    for (const item of DISCOVERY.devices.slice(0, 3)) {
      expect(row(item.idn).getByRole("button", { name: "已入库" })).toBeDisabled();
    }
    expect(row(DISCOVERY.devices[1].idn).getByText("TCPIP0::192.0.2.21::INSTR")).toBeInTheDocument();
    expect(row(DISCOVERY.devices[1].idn).getByText("已入库 · 已有信号源（source）")).toBeInTheDocument();
  });

  it("加入新设备后所有同序列号连接显示待保存，成功保存后显示已入库", async () => {
    await scan();
    fireEvent.click(row(DISCOVERY.devices[3].idn).getByRole("button", { name: "加入设备库" }));
    for (const item of DISCOVERY.devices.slice(3)) {
      expect(row(item.idn).getByRole("button", { name: "已加入，待保存" })).toBeDisabled();
    }
    expect(mockApi.mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    await waitFor(() => expect(row(DISCOVERY.devices[4].idn).getByRole("button", { name: "已入库" })).toBeDisabled());
    const [, options] = mockApi.mock.calls.find(([, value]) => value?.method === "PUT")!;
    const saved = JSON.parse(String(options?.body));
    expect(saved.devices.dg4enew.connection.serial_number).toBe("DG4ENEW");
    expect(saved.devices.source.resource).toBe(USB);
    expect(Object.keys(saved.devices)).toHaveLength(3);
  });

  it("保存失败仍保留待保存状态", async () => {
    const handler = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (url, options) => {
      if (options?.method === "PUT") throw new Error("修订冲突");
      return handler(url, options);
    });
    await scan();
    fireEvent.click(row(DISCOVERY.devices[3].idn).getByRole("button", { name: "加入设备库" }));
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    expect(await screen.findByText("Error: 修订冲突")).toBeInTheDocument();
    expect(row(DISCOVERY.devices[3].idn).getByRole("button", { name: "已加入，待保存" })).toBeDisabled();
  });

  it("展示失败地址和原因", async () => {
    await scan();
    expect(screen.getByText("1 个连接未能完成扫描")).toBeInTheDocument();
    expect(screen.getByText("TCPIP0::192.0.2.99::INSTR")).toBeInTheDocument();
    expect(screen.getByText(/连接超时/)).toBeInTheDocument();
  });

  it("编辑 HF2 通道保留 Demod0，忽略空项与非整数", async () => {
    render(<DeviceLibraryView />);
    const panel = (await screen.findByRole("heading", { name: "锁相放大器" })).closest("section")!;
    fireEvent.change(within(panel).getByLabelText("通道号列表（逗号分隔）"), { target: { value: "0, 1, 2, 1.5, " } });
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    await waitFor(() => expect(mockApi.mock.calls.some(([, options]) => options?.method === "PUT")).toBe(true));
    const [, options] = mockApi.mock.calls.find(([, value]) => value?.method === "PUT")!;
    expect(JSON.parse(String(options?.body)).devices.hf2.capabilities.channels).toEqual([0, 1, 2]);
  });

  it("新增 HF2 保存专用连接参数和六个解调器通道", async () => {
    const newHf2 = { ...DISCOVERY.devices[2], serial_number: "dev19001", idn: "Zurich Instruments,HF2LI,dev19001", connection: { device_id: "dev19001", host: "192.0.2.40", port: 8005 } };
    const handler = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (url, options) => url === "/api/device-library/discover-visa" ? { devices: [newHf2], errors: [] } as never : handler(url, options));
    await scan();
    fireEvent.click(row(newHf2.idn).getByRole("button", { name: "加入设备库" }));
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    await waitFor(() => expect(row(newHf2.idn).getByRole("button", { name: "已入库" })).toBeDisabled());
    const [, options] = mockApi.mock.calls.find(([, value]) => value?.method === "PUT")!;
    const device = JSON.parse(String(options?.body)).devices.dev19001;
    expect(device.resource).toBeNull();
    expect(device.connection).toMatchObject(newHf2.connection);
    expect(device.capabilities.channels).toEqual([0, 1, 2, 3, 4, 5]);
  });
});

describe("全系统锁相放大器选择", () => {
  const devices = { ...LIBRARY.devices, mfli: { ...LIBRARY.devices.hf2, model: "MFLI", label: "MFLI 锁相放大器",
    connection: { device_id: "dev30661", host: "192.168.124.109", port: 8004 }, capabilities: { channels: [0, 1, 2, 3] } } };
  const mapping: PhysicalMappingsDocument = { ...MAPPINGS, mapping: {
    lockin_r: { instrument: "lockin_amplifier", device_id: "hf2", endpoint: { kind: "demod", index: 0 } },
    lockin_2: { instrument: "lockin_amplifier", device_id: "hf2", endpoint: { kind: "demod", index: 1 } },
  } };
  const mockSelection = (fail = false) => {
    const handler = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (url, options) => {
      if (url === "/api/device-library" && !options?.method) return { ...LIBRARY, devices } as never;
      if (url === "/api/physical-mappings") return mapping as never;
      if (url === "/api/device-library" && options?.method === "PUT") {
        if (fail) throw new Error("设备映射已变化");
        const body = JSON.parse(String(options.body));
        return { ...LIBRARY, devices: body.devices, network_resources: body.network_resources, revision: "new-revision",
          physical_mappings: { ...mapping, revision: "selected-revision", mapping: body.mapping } } as never;
      }
      return handler(url, options);
    });
  };

  it("合并为一张卡片，选择只改草稿，保存时携带两个修订号", async () => {
    mockSelection(); render(<DeviceLibraryView />);
    const selection = await screen.findByLabelText("当前锁相放大器（全系统）");
    expect(screen.getAllByRole("heading", { name: "锁相放大器" })).toHaveLength(1);
    expect(selection).toHaveValue("hf2");
    fireEvent.change(selection, { target: { value: "mfli" } });
    await waitFor(() => expect(screen.getByLabelText("设备 ID / 序列号")).toHaveValue("dev30661"));
    expect(screen.getByLabelText("数据服务器地址")).toHaveValue("192.168.124.109:8004");
    expect(mockApi.mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
    expect(screen.getByText(/有未保存更改/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    await waitFor(() => expect(screen.queryByText(/有未保存更改/)).not.toBeInTheDocument());
    const [, options] = mockApi.mock.calls.find(([, value]) => value?.method === "PUT")!;
    expect(JSON.parse(String(options?.body))).toMatchObject({ base_revision: "saved-revision",
      physical_mapping_revision: "mapping-revision", mapping: {
        lockin_r: { device_id: "mfli", endpoint: { kind: "demod", index: 0 } },
        lockin_2: { device_id: "mfli", endpoint: { kind: "demod", index: 1 } },
      } });
    expect(screen.getAllByRole("heading", { name: "锁相放大器" })).toHaveLength(1);
  });

  it("有设备草稿或硬件任务时仍可选择，硬件任务期间禁止保存", async () => {
    mockSelection(); vi.mocked(useJobActivity).mockReturnValue({ hardwareBusy: true, activeJobs: [] });
    const view = render(<DeviceLibraryView />);
    const selection = await screen.findByLabelText("当前锁相放大器（全系统）");
    expect(selection).toBeEnabled();
    expect(screen.getByRole("button", { name: "保存设备库" })).toBeDisabled();
    fireEvent.change(selection, { target: { value: "mfli" } });
    expect(selection).toHaveValue("mfli");
    vi.mocked(useJobActivity).mockReturnValue({ hardwareBusy: false, activeJobs: [] });
    view.rerender(<DeviceLibraryView />);
    const panel = screen.getByRole("heading", { name: "锁相放大器" }).closest("section")!;
    fireEvent.change(within(panel).getByLabelText("标签"), { target: { value: "新标签" } });
    expect(screen.getByLabelText("当前锁相放大器（全系统）")).toBeEnabled();
    expect(screen.getByRole("button", { name: "保存设备库" })).toBeEnabled();
    expect(mockApi.mock.calls.some(([url]) => url === "/api/lockin-selection")).toBe(false);
  });

  it("保存失败保留选择草稿和原修订号", async () => {
    mockSelection(true); render(<DeviceLibraryView />);
    fireEvent.change(await screen.findByLabelText("当前锁相放大器（全系统）"), { target: { value: "mfli" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    expect(await screen.findByText("Error: 设备映射已变化")).toBeInTheDocument();
    expect(screen.getByLabelText("当前锁相放大器（全系统）")).toHaveValue("mfli");
    expect(screen.getByLabelText("设备 ID / 序列号")).toHaveValue("dev30661");
    expect(screen.getByText(/修订 saved-revi/)).toBeInTheDocument();
    expect(screen.getByText(/有未保存更改/)).toBeInTheDocument();
  });

  it("目标锁相缺少现有解调器时整组保持原选择", async () => {
    const incompatible = structuredClone(mapping);
    incompatible.mapping.lockin_2.endpoint!.index = 5;
    const handler = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (url, options) => {
      if (url === "/api/device-library") return { ...LIBRARY, devices } as never;
      if (url === "/api/physical-mappings") return incompatible as never;
      return handler(url, options);
    });
    render(<DeviceLibraryView />);
    fireEvent.change(await screen.findByLabelText("当前锁相放大器（全系统）"), { target: { value: "mfli" } });
    expect(screen.getByText(/缺少 lockin_2 所需的 Demod 5/)).toBeInTheDocument();
    expect(screen.getByLabelText("当前锁相放大器（全系统）")).toHaveValue("hf2");
    expect(mockApi.mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
  });
});

describe("信号源与主磁场 Resource 选择", () => {
  const library: DeviceLibraryDocument = { ...LIBRARY, devices: { ...LIBRARY.devices,
    lan: { ...LIBRARY.devices.source, model: "DG4000", label: "网络信号源", resource: "TCPIP0::192.168.124.33::INSTR",
      connection: { serial_number: "DG4E261300744" } },
    narrow: { ...LIBRARY.devices.source, label: "单通道", resource: "TCPIP0::192.0.2.32::INSTR", capabilities: { channels: [1] } },
    mfli: { ...LIBRARY.devices.hf2, model: "MFLI", connection: { device_id: "dev30661", host: "192.0.2.40", port: 8004 },
      capabilities: { channels: [0, 1, 2, 3] } },
    gs_usb: { instrument: "gs200", model: "GS200", label: "USB 主磁场电流源",
      resource: "USB0::0x0B21::0x0039::90Z631552::INSTR", reference_clock: null, connection: {}, capabilities: {} },
    gs_lan: { instrument: "gs200", model: "GS200", label: "网络主磁场电流源",
      resource: "TCPIP0::192.168.124.41::INSTR", reference_clock: null, connection: {}, capabilities: {} },
    keithley: { instrument: "keithley_6221", model: "6221", label: "小磁场电流源",
      resource: "TCPIP0::192.0.2.22::1394::SOCKET", reference_clock: null, connection: {}, capabilities: {} },
  } };
  const mapping: PhysicalMappingsDocument = { ...MAPPINGS, mapping: {
    main_magnetic_field: { instrument: "gs200", device_id: "gs_usb", label: "GS200 Z 主磁场", source_function: "CURRent" },
    keithley_6221_main_field: { instrument: "keithley_6221", device_id: "keithley", label: "Z 小磁场" },
    z: { instrument: "signal_generator", device_id: "source", label: "Z 场", endpoint: { kind: "channel", index: 1 } },
    sequence: { instrument: "signal_generator", device_id: "source", label: "时序", endpoint: { kind: "channel", index: 2 } },
    alias: { instrument: "signal_generator", device_id: "source", label: "时序别名", endpoint: { kind: "channel", index: 2 } },
    Heat_Control: { instrument: "signal_generator", device_id: "source", label: "加热信号", endpoint: { kind: "channel", index: 1 } },
    Temp_Switch: { instrument: "signal_generator", device_id: "source", label: "温度开关信号", endpoint: { kind: "channel", index: 2 } },
    carrier: { instrument: "signal_generator", device_id: "lan", label: "Probe AOM", endpoint: { kind: "channel", index: 1 } },
    lockin_r: { instrument: "lockin_amplifier", device_id: "hf2", endpoint: { kind: "demod", index: 0 } },
  }, constraints: { colocation_groups: { z_sequence: ["z", "sequence"], temperature_control: ["Heat_Control", "Temp_Switch"] },
    shared_channel_groups: { timing: ["sequence", "alias"] } } };
  const mockResources = () => {
    let savedLibrary = structuredClone(library);
    let savedMapping = structuredClone(mapping);
    mockApi.mockImplementation(async (url, options) => {
      if (url === "/api/physical-mappings") return savedMapping as never;
      if (url === "/api/device-library" && options?.method === "PUT") {
        const body = JSON.parse(String(options.body));
        savedLibrary = { ...savedLibrary, revision: "new-revision", devices: body.devices, network_resources: body.network_resources };
        savedMapping = { ...savedMapping, revision: "new-mapping-revision", mapping: body.mapping };
        return { ...savedLibrary, physical_mappings: savedMapping } as never;
      }
      if (url === "/api/device-library") return savedLibrary as never;
      throw new Error(`未预期的接口: ${url}`);
    });
  };

  it("主磁场只列出 GS200，保留原绑定直到保存，并可重新加载所选网络设备", async () => {
    mockResources();
    const view = render(<DeviceLibraryView />);
    const selection = await screen.findByLabelText("main_magnetic_field Resource");
    expect(selection).toHaveValue("gs_usb");
    expect(within(selection).getAllByRole("option").map(option => (option as HTMLOptionElement).value)).toEqual(["gs_usb", "gs_lan"]);
    expect(selection).toHaveTextContent("TCPIP0::192.168.124.41::INSTR");
    expect(selection).toHaveTextContent("90Z631552");
    expect(screen.getAllByRole("heading", { name: "GS200 Z 主磁场" })).toHaveLength(1);
    fireEvent.change(selection, { target: { value: "gs_lan" } });
    expect(selection).toHaveValue("gs_lan");
    expect(mockApi.mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    await waitFor(() => expect(screen.queryByText(/有未保存更改/)).not.toBeInTheDocument());
    const [, options] = mockApi.mock.calls.find(([, value]) => value?.method === "PUT")!;
    expect(JSON.parse(String(options?.body))).toMatchObject({ base_revision: "saved-revision",
      physical_mapping_revision: "mapping-revision", devices: library.devices, mapping: {
        ...mapping.mapping, main_magnetic_field: { ...mapping.mapping.main_magnetic_field, device_id: "gs_lan" },
      } });
    view.unmount(); render(<DeviceLibraryView />);
    expect(await screen.findByLabelText("main_magnetic_field Resource")).toHaveValue("gs_lan");
  });

  it("Resource 下拉显示真实设备，联动组保留通道，与锁相同批保存并可重新加载", async () => {
    mockResources();
    const view = render(<DeviceLibraryView />);
    const selection = await screen.findByLabelText("z Resource");
    expect(selection).toHaveTextContent(USB);
    expect(selection).toHaveTextContent("DG4000 · TCPIP0::192.168.124.33::INSTR · DG4E261300744");
    fireEvent.change(selection, { target: { value: "lan" } });
    fireEvent.change(screen.getByLabelText("当前锁相放大器（全系统）"), { target: { value: "mfli" } });
    expect(selection).toHaveValue("lan");
    expect(screen.getByLabelText("carrier Resource")).toHaveValue("lan");
    expect(screen.getByText(/z · CH1；sequence · CH2；alias · CH2/)).toBeInTheDocument();
    expect(mockApi.mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    await waitFor(() => expect(screen.queryByText(/有未保存更改/)).not.toBeInTheDocument());
    const [, options] = mockApi.mock.calls.find(([, value]) => value?.method === "PUT")!;
    const saved = JSON.parse(String(options?.body));
    expect(saved.devices).toEqual(library.devices);
    expect(saved.mapping).toEqual({ ...mapping.mapping,
      ...Object.fromEntries(["z", "sequence", "alias"].map(key => [key, { ...mapping.mapping[key], device_id: "lan" }])),
      lockin_r: { ...mapping.mapping.lockin_r, device_id: "mfli" },
    });
    expect(saved.physical_mapping_revision).toBe("mapping-revision");
    view.unmount(); render(<DeviceLibraryView />);
    expect(await screen.findByLabelText("z Resource")).toHaveValue("lan");
    fireEvent.change(screen.getByLabelText("z Resource"), { target: { value: "source" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    await waitFor(() => expect(mockApi.mock.calls.filter(([, value]) => value?.method === "PUT")).toHaveLength(2));
    const second = mockApi.mock.calls.filter(([, value]) => value?.method === "PUT")[1];
    expect(JSON.parse(String(second[1]?.body))).toMatchObject({ base_revision: "new-revision", physical_mapping_revision: "new-mapping-revision" });
  });

  it("温控两路只显示一张卡片，Resource 联动切换并在保存后生效", async () => {
    mockResources(); render(<DeviceLibraryView />);
    const selection = await screen.findByLabelText("Heat_Control Resource");
    expect(screen.getAllByRole("heading", { name: "加热信号 / 温度开关信号" })).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "加热信号" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "温度开关信号" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Temp_Switch Resource")).not.toBeInTheDocument();
    fireEvent.change(selection, { target: { value: "lan" } });
    expect(selection).toHaveValue("lan");
    expect(screen.getByText(/Heat_Control · CH1；Temp_Switch · CH2/)).toBeInTheDocument();
    expect(screen.getByLabelText("z Resource")).toHaveValue("source");
    expect(mockApi.mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    await waitFor(() => expect(screen.queryByText(/有未保存更改/)).not.toBeInTheDocument());
    const [, options] = mockApi.mock.calls.find(([, value]) => value?.method === "PUT")!;
    expect(JSON.parse(String(options?.body)).mapping).toEqual({ ...mapping.mapping,
      Heat_Control: { ...mapping.mapping.Heat_Control, device_id: "lan" },
      Temp_Switch: { ...mapping.mapping.Temp_Switch, device_id: "lan" },
    });
  });

  it("目标缺少组员通道时保持原 Resource 草稿", async () => {
    mockResources(); render(<DeviceLibraryView />);
    fireEvent.change(await screen.findByLabelText("z Resource"), { target: { value: "narrow" } });
    expect(screen.getByText(/缺少 sequence 所需的 CH2，整组未切换/)).toBeInTheDocument();
    expect(screen.getByLabelText("z Resource")).toHaveValue("source");
    expect(mockApi.mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
  });

  it("保存失败保留信号源、主磁场和锁相草稿", async () => {
    mockResources();
    const handler = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (url, options) => {
      if (options?.method === "PUT") throw new Error("修订冲突");
      return handler(url, options);
    });
    render(<DeviceLibraryView />);
    fireEvent.change(await screen.findByLabelText("z Resource"), { target: { value: "lan" } });
    fireEvent.change(screen.getByLabelText("main_magnetic_field Resource"), { target: { value: "gs_lan" } });
    fireEvent.change(screen.getByLabelText("当前锁相放大器（全系统）"), { target: { value: "mfli" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设备库" }));
    expect(await screen.findByText("Error: 修订冲突")).toBeInTheDocument();
    expect(screen.getByLabelText("z Resource")).toHaveValue("lan");
    expect(screen.getByLabelText("main_magnetic_field Resource")).toHaveValue("gs_lan");
    expect(screen.getByLabelText("当前锁相放大器（全系统）")).toHaveValue("mfli");
    expect(screen.getByText(/修订 saved-revi/)).toBeInTheDocument();
  });
});
