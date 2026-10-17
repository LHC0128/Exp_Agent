import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

it("DC 首行三栏显示波形、偏置和固定 V 单位，不提交 Vrms", async () => {
  const { container, onSaved } = renderChannel({ voltage_unit: "VRMS" });
  const labels = container.querySelectorAll(".generator-basic-grid > label > span");
  expect(Array.from(labels, (label) => label.textContent)).toEqual(["波形", "偏置 (V)", "单位"]);
  expect(screen.getByLabelText("单位")).toHaveValue("V");
  expect(screen.getByLabelText("单位")).toBeDisabled();
  expect(screen.queryByLabelText("频率 (Hz)")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "应用并回读" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(JSON.parse(String(mockApi.mock.calls[0][1]?.body)).settings.voltage_unit).toBe("VPP");
});

it.each(["DG4000", "DG900"] as const)("%s 交流单位选择可编辑并随幅度提交", async (type) => {
  const { container, onSaved } = renderChannel({ shape: "SINusoid", load: 50 }, type);
  const labels = container.querySelector(".generator-basic-grid")!.querySelectorAll("label > span");
  expect(Array.from(labels, (label) => label.textContent)).toEqual(["波形", "幅度", "单位"]);
  expect(screen.getAllByLabelText("单位")).toHaveLength(1);
  expect(screen.getByLabelText("频率 (Hz)").closest(".generator-secondary-grid")).not.toBeNull();
  expect(screen.getByLabelText("相位 (deg)").closest(".generator-secondary-grid")).not.toBeNull();
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

it.each(["DC", "PULSe"])("切换到 %s 时恢复固定电压单位", (shape) => {
  renderChannel({ shape: "SINusoid", voltage_unit: "VRMS" });
  fireEvent.change(screen.getByLabelText("波形"), { target: { value: shape } });
  expect(screen.getByLabelText("单位")).toHaveValue(shape === "DC" ? "V" : "VPP");
  if (shape === "PULSe") expect(screen.getByRole("option", { name: "Vrms" })).toBeDisabled();
});

it("未知单位仍允许独立关闭输出，不重写其它参数", async () => {
  const { onSaved } = renderChannel({ shape: "SINusoid", voltage_unit: null });
  expect(screen.getByLabelText("幅度")).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "应用并回读" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(JSON.parse(String(mockApi.mock.calls[0][1]?.body)).settings).toEqual({ output: false });
});

it("只读通道不能修改单位", () => {
  renderChannel({ shape: "SINusoid", read_only: true });
  expect(screen.getByLabelText("单位")).toBeDisabled();
  expect(screen.getByRole("button", { name: "应用并回读" })).toBeDisabled();
});
