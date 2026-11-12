import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "../../api";
import type { GeneratorChannelSnapshot, GeneratorSnapshot } from "../../types/api";
import { GeneratorEditor } from "./GeneratorEditor";

vi.mock("../../api", () => ({ api: vi.fn() }));
const mockApi = vi.mocked(api);

const CHANNEL: GeneratorChannelSnapshot = {
  number: 1, mapping_key: "Pump_laser_power", mapping_keys: ["Pump_laser_power"],
  label: "Pump光功率", read_only: false, output: true, shape: "DC",
  frequency: 1000, amplitude: 0.1, offset: 0.3, phase: 0, voltage_unit: "VPP", load: "9.9E+37",
  square_duty: null, ramp_symmetry: null, pulse_width: null, pulse_delay: null,
  mod: { enabled: false, type: null, source: null, internal_frequency: null, internal_function: null,
    am_depth: null, fm_deviation: null, pm_deviation: null, fsk_frequency: null, fsk_rate: null,
    fsk_polarity: null, pwm_duty_deviation: null },
  burst: { enabled: false, mode: null, ncycles: null, phase: null, period: null, delay: null,
    trigger_source: null, trigger_slope: null },
  readback_errors: [],
};

function renderChannel(channel: Partial<GeneratorChannelSnapshot> = {}, type: GeneratorSnapshot["type"] = "DG900", mappingKey = "Pump_laser_power") {
  const snapshot: GeneratorSnapshot = {
    id: "source", type, label: "信号源", resource: "fake", short_resource: "fake",
    idn: "RIGOL,fake", reference_clock: "EXT", options: {},
    channels: [{ ...CHANNEL, ...channel }],
  };
  const onSaved = vi.fn();
  const view = render(<GeneratorEditor snapshot={snapshot}
    route={{ mappingKey, deviceLibraryRevision: "devices-v1", physicalMappingRevision: "mapping-v1" }}
    onSaved={onSaved} onError={vi.fn()} />);
  return { ...view, onSaved };
}

beforeEach(() => {
  mockApi.mockReset();
  mockApi.mockResolvedValue({} as never);
});
afterEach(cleanup);

it.each([
  ["9.9E+37", "INF"], ["+9.900000E+37", "INF"], [9.9e37, "INF"],
  ["INF", "INF"], [50, 50], ["50", "50"], [1, 1], [null, null],
])("负载回读 %s 正确显示并提交为 %s", async (load, expected) => {
  const snapshot: GeneratorSnapshot = {
    id: "source", type: "DG900", label: "光功率", resource: "fake", short_resource: "DG9Q123",
    idn: "RIGOL,DG912 Pro,DG9Q123,1", reference_clock: "EXT", options: {},
    channels: [{ ...CHANNEL, load }],
  };
  const onSaved = vi.fn();
  render(<GeneratorEditor snapshot={snapshot}
    route={{ mappingKey: "Pump_laser_power", deviceLibraryRevision: "devices-v1", physicalMappingRevision: "mapping-v1" }}
    onSaved={onSaved} onError={vi.fn()} />);
  expect(screen.getByLabelText("负载 (Ω / INF)")).toHaveValue(String(expected ?? ""));
  fireEvent.click(screen.getByRole("button", { name: "应用并回读" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  const [url, options] = mockApi.mock.calls[0];
  expect(url).toBe("/api/control-targets/Pump_laser_power/generator");
  expect(options?.method).toBe("PUT");
  expect(JSON.parse(String(options?.body)).settings).toMatchObject({ load: expected, shape: "DC", offset: 0.3 });
});

it("DC 只显示电平，不显示单位、频率与运行模式，不提交 Vrms", async () => {
  const { container, onSaved } = renderChannel({ voltage_unit: "VRMS" });
  const labels = container.querySelectorAll(".generator-params label > span");
  expect(Array.from(labels, (label) => label.textContent)).toEqual(["电平"]);
  expect(screen.getByRole("radio", { name: "DC" })).toHaveAttribute("aria-checked", "true");
  expect(screen.queryByLabelText("单位")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("频率")).not.toBeInTheDocument();
  expect(screen.queryByRole("radiogroup", { name: "运行模式" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "应用并回读" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(JSON.parse(String(mockApi.mock.calls[0][1]?.body)).settings.voltage_unit).toBe("VPP");
});

it.each(["DG4000", "DG900"] as const)("%s 交流单位选择可编辑并随幅度提交", async (type) => {
  const { container, onSaved } = renderChannel({ shape: "SINusoid", load: 50 }, type);
  const labels = container.querySelectorAll(".generator-params label > span");
  expect(Array.from(labels, (label) => label.textContent)).toEqual(["频率", "幅度", "偏置", "相位"]);
  expect(screen.getAllByLabelText("单位")).toHaveLength(1);
  expect(screen.getByLabelText("单位").closest(".generator-param")).toBe(screen.getByLabelText("幅度").closest(".generator-param"));
  fireEvent.change(screen.getByLabelText("单位"), { target: { value: "VRMS" } });
  expect(screen.getByLabelText("幅度")).toBeEnabled();
  expect(screen.getByRole("button", { name: "应用并回读" })).toBeEnabled();
  fireEvent.change(screen.getByLabelText("幅度"), { target: { value: "0.2" } });
  fireEvent.click(screen.getByRole("button", { name: "应用并回读" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(JSON.parse(String(mockApi.mock.calls[0][1]?.body)).settings).toMatchObject({
    amplitude: 0.2, voltage_unit: "VRMS", offset: 0.3,
  });
});

it.each(["VPP", "VRMS", "DBM"])("%s 回读后保持可编辑并提交实际单位", async (voltage_unit) => {
  const { onSaved } = renderChannel({ shape: "SINusoid", voltage_unit, load: 50 });
  expect(screen.getByLabelText("单位")).toHaveValue(voltage_unit);
  expect(screen.getByLabelText("幅度")).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "应用并回读" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(JSON.parse(String(mockApi.mock.calls[0][1]?.body)).settings.voltage_unit).toBe(voltage_unit);
});

it("高阻禁用 dBm，改为数值负载后才开放", () => {
  renderChannel({ shape: "SINusoid" });
  expect(screen.getByRole("option", { name: "dBm" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("负载 (Ω / INF)"), { target: { value: "50" } });
  expect(screen.getByRole("option", { name: "dBm" })).toBeEnabled();
});

it("切换到 Pulse 时恢复 Vpp 并禁用 Vrms", () => {
  renderChannel({ shape: "SINusoid", voltage_unit: "VRMS", load: 50 });
  fireEvent.click(screen.getByRole("radio", { name: "Pulse" }));
  expect(screen.getByRole("radio", { name: "Pulse" })).toHaveAttribute("aria-checked", "true");
  expect(screen.getByLabelText("单位")).toHaveValue("VPP");
  expect(screen.getByRole("option", { name: "Vrms" })).toBeDisabled();
});

it("切换到 DC 后只剩电平，提交 DC 与 Vpp", async () => {
  const { onSaved } = renderChannel({ shape: "SIN", voltage_unit: "VRMS", load: 50 });
  expect(screen.getByRole("radio", { name: "Sine" })).toHaveAttribute("aria-checked", "true");
  fireEvent.click(screen.getByRole("radio", { name: "DC" }));
  expect(screen.queryByLabelText("单位")).not.toBeInTheDocument();
  expect(screen.getByLabelText("电平")).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "应用并回读" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(JSON.parse(String(mockApi.mock.calls[0][1]?.body)).settings).toMatchObject({ shape: "DC", voltage_unit: "VPP" });
});

it("改动后计数、高亮并显示仪器回读，撤销恢复回读值", () => {
  renderChannel({ shape: "SINusoid", amplitude: 0.037, load: 50 });
  expect(screen.getByRole("status")).toHaveTextContent("与仪器一致");
  expect(screen.getByRole("button", { name: "撤销" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("幅度"), { target: { value: "0.05" } });
  expect(screen.getByRole("status")).toHaveTextContent("1 项未应用");
  const cell = screen.getByLabelText("幅度").closest(".generator-param")!;
  expect(cell).toHaveClass("dirty");
  expect(cell).toHaveTextContent("仪器 0.037 Vpp");
  fireEvent.click(screen.getByRole("button", { name: "撤销" }));
  expect(screen.getByRole("status")).toHaveTextContent("与仪器一致");
  expect(screen.getByLabelText("幅度")).toHaveValue(0.037);
});

it("输出按钮显示 ON/OFF 文字", () => {
  renderChannel({ shape: "SINusoid", output: false, load: 50 });
  expect(screen.getByText("输出 OFF")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "输出" }));
  expect(screen.getByText("输出 ON")).toBeInTheDocument();
});

it("运行模式三选一：Burst 展开参数并提交起始相位，连续关闭调制与 Burst", async () => {
  const { onSaved } = renderChannel({ shape: "SINusoid", load: 50, mod: { ...CHANNEL.mod, enabled: true, type: "AM" } }, "DG4000");
  expect(screen.getByText("调制 AM")).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "调制" })).toHaveAttribute("aria-checked", "true");
  fireEvent.click(screen.getByRole("radio", { name: "Burst" }));
  fireEvent.change(screen.getByLabelText("起始相位 (deg)"), { target: { value: "90" } });
  fireEvent.click(screen.getByRole("button", { name: "应用并回读" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  const burstSettings = JSON.parse(String(mockApi.mock.calls[0][1]?.body)).settings;
  expect(burstSettings).toMatchObject({ target_mode: "burst", burst: { enabled: true, phase: 90 }, mod: { enabled: false } });
  fireEvent.click(screen.getByRole("radio", { name: "连续" }));
  expect(screen.queryByLabelText("起始相位 (deg)")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "应用并回读" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(2));
  expect(JSON.parse(String(mockApi.mock.calls[1][1]?.body)).settings).toMatchObject({ burst: { enabled: false }, mod: { enabled: false } });
});

it("未知单位仍允许独立关闭输出，不重写其它参数", async () => {
  const { onSaved } = renderChannel({ shape: "SINusoid", voltage_unit: null });
  expect(screen.getByLabelText("幅度")).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "输出" }));
  fireEvent.click(screen.getByRole("button", { name: "应用并回读" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(JSON.parse(String(mockApi.mock.calls[0][1]?.body)).settings).toEqual({ output: false });
});

it("只读通道不能修改单位", () => {
  renderChannel({ shape: "SINusoid", read_only: true });
  expect(screen.getByLabelText("单位")).toBeDisabled();
  expect(screen.getByRole("button", { name: "应用并回读" })).toBeDisabled();
});

const TWO_CHANNELS: GeneratorSnapshot = {
  id: "source", type: "DG4000", label: "王湾z", resource: "fake", short_resource: "fake",
  idn: "RIGOL,fake", reference_clock: "EXT", options: {},
  channels: [
    { ...CHANNEL, number: 1, mapping_key: "X_magnetic_field", mapping_keys: ["X_magnetic_field"], label: "X 磁场", shape: "SINusoid", amplitude: 0.1, load: 50 },
    { ...CHANNEL, number: 2, mapping_key: "Time_sequence_2", mapping_keys: ["Time_sequence_2"], label: "时序信号2", shape: "SQUare", frequency: 100, amplitude: 5, square_duty: 50, load: "INF" },
  ],
};
const TWO_CHANNEL_ROUTE = { mappingKey: "Time_sequence_2", deviceLibraryRevision: "devices-v1", physicalMappingRevision: "mapping-v1" };

it("不传 channelNumbers 时渲染全部通道", () => {
  render(<GeneratorEditor snapshot={TWO_CHANNELS} route={TWO_CHANNEL_ROUTE} onSaved={vi.fn()} onError={vi.fn()} />);
  expect(screen.getByRole("region", { name: "CH1 X 磁场" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "CH2 时序信号2" })).toBeInTheDocument();
});

it("channelNumbers 只渲染指定通道，写入与撤销仍按原通道定位", async () => {
  const onSaved = vi.fn();
  render(<GeneratorEditor snapshot={TWO_CHANNELS} route={TWO_CHANNEL_ROUTE} onSaved={onSaved} onError={vi.fn()} channelNumbers={[2]} />);
  expect(screen.queryByRole("region", { name: "CH1 X 磁场" })).not.toBeInTheDocument();
  const item = screen.getByRole("region", { name: "CH2 时序信号2" });
  expect(within(item).getByLabelText("幅度")).toHaveValue(5);
  fireEvent.change(within(item).getByLabelText("幅度"), { target: { value: "4" } });
  expect(within(item).getByRole("status")).toHaveTextContent("1 项未应用");
  fireEvent.click(within(item).getByRole("button", { name: "撤销" }));
  expect(within(item).getByLabelText("幅度")).toHaveValue(5);
  fireEvent.change(within(item).getByLabelText("幅度"), { target: { value: "4" } });
  fireEvent.click(within(item).getByRole("button", { name: "应用并回读" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(mockApi.mock.calls[0][0]).toBe("/api/control-targets/Time_sequence_2/generator");
  expect(JSON.parse(String(mockApi.mock.calls[0][1]?.body)).settings).toMatchObject({ shape: "SQUare", amplitude: 4, frequency: 100, square_duty: 50 });
});
