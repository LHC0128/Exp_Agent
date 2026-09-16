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

type Layout = { basic: string[]; advanced: string[] };

const field = (overrides: Record<string, unknown> = {}) => ({
  name: "TARGET_RELATIVE_RMS",
  label: "相对 RMS 误差阈值",
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

// 与后端 schema 一致：五个指定参数默认落在高级参数。
const BASIC_FIELDS = [
  field({ name: "RUN_TAG", label: "运行标签", type: "string", default: "z_aw_closed_loop_waveform_correction" }),
  field(),
  field({
    name: "MAX_ITERATIONS",
    label: "最大测量轮数（含初始轮）",
    type: "integer",
    default: 100,
    minimum: 1,
  }),
];

const ADVANCED_FIELDS = [
  field({ name: "CONTROL_SCALE", label: "控制幅度比例", group: "advanced", default: 1, minimum: 0.000001 }),
  field({
    name: "TRIGGER_FREQUENCY_HZ",
    label: "共同触发频率",
    unit: "Hz",
    group: "advanced",
    default: 100,
    minimum: 0.001,
  }),
  field({
    name: "TRIGGER_AMPLITUDE_VPP",
    label: "共同触发幅度",
    unit: "Vpp",
    group: "advanced",
    default: 10,
    minimum: 0.001,
  }),
  field({
    name: "SCOPE_CYCLES",
    label: "每次采集周期数",
    type: "integer",
    group: "advanced",
    default: 3,
    minimum: 2,
  }),
  field({
    name: "SCOPE_HEADROOM_FACTOR",
    label: "理论电压量程余量倍数",
    group: "advanced",
    default: 1.5,
    minimum: 1,
  }),
  field({ name: "REQUIRED_PASSES", label: "连续达标轮数", type: "integer", group: "advanced", default: 3, minimum: 1 }),
];

const MOVED_DEFAULTS = [
  "控制幅度比例",
  "共同触发频率 (Hz)",
  "共同触发幅度 (Vpp)",
  "每次采集周期数",
  "理论电压量程余量倍数",
];

const ALL_FIELDS = [...BASIC_FIELDS, ...ADVANCED_FIELDS];

const defaultLayout = (): Layout => ({
  basic: BASIC_FIELDS.map((item) => item.name),
  advanced: ADVANCED_FIELDS.map((item) => item.name),
});

const fieldsOf = (layout: Layout) => {
  const byName = new Map(ALL_FIELDS.map((item) => [item.name, item]));
  return [
    ...layout.basic.map((name) => ({ ...byName.get(name), group: "basic" })),
    ...layout.advanced.map((name) => ({ ...byName.get(name), group: "advanced" })),
  ];
};

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
  parameters: { TARGET_RELATIVE_RMS: 0.05, MAX_ITERATIONS: 6 },
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
// 模拟磁盘上的参数分类：默认来自后端 catalog，保存后被覆盖。
let persistedLayout: Layout;
let layoutSaved = true;
let submittedDefaults: Array<{ parameters: Record<string, unknown>; parameter_layout: Layout }> = [];

beforeEach(() => {
  activity.hardwareBusy = false;
  activity.activeJobs = [];
  definitionCalls = 0;
  persistedLayout = defaultLayout();
  layoutSaved = true;
  submittedDefaults = [];
  mockApi.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/z-aw-closed-loop-waveform-correction/schema")) {
      return {
        fields: fieldsOf(persistedLayout),
        parameter_layout: persistedLayout,
        parameter_layout_saved: layoutSaved,
      } as never;
    }
    if (url.endsWith("/derive")) {
      return { ok: true, values: {} } as never;
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
    if (url.endsWith("/defaults") && init?.method === "PUT") {
      const payload = JSON.parse(String(init.body)) as {
        parameters: Record<string, unknown>;
        parameter_layout: Layout;
      };
      const submitted = [...payload.parameter_layout.basic, ...payload.parameter_layout.advanced];
      const names = ALL_FIELDS.map((item) => item.name);
      // 后端严格校验：所有可见参数必须完整、无重复地出现在两个列表中。
      const complete = submitted.length === names.length
        && new Set(submitted).size === names.length
        && names.every((name) => submitted.includes(name));
      if (!complete) throw new Error("分类中包含未知参数或参数未归入基础或高级分类");
      submittedDefaults.push(payload);
      persistedLayout = {
        basic: [...payload.parameter_layout.basic],
        advanced: [...payload.parameter_layout.advanced],
      };
      layoutSaved = true;
      return {
        ok: true,
        message: "当前参数及分类已保存为默认值",
        schema: {
          fields: fieldsOf(persistedLayout),
          parameter_layout: persistedLayout,
          parameter_layout_saved: true,
        },
      } as never;
    }
    if (url.endsWith("/runs") && init?.method === "POST") {
      return {
        id: "job1",
        kind: "experiment:z-aw-closed-loop-waveform-correction",
        status: "running",
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

const openAdvanced = async () => {
  const tab = await screen.findByRole("tab", { name: /^高级参数，/ });
  fireEvent.click(tab);
  return tab;
};

const openBasic = async () => {
  const tab = await screen.findByRole("tab", { name: /^基础参数，/ });
  fireEvent.click(tab);
  return tab;
};

const saveButton = () => screen.getByRole("button", { name: "保存参数与布局" });

describe("ZAWClosedLoopExperimentPage", () => {
  it("载入 schema，渲染页面 Tab、参数分组标签和基础参数", async () => {
    show();
    await waitFor(() => expect(definitionCalls).toBeGreaterThan(0));
    expect(await screen.findByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "基础参数，3 项" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "高级参数，6 项" })).toHaveAttribute("aria-selected", "false");
    const targetField = await screen.findByLabelText("相对 RMS 误差阈值");
    expect(targetField).toHaveValue(0.03);
  });

  it("五个指定参数默认出现在高级参数，未保存过布局时也按模型默认分组", async () => {
    layoutSaved = false;
    show();
    await screen.findByRole("tab", { name: "基础参数，3 项" });
    expect(screen.getByRole("tab", { name: "高级参数，6 项" })).toBeInTheDocument();
    await openAdvanced();
    for (const label of MOVED_DEFAULTS) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    await openBasic();
    for (const label of MOVED_DEFAULTS) {
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument();
    }
  });

  it("可在基础参数和高级参数标签之间切换", async () => {
    show();
    expect(await screen.findByLabelText("相对 RMS 误差阈值")).toBeInTheDocument();
    await openAdvanced();
    expect(screen.queryByLabelText("相对 RMS 误差阈值")).not.toBeInTheDocument();
    expect(screen.getByLabelText("连续达标轮数")).toHaveValue(3);
    expect(screen.getByRole("tab", { name: "高级参数，6 项" })).toHaveAttribute("aria-selected", "true");
  });

  it("基础参数可移至高级再移回基础，计数与参数值都不变", async () => {
    show();
    const targetField = await screen.findByLabelText("相对 RMS 误差阈值");
    fireEvent.change(targetField, { target: { value: "0.08" } });
    expect(targetField).toHaveValue(0.08);

    // 基础 → 高级
    fireEvent.click(await screen.findByRole("button", { name: "相对 RMS 误差阈值 移至高级参数" }));
    expect(screen.getByRole("tab", { name: "基础参数，2 项" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "高级参数，7 项" })).toBeInTheDocument();
    expect(screen.queryByLabelText("相对 RMS 误差阈值")).not.toBeInTheDocument();

    await openAdvanced();
    const movedField = screen.getByLabelText("相对 RMS 误差阈值");
    expect(movedField).toHaveValue(0.08);

    // 高级 → 基础
    fireEvent.click(screen.getByRole("button", { name: "相对 RMS 误差阈值 移至基础参数" }));
    expect(screen.getByRole("tab", { name: "基础参数，3 项" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "高级参数，6 项" })).toBeInTheDocument();
    expect(screen.queryByLabelText("相对 RMS 误差阈值")).not.toBeInTheDocument();

    await openBasic();
    expect(screen.getByLabelText("相对 RMS 误差阈值")).toHaveValue(0.08);
  });

  it("移动参数后提示未保存，点击保存提交完整参数与布局并回读确认", async () => {
    show();
    await screen.findByLabelText("相对 RMS 误差阈值");
    expect(screen.queryByText("布局有未保存修改")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "最大测量轮数（含初始轮） 移至高级参数" }));
    expect(await screen.findByText("布局有未保存修改")).toBeInTheDocument();

    fireEvent.click(saveButton());
    await waitFor(() => expect(submittedDefaults).toHaveLength(1));
    const payload = submittedDefaults[0];
    expect(payload.parameter_layout).toEqual({
      basic: ["RUN_TAG", "TARGET_RELATIVE_RMS"],
      advanced: [...ADVANCED_FIELDS.map((item) => item.name), "MAX_ITERATIONS"],
    });
    // 参数值同时提交，且覆盖全部可见参数。
    expect(Object.keys(payload.parameters).sort()).toEqual(ALL_FIELDS.map((item) => item.name).sort());
    expect(payload.parameters.MAX_ITERATIONS).toBe(100);
    // 回读 schema 确认磁盘布局与页面一致后清除未保存提示。
    await waitFor(() => expect(screen.queryByText("布局有未保存修改")).not.toBeInTheDocument());
    expect(screen.getByText("当前参数及分类已保存为默认值")).toBeInTheDocument();
  });

  it("重新载入页面后恢复已保存的布局", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "相对 RMS 误差阈值 移至高级参数" }));
    fireEvent.click(saveButton());
    await waitFor(() => expect(submittedDefaults).toHaveLength(1));
    cleanup();

    show();
    expect(await screen.findByRole("tab", { name: "基础参数，2 项" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "高级参数，7 项" })).toBeInTheDocument();
    await openAdvanced();
    expect(screen.getByLabelText("相对 RMS 误差阈值")).toBeInTheDocument();
  });

  it("运行期间禁止移动参数", async () => {
    show();
    await screen.findByLabelText("相对 RMS 误差阈值");
    fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));

    await waitFor(() => expect(
      screen.getByRole("button", { name: "相对 RMS 误差阈值 移至高级参数" }),
    ).toBeDisabled());
    expect(screen.getByRole("button", { name: "实验运行中" })).toBeInTheDocument();
  });

  it("Tab 切换到历史再切回运行，参数保留", async () => {
    show();
    const targetField = await screen.findByLabelText("相对 RMS 误差阈值");
    fireEvent.change(targetField, { target: { value: "0.08" } });
    fireEvent.click(await screen.findByRole("tab", { name: "历史" }));
    expect(await screen.findByText("obbv8")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("tab", { name: "运行" }));
    expect(screen.getByLabelText("相对 RMS 误差阈值")).toHaveValue(0.08);
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
    expect(screen.getByLabelText("相对 RMS 误差阈值")).toHaveValue(0.05);
  });
});
