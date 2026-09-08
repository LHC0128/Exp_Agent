import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { ScopeCapturePage } from "./ScopeCapturePage";
import { api } from "../api";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/ScopePlot", () => ({ ScopePlot: ({ traces, mode, xScale, yScale }: { traces: unknown[]; mode: string; xScale: string; yScale: string }) => <output data-testid="plot">{JSON.stringify({ traces, mode, xScale, yScale })}</output> }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));
const mockApi = vi.mocked(api);
const fields = [
  { name: "mode", label: "模式", type: "string", default: "time", options: [{ value: "time", label: "时间" }, { value: "frequency", label: "频域" }] },
  { name: "sampling_rate_sa_s", label: "采样率", type: "number", default: 1000, unit: "Sa/s" },
  { name: "duration_s", label: "采集时间", type: "number", default: 1, unit: "s" },
  { name: "channel", label: "采集通道", type: "integer", default: 1, options: [{ value: 1, label: "C1" }, { value: 2, label: "C2" }] },
  { name: "trigger_channel", label: "触发通道", type: "integer", default: 1, options: [1, 2, 3, 4].map(value => ({ value, label: `C${value}` })) },
  { name: "trigger_mode", label: "触发模式", type: "string", default: "AUTO", options: ["AUTO", "NORMal", "SINGle"].map(value => ({ value, label: value })) },
  { name: "trigger_slope", label: "触发边沿", type: "string", default: "RISing", options: ["RISing", "FALLing"].map(value => ({ value, label: value })) },
  { name: "trigger_level_v", label: "触发电平", type: "number", default: 0, unit: "V" },
  { name: "vertical_scale_v_div", label: "垂直档位", type: "number", default: 1, unit: "V/div" },
  { name: "vertical_offset_v", label: "垂直偏置", type: "number", default: 0, unit: "V" },
  { name: "run_tag", label: "运行标签", type: "string", default: "scope" },
];
let runs: { id: string; artifacts: string[] }[];
beforeEach(() => {
  localStorage.clear();
  runs = [{ id: "run_a", artifacts: ["display.json", "psd.json"] }, { id: "run_b", artifacts: ["display.json", "psd.json"] }];
  mockApi.mockImplementation(async (url, init) => {
    if (url.endsWith("/schema")) return { fields } as never;
    if (url === "/api/jobs") return [] as never;
    if (url.startsWith("/api/runs?")) return { runs: [...runs], total: runs.length } as never;
    if (url.endsWith("display.json")) return { time_s: [0, 0.001], voltage_v: [1, 2], channel: 1, sample_count: 2, actual_rate_sa_s: 1000, actual_duration_s: 0.001,
      vertical_scale_v_div: 0.25, vertical_offset_v: 0.5, vertical_range_min_v: -0.5, vertical_range_max_v: 1.5,
      overrange: true, overrange_fraction: 0.5 } as never;
    if (url.endsWith("psd.json")) return { frequency_hz: [0, 500], psd_v2_hz: [0, 0.01], frequency_resolution_hz: 500 } as never;
    if (init?.method === "DELETE") { runs = runs.filter(r => !url.endsWith(r.id)); return { ok: true } as never; }
    if (url.endsWith("preflight")) return { ok: true, errors: [] } as never;
    if (url.endsWith("/runs")) return { id: "job1", kind: "experiment:scope-capture", status: "queued", events: [] } as never;
    return { ok: true } as never;
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks(); });

describe("示波器采集页面", () => {
  it("schema 渲染触发和垂直参数并按输入提交", async () => {
    render(<ScopeCapturePage />);
    fireEvent.change(await screen.findByLabelText("触发通道"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("触发模式"), { target: { value: "SINGle" } });
    fireEvent.change(screen.getByLabelText("触发边沿"), { target: { value: "FALLing" } });
    fireEvent.change(screen.getByLabelText("触发电平 (V)"), { target: { value: "0.2" } });
    fireEvent.change(screen.getByLabelText("垂直档位 (V/div)"), { target: { value: "0.5" } });
    fireEvent.change(screen.getByLabelText("垂直偏置 (V)"), { target: { value: "-0.3" } });
    fireEvent.click(screen.getByRole("button", { name: "开始采集" }));
    await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/api/experiments/scope-capture/runs", expect.anything()));
    const options = mockApi.mock.calls.find(([url]) => url === "/api/experiments/scope-capture/runs")?.[1];
    expect(JSON.parse(String(options?.body)).parameters).toMatchObject({ trigger_channel: 3, trigger_mode: "SINGle",
      trigger_slope: "FALLing", trigger_level_v: 0.2, vertical_scale_v_div: 0.5, vertical_offset_v: -0.3 });
  });
  it("时域显示量程及超量程读数，频域移除量程元数据", async () => {
    render(<ScopeCapturePage />);
    fireEvent.click(await screen.findByLabelText("选择 run_a"));
    expect(await screen.findByText("超量程")).toBeInTheDocument();
    expect(screen.getByText("0.25 V/div")).toBeInTheDocument();
    expect(screen.getByText("0.5 V")).toBeInTheDocument();
    expect(screen.getByText("-0.5 ~ 1.5 V")).toBeInTheDocument();
    expect(screen.getByTestId("plot").textContent).toContain('"rangeMax":1.5');
    fireEvent.click(screen.getByRole("button", { name: "频域" }));
    expect(screen.queryByText("超量程")).not.toBeInTheDocument();
    expect(screen.getByTestId("plot").textContent).not.toContain('"rangeMax"');
  });
  it("历史多选、隐藏、颜色与模式切换互相独立", async () => {
    render(<ScopeCapturePage />);
    fireEvent.click(await screen.findByLabelText("选择 run_a"));
    fireEvent.click(screen.getByLabelText("选择 run_b"));
    await waitFor(() => expect(screen.getByTestId("plot").textContent).toContain('"id":"run_b"'));
    fireEvent.click(screen.getByLabelText("隐藏 run_a"));
    expect(screen.getByTestId("plot").textContent).not.toContain('"id":"run_a"');
    expect(screen.getByLabelText("选择 run_a")).toBeChecked();
    fireEvent.change(screen.getByLabelText("颜色 run_b"), { target: { value: "#ff0000" } });
    expect(screen.getByTestId("plot").textContent).toContain("#ff0000");
    fireEvent.click(screen.getByRole("button", { name: "频域" }));
    expect(screen.getByTestId("plot").textContent).toContain('"mode":"frequency"');
    expect(screen.getByTestId("plot").textContent).toContain('"y":[0,0.01]');
    expect(localStorage.getItem("exp-agent:scope-capture:colors")).toContain("#ff0000");
  });
  it("频域可分别切换频率轴和 PSD 轴的 log 坐标", async () => {
    render(<ScopeCapturePage />);
    fireEvent.click(await screen.findByLabelText("选择 run_a"));
    fireEvent.click(screen.getByRole("button", { name: "频域" }));
    expect(screen.getByTestId("plot").textContent).toContain('"xScale":"linear"');
    expect(screen.getByTestId("plot").textContent).toContain('"yScale":"linear"');
    fireEvent.click(screen.getByLabelText("频率轴 log"));
    expect(screen.getByTestId("plot").textContent).toContain('"xScale":"log"');
    expect(screen.getByTestId("plot").textContent).toContain('"yScale":"linear"');
    fireEvent.click(screen.getByLabelText("PSD 轴 log"));
    expect(screen.getByTestId("plot").textContent).toContain('"xScale":"log"');
    expect(screen.getByTestId("plot").textContent).toContain('"yScale":"log"');
  });
  it("删除只发送选定运行，取消确认不删除", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<ScopeCapturePage />);
    fireEvent.click(await screen.findByLabelText("选择 run_a"));
    fireEvent.click(screen.getByLabelText("删除选定历史数据"));
    expect(mockApi.mock.calls.some(([, options]) => options?.method === "DELETE")).toBe(false);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByLabelText("删除选定历史数据"));
    await waitFor(() => expect(screen.queryByLabelText("选择 run_a")).not.toBeInTheDocument());
    expect(screen.getByLabelText("选择 run_b")).toBeInTheDocument();
    expect(mockApi).toHaveBeenCalledWith("/api/runs/scope-capture/run_a", { method: "DELETE" });
  });
  it("开始采集提交一个通道和当前模式", async () => {
    render(<ScopeCapturePage />);
    fireEvent.change(await screen.findByLabelText("采集通道"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "频域" }));
    fireEvent.click(screen.getByRole("button", { name: "开始采集" }));
    await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/api/experiments/scope-capture/runs", expect.anything()));
    const options = mockApi.mock.calls.find(([url]) => url === "/api/experiments/scope-capture/runs")?.[1];
    expect(JSON.parse(String(options?.body)).parameters).toMatchObject({ channel: 2, mode: "frequency", sampling_rate_sa_s: 1000 });
  });
});
