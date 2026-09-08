import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { ZArbitraryControl } from "./ZArbitraryControl";

vi.mock("../api", () => ({ api: vi.fn() }));
const plot = vi.hoisted(() => vi.fn());
const activity = vi.hoisted(() => ({ hardwareBusy: false, activeJobs: [] }));
vi.mock("./ScopePlot", () => ({ ScopePlot: (props: unknown) => { plot(props); return <div>波形图</div>; } }));
vi.mock("./JobView", () => ({ JobView: () => null }));
vi.mock("./JobActivity", () => ({ useJobActivity: () => activity }));
const mockApi = vi.mocked(api);
const summary = { run_name: "obbv5", waveform_sha256: "sha5", points: 3,
  repeat_frequency_hz: 250, period_s: 0.004, amplitude_vpp: 6, offset_v: 0,
  minimum_v: -0.1, maximum_v: 0.3 };
const preview = { ...summary, time_s: [0, 0.001, 0.002], voltage_v: [-0.1, 0, 0.3] };
const targets = [{ mapping_key: "Z_magnetic_field", device_label: "DG Z", endpoint: { index: 1 } },
  { mapping_key: "Time_sequence_2", device_label: "DG Z", endpoint: { index: 2 } }];
let state: Record<string, unknown>;

beforeEach(() => {
  activity.hardwareBusy = false;
  state = { device_library_revision: "devices", physical_mapping_revision: "mapping", targets,
    source_known: false, last_applied: null };
  mockApi.mockImplementation(async (url) => {
    if (url.endsWith("/state")) return state as never;
    if (url.endsWith("/sources")) return [{ run_name: "obbv5", summary, error: null },
      { run_name: "obbv6", summary: { ...summary, run_name: "obbv6" }, error: null },
      { run_name: "bad", summary: null, error: "缺少字段" }] as never;
    if (url.endsWith("/preview/obbv5")) return preview as never;
    if (url.endsWith("/preview/obbv6")) return { ...preview, run_name: "obbv6", waveform_sha256: "sha6", voltage_v: [0, 0.2, 0] } as never;
    if (url.endsWith("/actions")) return { id: "job1", kind: "z-arbitrary-control", status: "completed", events: [] } as never;
    throw new Error(url);
  });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const show = () => render(<MemoryRouter initialEntries={["/tools#z-arbitrary-control"]}><ZArbitraryControl /></MemoryRouter>);
const select = async (name = "obbv5") => {
  await screen.findByRole("option", { name: "obbv5" });
  fireEvent.change(screen.getByLabelText("闭环冻结结果"), { target: { value: name } });
  await screen.findByText("波形图");
};

describe("Z 任意波控制", () => {
  it("不自动选择来源，离线预览原始 V–t 数据且冻结参数不可编辑", async () => {
    show();
    await screen.findByRole("option", { name: "obbv5" });
    expect(screen.getByLabelText("闭环冻结结果")).toHaveValue("");
    expect(screen.getByRole("button", { name: "加载并启动" })).toBeDisabled();
    await select();
    expect(plot.mock.lastCall?.[0].traces[0]).toMatchObject({ x: preview.time_s, y: preview.voltage_v });
    expect(screen.getByText("6 Vpp")).toBeInTheDocument();
    expect(screen.getByText("250 Hz")).toBeInTheDocument();
    expect(screen.queryByRole("spinbutton", { name: "冻结幅度" })).not.toBeInTheDocument();
    expect(mockApi.mock.calls.every(([, init]) => !init?.method)).toBe(true);
    const oldReset = plot.mock.lastCall?.[0].reset;
    fireEvent.click(screen.getByRole("button", { name: "恢复全图" }));
    expect(plot.mock.lastCall?.[0].reset).toBeGreaterThan(oldReset);
  });

  it("切换来源更新图形，坏文件显示原因并禁选", async () => {
    show(); await select();
    expect(screen.getByRole("option", { name: "bad（文件无效）" })).toBeDisabled();
    expect(screen.getByText("bad：缺少字段")).toBeInTheDocument();
    await select("obbv6");
    await waitFor(() => expect(plot.mock.lastCall?.[0].traces[0].y).toEqual([0, 0.2, 0]));
  });

  it("提交预览哈希、修订号和编辑后的触发设置", async () => {
    show(); await select();
    fireEvent.click(screen.getByLabelText("联动时序信号2"));
    expect(screen.getByLabelText("触发频率 (Hz)")).toHaveValue(100);
    fireEvent.change(screen.getByLabelText("触发频率 (Hz)"), { target: { value: "200" } });
    fireEvent.click(screen.getByRole("button", { name: "加载并启动" }));
    await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url.endsWith("/actions"))).toBe(true));
    const sent = mockApi.mock.calls.find(([url]) => url.endsWith("/actions"))!;
    expect(JSON.parse(sent[1]!.body as string)).toMatchObject({ action: "configure_and_start",
      run_name: "obbv5", waveform_sha256: "sha5", device_library_revision: "devices",
      physical_mapping_revision: "mapping", settings: { link_trigger: true, trigger_frequency_hz: 200,
        trigger_amplitude_vpp: 5, trigger_offset_v: 2.5, trigger_duty_percent: 50 } });
  });

  it("停止不携带预览或未应用设置，并展示已应用联动", async () => {
    state.last_applied = { source: summary, state: "enabled", link_trigger: true,
      updated_at: "2026-09-08T00:00:00Z", snapshots: [] };
    state.source_known = true;
    show();
    fireEvent.click(await screen.findByRole("button", { name: "停止 Z 和时序信号2" }));
    await waitFor(() => expect(mockApi.mock.calls.some(([url]) => url.endsWith("/actions"))).toBe(true));
    const sent = mockApi.mock.calls.find(([url]) => url.endsWith("/actions"))!;
    expect(JSON.parse(sent[1]!.body as string)).toEqual({ action: "stop",
      device_library_revision: "devices", physical_mapping_revision: "mapping" });
  });

  it("硬件占用期间禁止写入", async () => {
    activity.hardwareBusy = true;
    show();
    await screen.findByRole("option", { name: "obbv5" });
    expect(screen.getByRole("button", { name: "停止 Z" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "加载并启动" })).toBeDisabled();
  });

  it("预览失败后禁止应用并显示错误", async () => {
    const original = mockApi.getMockImplementation()!;
    mockApi.mockImplementation(async (url, init) => {
      if (url.includes("/preview/")) throw new Error("波形文件已变化");
      return original(url, init);
    });
    show();
    await screen.findByRole("option", { name: "obbv5" });
    fireEvent.change(screen.getByLabelText("闭环冻结结果"), { target: { value: "obbv5" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("波形文件已变化");
    expect(screen.getByRole("button", { name: "加载并启动" })).toBeDisabled();
  });
});
