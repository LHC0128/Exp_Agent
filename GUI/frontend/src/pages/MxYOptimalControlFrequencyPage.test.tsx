import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
const activity = vi.hoisted(() => ({ hardwareBusy: false, activeJobs: [] }));
vi.mock("../components/JobView", () => ({ JobView: () => null }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => activity }));

const mockApi = vi.mocked(api);

type Layout = { basic: string[]; advanced: string[] };

const field = (overrides: Record<string, unknown> = {}) => ({
  name: "FREQUENCY_POINTS",
  label: "频率点数",
  type: "integer",
  group: "basic",
  default: 100,
  minimum: 1,
  maximum: null,
  unit: "",
  visible: true,
  read_only: false,
  ...overrides,
});

const BASIC_FIELDS = [
  field({ name: "RUN_TAG", label: "运行标签", type: "string", default: "mx_y_optimal_control_rf_freq_resp" }),
  field({ name: "COMPARISON_ENABLED", label: "启用常数 Z 控制对照", type: "boolean", default: false }),
  field({ name: "FREQUENCY_START_HZ", label: "频率扫描起点", type: "number", default: 100, unit: "Hz" }),
  field(),
  field({
    name: "CONSTANT_CONTROL_LARMOR_FREQUENCY_HZ",
    label: "常数控制目标 Larmor 频率",
    type: "number",
    default: 0,
    unit: "Hz",
  }),
  field({
    name: "CONSTANT_CONTROL_CALIBRATION_SOURCE_RUN",
    label: "电流耦合标定运行",
    type: "string",
    default: "",
    options: [
      {
        value: "0820_170037_mx_z_current_coupling_calibration",
        label: "0820_170037_mx_z_current_coupling_calibration",
      },
    ],
  }),
];

const ADVANCED_FIELDS = [
  field({ name: "FREQUENCY_SETTLE_TIME_S", label: "频点稳定时间", type: "number", group: "advanced", default: 0.05, unit: "s" }),
  field({ name: "R_POINT_MAX_ATTEMPTS", label: "最大尝试次数", type: "integer", group: "advanced", default: 3 }),
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
  id: "mx-y-optimal-control-rf-frequency-response",
  title: "Mx Y 最优控制 RF 频率响应",
  category: "measurement",
  category_label: "测量",
  family: "rf-sensitivity",
  variant: "mx-y-optimal-control-frequency",
  description: "Z 向闭环最优控制下逐频率扫描 Y RF Burst 相位并取相位中位数。",
  required_devices: ["GS200", "DG900", "DG4000", "HF2"],
  required_mapping_keys: [],
  execution_mode: "typed_workflow",
  acquisition_program: "experiments/Mx_Y_Optimal_Control_RF_Frequency_Response.py",
  analysis_program: "experiments/Mx_Y_Optimal_Control_RF_Frequency_Response_plot.py",
  wiring_notes: [],
  safety_notes: [],
  supports_cancel: true,
  can_analyze: true,
};

const runSummary = {
  run_id: "0901_120000_mx_y_optimal_control_rf_freq_resp",
  timestamp: "2026-09-01 12:00:00",
  run_tag: "mx_y_optimal_control_rf_freq_resp",
  completion_status: "completed",
  comparison_enabled: true,
  parameters: { FREQUENCY_POINTS: 100, COMPARISON_ENABLED: true },
  calibration: {
    enabled: true,
    calibration_run: "0820_170037_mx_z_current_coupling_calibration",
    calibration_analysis_sha256: "a".repeat(64),
    target_larmor_frequency_hz: 800,
    target_current_a: 2.6e-5,
    target_voltage_v: -0.0005,
    voltage_to_current_fit: { gain_a_per_v: 0.00575, intercept_a: 2.9e-5, point_count: 7 },
    calibration_support: { voltage_min_v: 0.5, voltage_max_v: 2.0 },
    extrapolated: true,
  },
  optimal_response_metadata: { valid_points: 350, total_points: 400 },
  constant_response_metadata: { valid_points: 100, total_points: 100 },
  artifacts: {
    "rf_frequency_response_comparison.png":
      "/api/runs/mx-y-optimal-control-rf-frequency-response/0901_120000_mx_y_optimal_control_rf_freq_resp/artifacts/rf_frequency_response_comparison.png",
    "r_phase_frequency.png":
      "/api/runs/mx-y-optimal-control-rf-frequency-response/0901_120000_mx_y_optimal_control_rf_freq_resp/artifacts/r_phase_frequency.png",
  },
};

const runList = {
  total: 1,
  offset: 0,
  limit: 50,
  runs: [
    {
      run_id: "0901_120000_mx_y_optimal_control_rf_freq_resp",
      timestamp: "2026-09-01 12:00:00",
      run_tag: "mx_y_optimal_control_rf_freq_resp",
      completion_status: "completed",
      comparison_enabled: true,
    },
  ],
};

let definitionCalls = 0;
let persistedLayout: Layout;
let layoutSaved = true;
let submittedDefaults: Array<{ parameters: Record<string, unknown>; parameter_layout: Layout }> = [];
let submittedPreflight: Array<Record<string, unknown>> = [];
let runListCalls = 0;
let jobsSummaries: Array<Record<string, unknown>> = [];

const KIND = "experiment:mx-y-optimal-control-rf-frequency-response";

const jobSummary = (overrides: Record<string, unknown> = {}) => ({
  id: "job-active",
  kind: KIND,
  status: "running",
  stage: "optimal_control_scan",
  percent: 45,
  estimated_remaining_seconds: 125,
  message: "最优控制扫描：频率 2/10，相位 3/36",
  created_at: "2026-09-13T10:00:00",
  started_at: "2026-09-13T10:00:01",
  finished_at: null,
  ...overrides,
});

const jobDetail = (overrides: Record<string, unknown> = {}) => ({
  ...jobSummary(overrides),
  result: null,
  error: null,
  events: [],
  ...overrides,
});

beforeEach(() => {
  activity.hardwareBusy = false;
  activity.activeJobs = [];
  definitionCalls = 0;
  persistedLayout = defaultLayout();
  layoutSaved = true;
  submittedDefaults = [];
  submittedPreflight = [];
  runListCalls = 0;
  jobsSummaries = [];
  mockApi.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === "/api/jobs") {
      return jobsSummaries as never;
    }
    if (/^\/api\/jobs\/[a-z0-9-]+$/.test(url)) {
      const summary = jobsSummaries.find((item) => item.id === url.split("/").pop());
      return jobDetail(summary ?? {}) as never;
    }
    if (url.endsWith("/mx-y-optimal-control-rf-frequency-response/schema")) {
      return {
        fields: fieldsOf(persistedLayout),
        parameter_layout: persistedLayout,
        parameter_layout_saved: layoutSaved,
      } as never;
    }
    if (url === "/api/experiments/mx-y-optimal-control-rf-frequency-response") {
      definitionCalls += 1;
      return definition as never;
    }
    // 专属运行列表端点：不带 experiment_id 查询参数。
    if (url.startsWith("/api/experiments/mx-y-optimal-control-rf-frequency-response/runs?")) {
      runListCalls += 1;
      expect(url).not.toContain("experiment_id=");
      return runList as never;
    }
    if (url.endsWith("/summary")) {
      return runSummary as never;
    }
    if (url.endsWith("/preflight")) {
      submittedPreflight.push(
        JSON.parse(String(init?.body)).parameters as Record<string, unknown>,
      );
      return { ok: true, errors: [] } as never;
    }
    if (url.endsWith("/defaults") && init?.method === "PUT") {
      const payload = JSON.parse(String(init.body)) as {
        parameters: Record<string, unknown>;
        parameter_layout: Layout;
      };
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
        kind: "experiment:mx-y-optimal-control-rf-frequency-response",
        status: "running",
        events: [],
        result: {
          run_dir:
            "data/Mx_Y_Optimal_Control_RF_Frequency_Response/0901_120000_mx_y_optimal_control_rf_freq_resp",
        },
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
    <MemoryRouter initialEntries={["/experiments/mx-y-optimal-control-rf-frequency-response"]}>
      <Routes>
        <Route path="/experiments/:experimentId" element={<ExperimentPage />} />
      </Routes>
    </MemoryRouter>,
  );

const openBasic = async () => {
  const tab = await screen.findByRole("tab", { name: /^基础参数，/ });
  fireEvent.click(tab);
  return tab;
};

const toggleComparison = async (on: boolean) => {
  const select = await screen.findByLabelText("启用常数 Z 控制对照");
  fireEvent.change(select, { target: { value: on ? "true" : "false" } });
};

describe("MxYOptimalControlFrequencyPage", () => {
  it("专属路由载入 schema 并渲染页面 Tab 与参数分组", async () => {
    show();
    await waitFor(() => expect(definitionCalls).toBeGreaterThan(0));
    expect(await screen.findByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("tab", { name: /^基础参数，/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^高级参数，/ })).toBeInTheDocument();
    expect(await screen.findByLabelText("运行标签")).toBeInTheDocument();
  });

  it("页面显示三个 Tab：运行、历史、架构，默认运行", async () => {
    show();
    expect(await screen.findByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "历史" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "架构" })).toBeInTheDocument();
    expect(screen.queryByTitle("Mx Y 最优控制频率响应实验采集流程架构图")).not.toBeInTheDocument();
  });

  it("点击架构 Tab 后显示 iframe，src 与新页面打开链接一致，且有中文无障碍标题", async () => {
    show();
    fireEvent.click(await screen.findByRole("tab", { name: "架构" }));
    const iframe = await screen.findByTitle("Mx Y 最优控制频率响应实验采集流程架构图");
    expect(iframe).toHaveAttribute(
      "src",
      "/architecture/mx-y-optimal-control-rf-frequency-response.html",
    );
    const link = screen.getByRole("link", { name: "在新页面打开" });
    expect(link).toHaveAttribute(
      "href",
      "/architecture/mx-y-optimal-control-rf-frequency-response.html",
    );
  });

  it("架构 Tab 不触发额外后端实验运行请求", async () => {
    show();
    await screen.findByRole("tab", { name: "运行" });
    const definitionCallsBefore = definitionCalls;
    const runListCallsBefore = runListCalls;
    fireEvent.click(screen.getByRole("tab", { name: "架构" }));
    expect(await screen.findByTitle("Mx Y 最优控制频率响应实验采集流程架构图")).toBeInTheDocument();
    expect(definitionCalls).toBe(definitionCallsBefore);
    expect(runListCalls).toBe(runListCallsBefore);
  });

  it("ArrowLeft / ArrowRight 可以在三个 Tab 间循环切换", async () => {
    show();
    const tablist = await screen.findByRole("tablist", {
      name: "Mx Y 最优控制频率响应实验视图",
    });
    const runTab = screen.getByRole("tab", { name: "运行" });
    runTab.focus();
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "历史" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "架构" })).toHaveAttribute("aria-selected", "true");
    // 架构 → 运行 循环回绕。
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
    // 运行 → 架构 反向回绕。
    runTab.focus();
    fireEvent.keyDown(tablist, { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "架构" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(tablist, { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "历史" })).toHaveAttribute("aria-selected", "true");
  });

  it("对照关闭时隐藏 Larmor 频率与标定下拉框，且保留其取值", async () => {
    show();
    await screen.findByLabelText("运行标签");
    expect(screen.getByLabelText("启用常数 Z 控制对照")).toBeInTheDocument();
    expect(screen.queryByLabelText("常数控制目标 Larmor 频率 (Hz)")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("电流耦合标定运行")).not.toBeInTheDocument();
    // 基础参数 Tab 计数不含被隐藏的两个对照字段。
    expect(screen.getByRole("tab", { name: /^基础参数，4 项$/ })).toBeInTheDocument();
  });

  it("对照开启时显示 Larmor 频率与标定下拉框，并可选择标定运行", async () => {
    show();
    await screen.findByLabelText("运行标签");
    await toggleComparison(true);

    const larmor = await screen.findByLabelText("常数控制目标 Larmor 频率 (Hz)");
    fireEvent.change(larmor, { target: { value: "800" } });
    expect(larmor).toHaveValue(800);

    const calibration = screen.getByLabelText("电流耦合标定运行");
    fireEvent.change(calibration, {
      target: { value: "0820_170037_mx_z_current_coupling_calibration" },
    });
    expect(calibration).toHaveValue("0820_170037_mx_z_current_coupling_calibration");
    expect(screen.getByRole("tab", { name: /^基础参数，6 项$/ })).toBeInTheDocument();
  });

  it("关闭对照后重新开启，之前填写的对照参数值不丢失", async () => {
    show();
    await screen.findByLabelText("运行标签");
    await toggleComparison(true);
    fireEvent.change(await screen.findByLabelText("常数控制目标 Larmor 频率 (Hz)"), {
      target: { value: "1234" },
    });
    await toggleComparison(false);
    await toggleComparison(true);
    expect(screen.getByLabelText("常数控制目标 Larmor 频率 (Hz)")).toHaveValue(1234);
  });

  it("移动参数后视图停留在当前分组、参数值不变，且提示已移动", async () => {
    show();
    const frequencyPoints = await screen.findByLabelText("频率点数");
    fireEvent.change(frequencyPoints, { target: { value: "256" } });
    expect(frequencyPoints).toHaveValue(256);

    fireEvent.click(screen.getByRole("button", { name: "频率点数 移至高级参数" }));

    // 仍停留在基础参数，移出的字段从本组列表消失。
    expect(await screen.findByText("已移至高级参数")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^基础参数，/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByLabelText("频率点数")).not.toBeInTheDocument();

    // 切到高级参数可看到该字段，且原值不变。
    fireEvent.click(screen.getByRole("tab", { name: /^高级参数，3 项$/ }));
    expect(screen.getByLabelText("频率点数")).toHaveValue(256);
  });

  it("从高级参数移回基础参数时也停留在高级参数", async () => {
    show();
    await screen.findByLabelText("运行标签");
    fireEvent.click(screen.getByRole("tab", { name: /^高级参数，/ }));
    const settle = screen.getByLabelText("频点稳定时间 (s)");
    fireEvent.change(settle, { target: { value: "0.25" } });

    fireEvent.click(screen.getByRole("button", { name: "频点稳定时间 移至基础参数" }));

    expect(await screen.findByText("已移至基础参数")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^高级参数，/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByLabelText("频点稳定时间 (s)")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /^基础参数，/ }));
    expect(screen.getByLabelText("频点稳定时间 (s)")).toHaveValue(0.25);
  });

  it("保存参数与布局后提交完整参数并回读清除未保存提示", async () => {
    show();
    await screen.findByLabelText("运行标签");
    await toggleComparison(true);
    fireEvent.change(await screen.findByLabelText("常数控制目标 Larmor 频率 (Hz)"), {
      target: { value: "800" },
    });

    expect(screen.queryByText("布局有未保存修改")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "运行标签 移至高级参数" }));
    expect(await screen.findByText("布局有未保存修改")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
    await waitFor(() => expect(submittedDefaults).toHaveLength(1));
    const payload = submittedDefaults[0];
    expect(payload.parameter_layout.advanced).toContain("RUN_TAG");
    // 隐藏的对照字段也随参数一起提交，取值不丢失。
    expect(payload.parameters.CONSTANT_CONTROL_LARMOR_FREQUENCY_HZ).toBe(800);
    await waitFor(() => expect(screen.queryByText("布局有未保存修改")).not.toBeInTheDocument());
    expect(screen.getByText("当前参数及分类已保存为默认值")).toBeInTheDocument();
  });

  it("预检并运行时提交当前全部参数", async () => {
    show();
    await screen.findByLabelText("运行标签");
    await toggleComparison(true);
    fireEvent.change(await screen.findByLabelText("常数控制目标 Larmor 频率 (Hz)"), {
      target: { value: "800" },
    });

    fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
    await waitFor(() => expect(submittedPreflight.length).toBeGreaterThan(0));
    const params = submittedPreflight[submittedPreflight.length - 1];
    expect(params.COMPARISON_ENABLED).toBe(true);
    expect(params.CONSTANT_CONTROL_LARMOR_FREQUENCY_HZ).toBe(800);
    expect(await screen.findByRole("button", { name: "实验运行中" })).toBeInTheDocument();
  });

  it("运行中禁止移动参数", async () => {
    show();
    await screen.findByLabelText("运行标签");
    fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "运行标签 移至高级参数" })).toBeDisabled(),
    );
  });

  it("历史列表只请求专属端点，选中后展示摘要、图与外推警告，可填回参数", async () => {
    show();
    fireEvent.click(await screen.findByRole("tab", { name: "历史" }));
    expect(runListCalls).toBeGreaterThan(0);

    const runButton = await screen.findByRole("button", {
      name: /0901_120000_mx_y_optimal_control_rf_freq_resp/,
    });
    fireEvent.click(runButton);

    // 外推警告与标定信息。
    expect(
      await screen.findByText(/超出标定支撑范围|超出标定有效范围/),
    ).toBeInTheDocument();
    expect(screen.getByText("0820_170037_mx_z_current_coupling_calibration")).toBeInTheDocument();

    // 两张图使用后端给出的 artifact URL。
    const comparisonImage = screen.getByAltText("最优控制中位数 vs 常数控制对照");
    expect(comparisonImage).toHaveAttribute(
      "src",
      runSummary.artifacts["rf_frequency_response_comparison.png"],
    );
    const heatmap = screen.getByAltText("相位-频率热图");
    expect(heatmap).toHaveAttribute(
      "src",
      runSummary.artifacts["r_phase_frequency.png"],
    );

    fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
    expect(await screen.findByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("频率点数")).toHaveValue(100);
  });

  it("Tab 切到历史再切回运行，参数保留", async () => {
    show();
    const frequencyPoints = await screen.findByLabelText("频率点数");
    fireEvent.change(frequencyPoints, { target: { value: "77" } });
    fireEvent.click(await screen.findByRole("tab", { name: "历史" }));
    fireEvent.click(await screen.findByRole("tab", { name: "运行" }));
    expect(screen.getByLabelText("频率点数")).toHaveValue(77);
  });

  it("挂载时恢复本实验的活动任务", async () => {
    jobsSummaries = [
      jobSummary({ id: "job-old", status: "completed", percent: 100, started_at: "2026-09-12T09:00:00" }),
      jobSummary(),
    ];
    show();
    expect(await screen.findByRole("button", { name: "实验运行中" })).toBeInTheDocument();
    // 运行中禁止移动参数，说明恢复的任务驱动了控制区状态。
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "运行标签 移至高级参数" })).toBeDisabled(),
    );
  });

  it("没有活动任务时恢复最近一次已开始的任务", async () => {
    jobsSummaries = [
      jobSummary({
        id: "job-done",
        status: "completed",
        stage: "complete",
        percent: 100,
        estimated_remaining_seconds: null,
        message: "任务完成",
        finished_at: "2026-09-13T11:00:00",
      }),
      jobSummary({ id: "job-other", kind: "experiment:other-experiment" }),
    ];
    show();
    await screen.findByRole("button", { name: "预检并运行实验" });
    // 已完成任务不阻塞新运行，但最近一次结果通过恢复的 job 展示。
    await screen.findByText("尚无运行结果，启动一次实验后这里会展示频率响应曲线与相位-频率热图。");
  });

  it("运行后切到历史再切回运行，任务状态不丢失", async () => {
    show();
    await screen.findByLabelText("运行标签");
    fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
    expect(await screen.findByRole("button", { name: "实验运行中" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "历史" }));
    fireEvent.click(await screen.findByRole("tab", { name: "运行" }));
    expect(await screen.findByRole("button", { name: "实验运行中" })).toBeInTheDocument();
  });

  it("未保存过布局时按模型默认分组渲染", async () => {
    layoutSaved = false;
    show();
    await openBasic();
    expect(screen.getByLabelText("运行标签")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /^高级参数，/ }));
    expect(screen.getByLabelText("频点稳定时间 (s)")).toHaveValue(0.05);
  });
});
