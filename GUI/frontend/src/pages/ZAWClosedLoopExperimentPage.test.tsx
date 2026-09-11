import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { ZAWClosedLoopExperimentPage } from "./ZAWClosedLoopExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
const activity = vi.hoisted(() => ({ hardwareBusy: false, activeJobs: [] }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => activity }));

const mockApi = vi.mocked(api);

const field = (overrides: Record<string, unknown> = {}) => ({
  name: "TARGET_SHAPE_NRMSE",
  label: "目标波形相对误差",
  type: "number",
  group: "basic",
  default: 0.03,
  minimum: 0,
  maximum: 1,
  unit: "",
  visible: true,
  read_only: false,
  ...overrides,
});

const definition = {
  id: "z-aw-closed-loop-waveform-correction",
  title: "Z 任意波实际电流闭环校正",
  category: "z-closed-loop",
  category_label: "Z 闭环",
  family: "z",
  variant: "closed-loop",
  description: "通过多次迭代让 Z 线圈实测电流逼近目标波形。",
  required_devices: ["GS200", "HF2", "DG4000", "SDS"],
  required_mapping_keys: ["Z_magnetic_field", "Z_coil_current"],
  execution_mode: "typed_workflow",
  acquisition_program: "z_aw_closed_loop_waveform_correction",
  analysis_program: null,
  wiring_notes: [],
  safety_notes: [],
  supports_cancel: true,
  can_analyze: true,
};

const runSummary = {
  run_id: "obbv8",
  timestamp: "2026-09-11 14:30:00",
  run_tag: "z_aw_closed_loop_waveform_correction",
  target_reached: true,
  completion_status: "completed",
  stop_reason: "达标",
  iteration_count: 4,
  best_iteration: 3,
  best_relative_rms_error: 0.0049,
  target_relative_rms: 0.03,
  static_calibration: { source_run: "0819_133941_mx_z_cal", gain_a_per_v: 0.0123 },
  parameters: { TARGET_SHAPE_NRMSE: 0.05, MAX_ITERATIONS: 6 },
  artifacts: {
    "convergence.png": "/api/runs/z-aw-closed-loop-waveform-correction/obbv8/artifacts/convergence.png",
    "waveform_comparison.png": "/api/runs/z-aw-closed-loop-waveform-correction/obbv8/artifacts/waveform_comparison.png",
  },
};

const runList = {
  total: 1,
  offset: 0,
  limit: 50,
  runs: [
    {
      id: "obbv8",
      experiment_id: "z-aw-closed-loop-waveform-correction",
      experiment_title: "Z 任意波实际电流闭环校正",
      path: "data/Z_AW_Closed_Loop_Waveform_Correction/obbv8",
      artifacts: ["convergence.png", "waveform_comparison.png"],
      can_analyze: true,
      modified_at: 1726050600,
    },
  ],
};

let definitionCalls = 0;
let schemaCalls = 0;

beforeEach(() => {
  activity.hardwareBusy = false;
  activity.activeJobs = [];
  definitionCalls = 0;
  schemaCalls = 0;
  mockApi.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/z-aw-closed-loop-waveform-correction/schema")) {
      schemaCalls += 1;
      return { fields: [field()] } as never;
    }
    if (url.endsWith("/derive")) {
      return { ok: true, values: { TARGET_SHAPE_NRMSE: 0.03 } } as never;
    }
    if (url === "/api/experiments/z-aw-closed-loop-waveform-correction") {
      definitionCalls += 1;
      return definition as never;
    }
    if (url.startsWith("/api/runs?") && !init?.method) {
      return runList as never;
    }
    if (url.endsWith("/runs/obbv8/summary")) {
      return runSummary as never;
    }
    if (url.endsWith("/preflight")) {
      return { ok: true, errors: [] } as never;
    }
    if (url.endsWith("/runs") && init?.method === "POST") {
      return {
        id: "job1",
        kind: "experiment:z-aw-closed-loop-waveform-correction",
        status: "completed",
        events: [],
        result: { run_dir: "data/Z_AW_Closed_Loop_Waveform_Correction/obbv8" },
      } as never;
    }
    return {} as never;
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const show = () =>
  render(
    <MemoryRouter initialEntries={["/experiments/z-aw-closed-loop-waveform-correction"]}>
      <ZAWClosedLoopExperimentPage />
    </MemoryRouter>,
  );

describe("ZAWClosedLoopExperimentPage", () => {
  it("载入 schema，渲染 Tab 切换和参数表单", async () => {
    show();
    await waitFor(() => expect(definitionCalls).toBeGreaterThan(0));
    expect(await screen.findByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
    const targetField = await screen.findByLabelText("目标波形相对误差");
    expect(targetField).toHaveValue(0.03);
  });

  it("Tab 切换到历史再切回运行，参数保留", async () => {
    show();
    const targetField = await screen.findByLabelText("目标波形相对误差");
    fireEvent.change(targetField, { target: { value: "0.08" } });
    fireEvent.click(await screen.findByRole("tab", { name: "历史" }));
    expect(await screen.findByText("obbv8")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("tab", { name: "运行" }));
    expect(screen.getByLabelText("目标波形相对误差")).toHaveValue(0.08);
  });

  it("选中历史后渲染指标、图与参数表，点击「填回参数」写回 values 并切回运行 Tab", async () => {
    show();
    fireEvent.click(await screen.findByRole("tab", { name: "历史" }));
    const runButton = await screen.findByRole("button", { name: /obbv8/ });
    fireEvent.click(runButton);
    expect(await screen.findByText("已达标")).toBeInTheDocument();
    expect(await screen.findByText("0.490%")).toBeInTheDocument();
    const fillBack = await screen.findByRole("button", { name: "填回参数" });
    fireEvent.click(fillBack);
    expect(await screen.findByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("目标波形相对误差")).toHaveValue(0.05);
  });
});
