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
