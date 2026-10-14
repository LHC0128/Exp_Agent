import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api";
import { ExperimentPage } from "./ExperimentPage";

vi.mock("../api", () => ({ api: vi.fn() }));
vi.mock("../components/JobActivity", () => ({ useJobActivity: () => ({ hardwareBusy: false, activeJobs: [] }) }));

const ID = "rf-sensitivity-aw";
const BASE = `/api/experiments/${ID}`;
const mockApi = vi.mocked(api);
let summary: any;
let historyRuns: any[] | undefined;
let managementError: string;
let jobs: any[];
let analysisJob: any;
let comparisonJob: any;
let comparisonRuns: any[];
let preflightOk: boolean;
let pageTotal: number;
let sources: MockEventSource[];
let activeSession: any;
let sessionJob: any;
let stepResult: any;
let parameterResult: any;
let parameterApplyError: boolean;
const fields = [
  { name: "RUN_TAG", label: "运行标签", type: "string", default: "rf_sens_aw", group: "basic" },
  { name: "ARB_WAVEFORM_FILE", label: "最优控制波形版本", type: "string", default: "", group: "basic", options: [{ value: "", label: "请选择最优控制版本" }, ...["v1", "v8"].map(version => ({ value: `${version}/waveforms/optimal_control_waveform.csv`, label: version }))] },
  ...["FREQ", "AMPLITUDE", "OFFSET", "DUTY", "PHASE"].map((suffix, index) => ({ name: `XY_TRIGGER_${suffix}`, label: `共同触发${suffix}`, type: "number", default: [100, 5, 2.5, 50, 0][index], group: "basic" })),
  { name: "DEMOD3_IDX", label: "RF 解调器索引", type: "integer", default: 3, group: "basic" },
  { name: "RF_INPUT_ROUTE", label: "RF 输入线路", type: "string", default: "physical", group: "basic", options: [{ value: "physical", label: "物理连接" }, { value: "internal", label: "内部回采（仅 MFLI）" }] },
  { name: "RF_AUX_OUTPUT_IDX", label: "RF 辅助输出通道", type: "integer", default: 1, group: "basic", options: [0, 1, 2, 3].map(value => ({ value, label: `AuxOut${value + 1}` })) },
  { name: "RF_PHYSICAL_INPUT_SOURCE", label: "RF 物理输入通道", type: "string", default: "aux_input1", group: "basic", options: [{ value: "aux_input0", label: "Aux Input1" }, { value: "aux_input1", label: "Aux Input2" }, { value: "voltage1", label: "Signal Input2（仅 HF2）" }] },
  { name: "LOOPBACK_AUX_SCALE", label: "回环增益", type: "number", default: 2.2044, group: "advanced" },
  { name: "TEMP_SWITCH_ON_LAG_S", label: "恢复温控后等待", type: "number", default: 0.5, group: "basic" },
  { name: "NOISE_NPERSEG", label: "Welch 段长", type: "integer", default: 2000, group: "basic" },
  { name: "LINEWIDTH_MODE", label: "线宽模式", type: "string", default: "dispersive_equivalent", group: "basic", options: [{ value: "frequency_sweep", label: "扫频测线宽" }, { value: "dispersive_equivalent", label: "色散等效线宽" }] },
  { name: "BW_FREQ_START", label: "带宽扫频起点", type: "number", default: 2000, group: "basic" },
  { name: "BW_FREQ_STOP", label: "带宽扫频终点", type: "number", default: 4000, group: "basic" },
  { name: "BW_FREQ_POINTS", label: "带宽扫频点数", type: "integer", default: 21, group: "basic" },
];
const flow = {
  "acquisition": [
    {
      "id": "A01",
      "title": "启动预检",
      "group": "启动准备",
      "dependencies": [],
      "parameters": [],
      "artifacts": [],
      "display_parameters": []
    },
    {
      "id": "A02",
      "title": "设备连接",
      "group": "启动准备",
      "dependencies": [
        "A01"
      ],
      "parameters": [],
      "artifacts": [],
      "display_parameters": []
    },
    {
      "id": "A03",
      "title": "参考时钟同步",
      "group": "启动准备",
      "dependencies": [
        "A02"
      ],
      "parameters": [],
      "artifacts": [],
      "display_parameters": []
    },
    {
      "id": "A04",
      "title": "初始输出设置",
      "group": "初始条件",
      "dependencies": [
        "A03"
      ],
      "parameters": [
        "FIXED_PARAMS.Pump_laser_power",
        "FIXED_PARAMS.Probe_laser_power",
        "FIXED_PARAMS.main_magnetic_field",
        "FIXED_PARAMS.Temp_Switch"
      ],
      "artifacts": [],
      "display_parameters": [
        "FIXED_PARAMS.Pump_laser_power",
        "FIXED_PARAMS.Probe_laser_power",
        "FIXED_PARAMS.main_magnetic_field",
        "FIXED_PARAMS.Temp_Switch"
      ]
    },
    {
      "id": "A05",
      "title": "目标温度设置",
      "group": "初始条件",
      "dependencies": [
        "A04"
      ],
      "parameters": [
        "FIXED_PARAMS.temperature"
      ],
      "artifacts": [],
      "display_parameters": [
        "FIXED_PARAMS.temperature"
      ]
    },
    {
      "id": "A06",
      "title": "Pump 载波与门控",
      "group": "激励与触发",
      "dependencies": [
        "A05"
      ],
      "parameters": [
        "PUMP_MOD_FREQ",
        "PUMP_MOD_AMPLITUDE",
        "PUMP_MOD_DUTY",
        "RF_GATE_AMPLITUDE",
        "RF_GATE_OFFSET"
      ],
      "artifacts": [],
      "display_parameters": [
        "PUMP_MOD_FREQ",
        "PUMP_MOD_AMPLITUDE",
        "PUMP_MOD_DUTY",
        "RF_GATE_AMPLITUDE",
        "RF_GATE_OFFSET"
      ]
    },
    {
      "id": "A07",
      "title": "XYZ 共同触发",
      "group": "激励与触发",
      "dependencies": [
        "A06"
      ],
      "parameters": [
        "XY_TRIGGER_FREQ",
        "XY_TRIGGER_AMPLITUDE",
        "XY_TRIGGER_OFFSET",
        "XY_TRIGGER_DUTY",
        "XY_TRIGGER_PHASE",
        "Z_TRIGGER_FREQ",
        "Z_TRIGGER_AMPLITUDE",
        "Z_TRIGGER_OFFSET",
        "Z_TRIGGER_DUTY",
        "Z_TRIGGER_PHASE"
      ],
      "artifacts": [],
      "display_parameters": [
        "XY_TRIGGER_FREQ",
        "XY_TRIGGER_AMPLITUDE",
        "XY_TRIGGER_OFFSET",
        "XY_TRIGGER_DUTY",
        "XY_TRIGGER_PHASE"
      ]
    },
    {
      "id": "A08",
      "title": "Bell-Bloom / Demod0 校相",
      "group": "四级相位校准",
      "dependencies": [
        "A07"
      ],
      "parameters": [
        "DEMOD0_IDX",
        "DEMOD0_OSC_IDX",
        "DEMOD0_SIGNAL_RANGE",
        "DEMOD0_ORDER",
        "DEMOD0_TC_CALIB",
        "DEMOD0_RATE",
        "SYSTEM_SETTLE_S",
        "TEMP_SWITCH_ON_LAG_S"
      ],
      "artifacts": [],
      "display_parameters": [
        "DEMOD0_IDX",
        "DEMOD0_OSC_IDX",
        "DEMOD0_SIGNAL_RANGE",
        "DEMOD0_ORDER",
        "DEMOD0_TC_CALIB",
        "DEMOD0_RATE",
        "SYSTEM_SETTLE_S",
        "TEMP_SWITCH_ON_LAG_S"
      ]
    },
    {
      "id": "A09",
      "title": "XY DirectAW 上传与迭代校相",
      "group": "四级相位校准",
      "dependencies": [
        "A08"
      ],
      "parameters": [
        "ARB_WAVEFORM_FILE",
        "AW_REPEAT_FREQ",
        "XY_CTRL_K_HZ_PER_V",
        "XY_CTRL_B_HZ",
        "XY_CTRL_PHASE",
        "XY_CTRL_QUAD",
        "XY_CTRL_PHASE_TOL_DEG",
        "XY_CTRL_PHASE_MAX_ITER",
        "XY_CTRL_PHASE_MIN_R_V",
        "XY_CTRL_PHASE_MIN_R_RATIO"
      ],
      "artifacts": [
        "raw/xy_aw_waveforms_initial.npz",
        "raw/xy_aw_waveforms.npz"
      ],
      "display_parameters": [
        "ARB_WAVEFORM_FILE",
        "AW_REPEAT_FREQ",
        "XY_CTRL_K_HZ_PER_V",
        "XY_CTRL_B_HZ",
        "XY_CTRL_PHASE",
        "XY_CTRL_QUAD",
        "XY_CTRL_PHASE_TOL_DEG",
        "XY_CTRL_PHASE_MAX_ITER",
        "XY_CTRL_PHASE_MIN_R_V",
        "XY_CTRL_PHASE_MIN_R_RATIO",
        "SYSTEM_SETTLE_S",
        "TEMP_SWITCH_ON_LAG_S"
      ]
    },
    {
      "id": "A10",
      "title": "RF 输入线路与 Z RF 响应准备",
      "group": "四级相位校准",
      "dependencies": [
        "A09"
      ],
      "parameters": [
        "DEMOD0_TC_MEAS",
        "DEMOD3_IDX",
        "DEMOD3_OSC_IDX",
        "DEMOD3_ORDER",
        "DEMOD3_TC",
        "DEMOD3_RATE",
        "LOOPBACK_AUX_SCALE",
        "LOOPBACK_AUX_OFFSET_V",
        "LOOPBACK_INPUT_RANGE_V",
        "LOOPBACK_INPUT_DIFF",
        "Z_RF_FREQ",
        "Z_RF_AMPLITUDE_INIT",
        "Z_BURST_PHASE_INIT",
        "RF_INPUT_ROUTE",
        "RF_AUX_OUTPUT_IDX",
        "RF_PHYSICAL_INPUT_SOURCE"
      ],
      "artifacts": [],
      "display_parameters": [
        "DEMOD0_TC_MEAS",
        "DEMOD3_IDX",
        "DEMOD3_OSC_IDX",
        "DEMOD3_ORDER",
        "DEMOD3_TC",
        "DEMOD3_RATE",
        "LOOPBACK_AUX_SCALE",
        "LOOPBACK_AUX_OFFSET_V",
        "LOOPBACK_INPUT_RANGE_V",
        "LOOPBACK_INPUT_DIFF",
        "Z_RF_FREQ",
        "Z_RF_AMPLITUDE_INIT",
        "Z_BURST_PHASE_INIT",
        "RF_INPUT_ROUTE",
        "RF_AUX_OUTPUT_IDX",
        "RF_PHYSICAL_INPUT_SOURCE"
      ]
    },
    {
      "id": "A11",
      "title": "控制 AM 与 Z RF 触发相位扫描",
      "group": "四级相位校准",
      "dependencies": [
        "A10"
      ],
      "parameters": [
        "Z_PHASE_SCAN_START_DEG",
        "Z_PHASE_SCAN_STOP_DEG",
        "Z_PHASE_SCAN_STEP_DEG",
        "TEMP_GROUP_SIZE",
        "SYSTEM_SETTLE_S",
        "SCAN_POINT_DURATION_S"
      ],
      "artifacts": [
        "raw/z_phase_scan.npz",
        "results/z_phase_scan.png",
        "results/z_phase_scan.pdf"
      ],
      "display_parameters": [
        "Z_PHASE_SCAN_START_DEG",
        "Z_PHASE_SCAN_STOP_DEG",
        "Z_PHASE_SCAN_STEP_DEG",
        "Z_RF_AMPLITUDE_INIT",
        "SYSTEM_SETTLE_S",
        "TEMP_SWITCH_ON_LAG_S",
        "TEMP_GROUP_SIZE",
        "SCAN_POINT_DURATION_S"
      ]
    },
    {
      "id": "A12",
      "title": "RF 解调器校相",
      "group": "四级相位校准",
      "dependencies": [
        "A11"
      ],
      "parameters": [],
      "artifacts": [],
      "display_parameters": [
        "Z_RF_AMPLITUDE_INIT",
        "SYSTEM_SETTLE_S",
        "TEMP_SWITCH_ON_LAG_S"
      ]
    },
    {
      "id": "A13",
      "title": "有符号 Z RF 幅度扫描",
      "group": "采集与结束",
      "dependencies": [
        "A12"
      ],
      "parameters": [
        "RUN_TAG",
        "RF_AMP_START",
        "RF_AMP_STOP",
        "RF_AMP_POINTS",
        "Z_V_TO_NT"
      ],
      "artifacts": [
        "raw/response_data.npz",
        "experiment_config.yaml"
      ],
      "display_parameters": [
        "RUN_TAG",
        "RF_AMP_START",
        "RF_AMP_STOP",
        "RF_AMP_POINTS",
        "Z_V_TO_NT",
        "SYSTEM_SETTLE_S",
        "TEMP_SWITCH_ON_LAG_S",
        "TEMP_GROUP_SIZE",
        "SCAN_POINT_DURATION_S"
      ]
    },
    {
      "id": "A14",
      "title": "零 RF 工作点 X 噪声采集",
      "group": "采集与结束",
      "dependencies": [
        "A13"
      ],
      "parameters": [
        "NOISE_N_AVG",
        "NOISE_DURATION",
        "NOISE_RATE",
        "DEMOD3_NOISE_TC",
        "DEMOD3_NOISE_ORDER"
      ],
      "artifacts": [
        "raw/noise_D3_*.npz"
      ],
      "display_parameters": [
        "NOISE_N_AVG",
        "NOISE_DURATION",
        "NOISE_RATE",
        "DEMOD3_NOISE_TC",
        "DEMOD3_NOISE_ORDER",
        "SYSTEM_SETTLE_S",
        "TEMP_SWITCH_ON_LAG_S"
      ]
    },
    {
      "id": "A13B",
      "title": "Z RF 扫频带宽测量（可选）",
      "group": "采集与结束",
      "dependencies": [
        "A14"
      ],
      "parameters": [
        "LINEWIDTH_MODE",
        "BW_FREQ_START",
        "BW_FREQ_STOP",
        "BW_FREQ_POINTS"
      ],
      "artifacts": [
        "raw/bandwidth_data.npz"
      ],
      "display_parameters": [
        "LINEWIDTH_MODE",
        "BW_FREQ_START",
        "BW_FREQ_STOP",
        "BW_FREQ_POINTS",
        "Z_RF_AMPLITUDE_INIT",
        "SYSTEM_SETTLE_S",
        "TEMP_SWITCH_ON_LAG_S",
        "TEMP_GROUP_SIZE",
        "SCAN_POINT_DURATION_S"
      ]
    },
    {
      "id": "A15",
      "title": "响应恢复与本轮安全收尾",
      "group": "采集与结束",
      "dependencies": [
        "A14"
      ],
      "parameters": [],
      "artifacts": [],
      "display_parameters": []
    },
    {
      "id": "A16",
      "title": "安全收尾 / 结束会话",
      "group": "采集与结束",
      "dependencies": [],
      "parameters": [],
      "artifacts": [],
      "display_parameters": []
    }
  ],
  "analysis": [
    {
      "id": "B01",
      "title": "加载配置与校验原始数据",
      "group": "独立离线分析",
      "dependencies": [],
      "parameters": [],
      "artifacts": [],
      "display_parameters": []
    },
    {
      "id": "B02",
      "title": "X 色散响应与斜率",
      "group": "独立离线分析",
      "dependencies": [
        "B01"
      ],
      "parameters": [],
      "artifacts": [
        "response_fit.npz"
      ],
      "display_parameters": []
    },
    {
      "id": "B03",
      "title": "Welch X 噪声 PSD",
      "group": "独立离线分析",
      "dependencies": [
        "B02"
      ],
      "parameters": [
        "NOISE_NPERSEG"
      ],
      "artifacts": [
        "psd_avg.npz"
      ],
      "display_parameters": [
        "NOISE_NPERSEG"
      ]
    },
    {
      "id": "B04",
      "title": "RF 灵敏度、频响补偿与结果图",
      "group": "独立离线分析",
      "dependencies": [
        "B03"
      ],
      "parameters": [],
      "artifacts": [
        "sensitivity.npz",
        "analysis.yaml",
        "analysis.json",
        "bandwidth_fit.png",
        "bandwidth_fit.pdf",
        "response_curve.png",
        "response_curve.pdf",
        "noise_psd.png",
        "noise_psd.pdf",
        "sensitivity.png",
        "sensitivity.pdf",
        "full_analysis.png",
        "full_analysis.pdf"
      ],
      "display_parameters": []
    }
  ],
  "parameter_impacts": {
    "acquisition": {
      "FIXED_PARAMS.Pump_laser_power": [
        "A04",
        "A05",
        "A06",
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "FIXED_PARAMS.Probe_laser_power": [
        "A04",
        "A05",
        "A06",
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "FIXED_PARAMS.main_magnetic_field": [
        "A04",
        "A05",
        "A06",
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "FIXED_PARAMS.Temp_Switch": [
        "A04",
        "A05",
        "A06",
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "FIXED_PARAMS.temperature": [
        "A05",
        "A06",
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "PUMP_MOD_FREQ": [
        "A06",
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "PUMP_MOD_AMPLITUDE": [
        "A06",
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "PUMP_MOD_DUTY": [
        "A06",
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "RF_GATE_AMPLITUDE": [
        "A06",
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "RF_GATE_OFFSET": [
        "A06",
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "XY_TRIGGER_FREQ": [
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "XY_TRIGGER_AMPLITUDE": [
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "XY_TRIGGER_OFFSET": [
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "XY_TRIGGER_DUTY": [
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "XY_TRIGGER_PHASE": [
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "Z_TRIGGER_FREQ": [
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "Z_TRIGGER_AMPLITUDE": [
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "Z_TRIGGER_OFFSET": [
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "Z_TRIGGER_DUTY": [
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "Z_TRIGGER_PHASE": [
        "A07",
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD0_IDX": [
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD0_OSC_IDX": [
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD0_SIGNAL_RANGE": [
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD0_ORDER": [
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD0_TC_CALIB": [
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD0_RATE": [
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "SYSTEM_SETTLE_S": [
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "TEMP_SWITCH_ON_LAG_S": [
        "A08",
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "ARB_WAVEFORM_FILE": [
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "AW_REPEAT_FREQ": [
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "XY_CTRL_K_HZ_PER_V": [
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "XY_CTRL_B_HZ": [
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "XY_CTRL_PHASE": [
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "XY_CTRL_QUAD": [
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "XY_CTRL_PHASE_TOL_DEG": [
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "XY_CTRL_PHASE_MAX_ITER": [
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "XY_CTRL_PHASE_MIN_R_V": [
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "XY_CTRL_PHASE_MIN_R_RATIO": [
        "A09",
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD0_TC_MEAS": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD3_IDX": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD3_OSC_IDX": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD3_ORDER": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD3_TC": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD3_RATE": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "LOOPBACK_AUX_SCALE": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "LOOPBACK_AUX_OFFSET_V": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "LOOPBACK_INPUT_RANGE_V": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "LOOPBACK_INPUT_DIFF": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "Z_RF_FREQ": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "Z_RF_AMPLITUDE_INIT": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "Z_BURST_PHASE_INIT": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "RF_INPUT_ROUTE": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "RF_AUX_OUTPUT_IDX": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "RF_PHYSICAL_INPUT_SOURCE": [
        "A10",
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "Z_PHASE_SCAN_START_DEG": [
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "Z_PHASE_SCAN_STOP_DEG": [
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "Z_PHASE_SCAN_STEP_DEG": [
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "TEMP_GROUP_SIZE": [
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "SCAN_POINT_DURATION_S": [
        "A11",
        "A12",
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "RUN_TAG": [
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "RF_AMP_START": [
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "RF_AMP_STOP": [
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "RF_AMP_POINTS": [
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "Z_V_TO_NT": [
        "A13",
        "A14",
        "A13B",
        "A15"
      ],
      "NOISE_N_AVG": [
        "A14",
        "A13B",
        "A15"
      ],
      "NOISE_DURATION": [
        "A14",
        "A13B",
        "A15"
      ],
      "NOISE_RATE": [
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD3_NOISE_TC": [
        "A14",
        "A13B",
        "A15"
      ],
      "DEMOD3_NOISE_ORDER": [
        "A14",
        "A13B",
        "A15"
      ],
      "LINEWIDTH_MODE": [
        "A13B"
      ],
      "BW_FREQ_START": [
        "A13B"
      ],
      "BW_FREQ_STOP": [
        "A13B"
      ],
      "BW_FREQ_POINTS": [
        "A13B"
      ]
    },
    "analysis": {
      "NOISE_NPERSEG": [
        "B03",
        "B04"
      ]
    }
  },
  "diagram_url": "/api/experiments/rf-sensitivity-aw/workflow/diagram"
};
const layout = { basic: fields.filter(field => field.group === "basic").map(field => field.name), advanced: ["LOOPBACK_AUX_SCALE"] };

function makeSession() {
  const parameters: Record<string, any> = Object.fromEntries(fields.map(field => [field.name, field.default]));
  for (const suffix of ["FREQ", "AMPLITUDE", "OFFSET", "DUTY", "PHASE"]) parameters[`Z_TRIGGER_${suffix}`] = parameters[`XY_TRIGGER_${suffix}`];
  return { session_id: "rf-session", job_id: "session-job", revision: 0, status: "waiting", pending_request: null,
    parameters, steps: Object.fromEntries(flow.acquisition.map(stage => [stage.id, { status: "pending", error: "" }])),
    outputs: {}, current_step: null, run_id: null };
}

function analysisState(runId: string) {
  return { run_id: runId, revision: 4, options: { noise_nperseg: runId === "1009_latest" ? 800 : 600 },
    steps: Object.fromEntries(flow.analysis.map(stage => [stage.id, { status: "completed", error: "" }])) };
}

class MockEventSource {
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { sources.push(this); }
  close() {}
}

const renderPage = async () => { const view = render(<MemoryRouter initialEntries={[`/experiments/${ID}`]}><Routes><Route path="/experiments/:experimentId" element={<ExperimentPage />} /></Routes></MemoryRouter>);
  await screen.findByRole("button", { name: "创建分步会话" });
  await waitFor(() => expect(document.querySelectorAll(".xy-node-parameters").length).toBeGreaterThan(0));
  document.querySelectorAll<HTMLDetailsElement>(".xy-node-parameters").forEach(node => { node.open = true; });
  return view;
};

beforeEach(() => {
  historyRuns = undefined;
  managementError = "";
  sources = [];
  vi.stubGlobal("EventSource", MockEventSource);
  jobs = [];
  pageTotal = 1;
  preflightOk = true;
  activeSession = null;
  stepResult = parameterResult = undefined;
  parameterApplyError = false;
  sessionJob = { id: "session-job", kind: `session:${ID}`, status: "running", events: [], message: "等待下一步" };
  analysisJob = { id: "analysis", kind: `analysis:${ID}:1009_latest`, status: "running", stage: "analysis", message: "正在分析", events: [] };
  comparisonJob = { id: "comparison", kind: `comparison:${ID}`, status: "running", stage: "analysis", message: "生成对比", events: [] };
  comparisonRuns = [];
  summary = {
    run_id: "1009_latest", name: "test", starred: false, timestamp: "2026-10-09T12:00:00", run_tag: "test", completion_status: "completed", phase_calibration_status: "completed", analysis_status: "completed", can_analyze: true, can_compare: true, comparison_block_reason: "",
    parameters: { RUN_TAG: "history-tag", ARB_WAVEFORM_FILE: "v1/waveforms/optimal_control_waveform.csv", XY_TRIGGER_FREQ: 200, LOOPBACK_AUX_SCALE: 3.5 }, failure_reason: "", analysis_error: "", analysis_block_reason: "", temperature_control: {}, xy_phase_calibration: { converged: true }, safety_shutdown: { completed: true }, actual_rates: { demod3_noise_Sa_s: 90000 }, hf2_response_snapshot: {}, hf2_noise_snapshot: {},
    artifacts: { "full_analysis.png": "/plot.png?v=1" }, analysis: { response_fit: { success: true, method: "dispersive_curve_fit", slope_V_per_nT: 0.001 }, frequency_response_correction: { applied: true, hwhm_Hz: 560 }, sensitivity: { flat_median_fT_per_sqrtHz: 10, flat_fmin_Hz: 3, flat_fmax_Hz: 560 } },
  };
  mockApi.mockReset();
  mockApi.mockImplementation(async (url: string, options?: RequestInit): Promise<any> => {
    if (url === BASE) return { title: "RF 灵敏度（DirectAW）", description: "XYZ 共用时序信号2主输出触发", wiring_notes: [] };
    if (url === `${BASE}/workflow`) return flow;
    if (url === `${BASE}/sessions/active`) return activeSession;
    if (url === `${BASE}/sessions` && options?.method === "POST") {
      activeSession = { ...makeSession(), parameters: JSON.parse(String(options.body)).parameters };
      return activeSession;
    }
    if (url === `${BASE}/sessions/rf-session/steps`) {
      const ids = JSON.parse(String(options?.body)).step_ids;
      if (ids.length === 1 && ids[0] === "A13") throw new Error("缺少前置：A12 RF 解调器校相");
      if (ids.length === 1 && ids[0] === "A13B") throw new Error("缺少前置：A14 零 RF 工作点 X 噪声采集");
      activeSession = stepResult ?? activeSession;
      return activeSession;
    }
    if (url === `${BASE}/sessions/rf-session/parameters`) {
      if (parameterApplyError) throw new Error("参数校验失败");
      activeSession = parameterResult ?? { ...activeSession, revision: activeSession.revision + 1,
        parameters: JSON.parse(String(options?.body)).parameters };
      return activeSession;
    }
    if (url === `${BASE}/sessions/rf-session/finish`) {
      activeSession = { ...activeSession, status: "closed" };
      return activeSession;
    }
    if (url === `${BASE}/sessions/rf-session`) return activeSession;
    if (url === "/api/jobs/session-job") return sessionJob;
    if (url === `${BASE}/schema`) return { schema_version: 2, fields, parameter_layout: layout };
    if (url === "/api/jobs") return jobs;
    if (url === "/api/jobs/acquisition") return { id: "acquisition", kind: `experiment:${ID}`, status: "running", message: "响应扫描", estimated_remaining_seconds: 120, events: [] };
    if (url === "/api/jobs/analysis") return analysisJob;
    if (url === "/api/jobs/comparison") return comparisonJob;
    if (url === "/api/jobs/comparison/cancel") return { ...comparisonJob, status: "cancelled" };
    if (url === `${BASE}/compare`) {
      comparisonRuns = JSON.parse(String(options?.body)).runs;
      return comparisonJob;
    }
    if (url === "/api/jobs/acquisition/cancel") return { id: "acquisition", kind: `experiment:${ID}`, status: "cancelled", events: [] };
    if (url.startsWith(`${BASE}/runs?`)) {
      const query = new URLSearchParams(url.split("?")[1]);
      if (historyRuns) {
        const filtered = query.get("starred_only") === "true" ? historyRuns.filter(run => run.starred) : historyRuns;
        const offset = Number(query.get("offset"));
        const limit = Number(query.get("limit"));
        return { runs: filtered.slice(offset, offset + limit), total: filtered.length, limit, offset };
      }
      return { runs: pageTotal ? [{ ...summary, run_id: query.get("offset") === "50" ? "1008_older" : summary.run_id }] : [], total: pageTotal, limit: 50, offset: Number(query.get("offset")) };
    }
    if (url.endsWith("/star") || url.endsWith("/rename")) {
      if (managementError) throw new Error(managementError);
      const runId = decodeURIComponent(url.split("/").at(-2)!);
      const run = historyRuns?.find(item => item.run_id === runId) ?? summary;
      const payload = JSON.parse(String(options?.body));
      const updated = url.endsWith("/star") ? { ...run, starred: payload.starred }
        : { ...run, name: payload.name, run_id: `1009_120000_${payload.name}` };
      if (historyRuns) historyRuns = historyRuns.map(item => item.run_id === runId ? updated : item);
      if (summary.run_id === runId) summary = updated;
      return updated;
    }
    if (options?.method === "DELETE") {
      if (managementError) throw new Error(managementError);
      const runId = decodeURIComponent(url.split("/").at(-1)!);
      if (historyRuns) historyRuns = historyRuns.filter(item => item.run_id !== runId);
      else pageTotal = 0;
      return { ok: true, experiment_id: ID, run_id: runId };
    }
    if (url.endsWith("/summary")) return historyRuns?.find(item => item.run_id === decodeURIComponent(url.split("/").at(-2)!)) ?? summary;
    if (url.endsWith("/analysis")) return options?.method === "POST" ? analysisJob : analysisState(decodeURIComponent(url.split("/").at(-2)!));
    if (url === `${BASE}/preflight`) {
      const chosen = JSON.parse(String(options?.body)).parameters.ARB_WAVEFORM_FILE;
      return { ok: preflightOk && Boolean(chosen), errors: !preflightOk ? ["共同触发参数不一致"] : chosen ? [] : ["请选择 oc_sens 中的最优控制波形版本"] };
    }
    if (url === `${BASE}/defaults`) return { schema: { fields, parameter_layout: JSON.parse(String(options?.body)).parameter_layout } };
    if (url === `${BASE}/runs` && options?.method === "POST") return { id: "acquisition", kind: `experiment:${ID}`, status: "running", message: "正在采集", events: [] };
    if (url.endsWith("/analyze")) return analysisJob;
    throw new Error(`未模拟 ${url}`);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("从实验路由进入双 Tab，切换保留参数，历史填回不启动采集", async () => {
  await renderPage();
  const tabs = screen.getByRole("tablist", { name: "RF 灵敏度（DirectAW）视图" });
  expect(within(tabs).getAllByRole("tab")).toHaveLength(2);
  expect(await within(screen.getByRole("tabpanel", { name: "运行" })).findByAltText("X 响应与 RF 灵敏度")).toHaveAttribute("src", "/plot.png?v=1");
  expect(within(screen.getByRole("tabpanel", { name: "运行" })).getByText(/已作洛伦兹校正/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("运行标签"), { target: { value: "edited" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "填回参数" })).toBeEnabled());
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("edited");
  fireEvent.keyDown(tabs, { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: "历史" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("运行标签")).toHaveValue("history-tag");
  expect(screen.getByLabelText("最优控制波形版本")).toHaveValue("v1/waveforms/optimal_control_waveform.csv");
  expect(screen.getByLabelText("共同触发FREQ")).toHaveValue(200);
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
});

it("保存参数与分组布局，并把共同触发同步到两套旧键", async () => {
  await renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.change(screen.getByLabelText("共同触发FREQ"), { target: { value: "200" } });
  fireEvent.change(screen.getByLabelText("最优控制波形版本"), { target: { value: "v8/waveforms/optimal_control_waveform.csv" } });
  fireEvent.click(screen.getByRole("button", { name: "运行标签 移至高级参数" }));
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  const call = mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)!;
  const body = JSON.parse(String(call[1]?.body));
  for (const suffix of ["FREQ", "AMPLITUDE", "OFFSET", "DUTY", "PHASE"]) expect(body.parameters[`XY_TRIGGER_${suffix}`]).toEqual(body.parameters[`Z_TRIGGER_${suffix}`]);
  expect(body.parameters.ARB_WAVEFORM_FILE).toBe("v8/waveforms/optimal_control_waveform.csv");
  expect(body.parameter_layout.advanced).toContain("RUN_TAG");
  document.querySelectorAll<HTMLDetailsElement>(".xy-node-parameters details").forEach(node => { node.open = true; });
  expect(screen.getByLabelText("RF 解调器索引")).toBeEnabled();
  expect(screen.getByLabelText("回环增益")).toBeEnabled();
});

it("可选 RF 索引与内部输出保存，旧历史填回保留物理接线", async () => {
  await renderPage();
  fireEvent.change(screen.getByLabelText("RF 解调器索引"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("RF 辅助输出通道"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("RF 输入线路"), { target: { value: "internal" } });
  expect(screen.getByLabelText("RF 物理输入通道")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  const body = JSON.parse(String(mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)![1]?.body));
  expect(body.parameters).toMatchObject({ DEMOD3_IDX: 2, RF_INPUT_ROUTE: "internal", RF_AUX_OUTPUT_IDX: 3 });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "填回参数" }));
  expect(screen.getByLabelText("RF 输入线路")).toHaveValue("physical");
  expect(screen.getByLabelText("RF 辅助输出通道")).toHaveValue("1");
  expect(screen.getByLabelText("RF 物理输入通道")).toHaveValue("voltage1");
  expect(screen.getByText(/旧参数按 HF2 物理接线/)).toBeVisible();
});

it("预检失败不启动，空历史仍可浏览", async () => {
  preflightOk = false;
  pageTotal = 0;
  await renderPage();
  await waitFor(() => expect(screen.getByRole("button", { name: "预检并运行实验" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("共同触发参数不一致")).toBeVisible();
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByText("暂无运行记录。")).toBeVisible();
});

it("恢复活动采集任务，显示 ETA 并使用真实 JobView 取消接口", async () => {
  jobs = [{ id: "acquisition", kind: `experiment:${ID}`, status: "running" }];
  await renderPage();
  const cancel = await screen.findByRole("button", { name: "安全停止" });
  expect(screen.getByText(/预计剩余/)).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  fireEvent.click(cancel);
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/api/jobs/acquisition/cancel", { method: "POST" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "安全停止" })).toBeNull());
});

it("成功预检后提交运行，并同步两套触发参数", async () => {
  await renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.change(screen.getByLabelText("共同触发FREQ"), { target: { value: "200" } });
  fireEvent.change(screen.getByLabelText("最优控制波形版本"), { target: { value: "v8/waveforms/optimal_control_waveform.csv" } });
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  await screen.findByRole("button", { name: "安全停止" });
  const startIndex = mockApi.mock.calls.findIndex(([url, options]) => url === `${BASE}/runs` && options?.method === "POST");
  const preflightIndex = mockApi.mock.calls.findIndex(([url]) => url === `${BASE}/preflight`);
  expect(startIndex).toBeGreaterThan(preflightIndex);
  const body = JSON.parse(String(mockApi.mock.calls[startIndex][1]?.body));
  expect(body.parameters.XY_TRIGGER_FREQ).toBe(200);
  expect(body.parameters.Z_TRIGGER_FREQ).toBe(200);
  expect(body.parameters.ARB_WAVEFORM_FILE).toBe("v8/waveforms/optimal_control_waveform.csv");
});

it("最优控制版本默认留空，未选择时预检阻止启动，选择后切换 Tab 保留", async () => {
  await renderPage();
  const select = await screen.findByLabelText("最优控制波形版本");
  expect(select).toHaveValue("");
  expect(within(select).getByRole("option", { name: "v8" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "预检并运行实验" }));
  expect(await screen.findByText("请选择 oc_sens 中的最优控制波形版本")).toBeVisible();
  expect(mockApi.mock.calls.some(([url, options]) => url === `${BASE}/runs` && options?.method === "POST")).toBe(false);
  fireEvent.change(select, { target: { value: "v8/waveforms/optimal_control_waveform.csv" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(select).toHaveValue("v8/waveforms/optimal_control_waveform.csv");
});

it("校相失败只显示诊断，并禁止重新分析", async () => {
  summary = { ...summary, completion_status: "failed", phase_calibration_status: "failed", can_analyze: false, failure_reason: "XY 校相信号低于门槛", analysis_block_reason: "采集尚未完整完成，仅可查看诊断", artifacts: {}, analysis: null };
  await renderPage();
  expect(await within(screen.getByRole("tabpanel", { name: "运行" })).findByText("XY 校相信号低于门槛")).toBeVisible();
  expect(screen.queryByAltText("X 响应与 RF 灵敏度")).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await screen.findByRole("button", { name: "重新分析" })).toBeDisabled();
});

it("重新分析完成后刷新所选记录与图片", async () => {
  await renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "重新分析" }));
  await screen.findByRole("button", { name: "取消任务" });
  summary = { ...summary, artifacts: { "full_analysis.png": "/plot.png?v=2" } };
  analysisJob = { ...analysisJob, status: "completed" };
  await waitFor(() => expect(sources.some(source => source.url === "/api/jobs/analysis/events")).toBe(true));
  await act(async () => sources.find(source => source.url === "/api/jobs/analysis/events")!.onmessage?.({ data: JSON.stringify({ done: true, status: "completed" }) }));
  await waitFor(() => expect(within(screen.getByRole("tabpanel", { name: "历史" })).getByAltText("X 响应与 RF 灵敏度")).toHaveAttribute("src", "/plot.png?v=2"));
});

it("历史列表支持分页", async () => {
  pageTotal = 51;
  await renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "下一页" }));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(`${BASE}/runs?limit=50&offset=50`));
  expect(await screen.findByRole("button", { name: "1008_older" })).toBeVisible();
});

it("星标与筛选只改变历史列表，筛选后分页且保留最新结果和分析草稿", async () => {
  historyRuns = Array.from({ length: 51 }, (_, index) => ({ ...summary,
    run_id: index ? `1008_110000_run${index}` : summary.run_id, starred: index === 50 }));
  await renderPage();
  const object = screen.getByLabelText("分析操作对象");
  fireEvent.change(object, { target: { value: "1009_latest" } });
  await waitFor(() => expect(screen.getByLabelText("Welch 段长")).toHaveValue(800));
  fireEvent.change(screen.getByLabelText("Welch 段长"), { target: { value: "512" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "星标 1009_latest" }));
  expect(await screen.findByRole("button", { name: "取消星标 1009_latest" })).toHaveAttribute("aria-pressed", "true");
  expect(mockApi).toHaveBeenCalledWith(`${BASE}/runs/1009_latest/star`, { method: "PUT", body: JSON.stringify({ starred: true }) });
  fireEvent.click(screen.getByRole("checkbox", { name: "仅显示星标" }));
  await screen.findByRole("button", { name: "1008_110000_run50" });
  expect(screen.getByRole("heading", { name: "历史运行（2）" })).toBeVisible();
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "1008_110000_run1" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "取消星标 1009_latest" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "1009_latest" })).toBeNull());
  expect(await screen.findByRole("heading", { name: "历史运行（1）" })).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(object).toHaveValue("1009_latest");
  expect(screen.getByLabelText("Welch 段长")).toHaveValue(512);
  expect(within(screen.getByRole("tabpanel", { name: "运行" })).getByAltText("X 响应与 RF 灵敏度")).toHaveAttribute("src", "/plot.png?v=1");
});

it("重命名只编辑后缀，迁移分析草稿与对比选择，后续请求使用中文新目录", async () => {
  historyRuns = [summary];
  await renderPage();
  const object = screen.getByLabelText("分析操作对象");
  fireEvent.change(object, { target: { value: "1009_latest" } });
  await waitFor(() => expect(screen.getByLabelText("Welch 段长")).toHaveValue(800));
  fireEvent.change(screen.getByLabelText("Welch 段长"), { target: { value: "512" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "选择 1009_latest 进行对比" }));
  fireEvent.change(screen.getByLabelText("图例：1009_latest"), { target: { value: "Custom legend" } });
  fireEvent.click(screen.getByRole("button", { name: "单次详情" }));
  fireEvent.click(screen.getByRole("button", { name: "重命名" }));
  expect(screen.getByLabelText("新名称")).toHaveValue("test");
  fireEvent.change(screen.getByLabelText("新名称"), { target: { value: "最佳灵敏度" } });
  fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
  const newId = "1009_120000_最佳灵敏度";
  await screen.findByText(`已重命名为 ${newId}`);
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(`${BASE}/runs/${encodeURIComponent(newId)}/summary`));
  expect(await screen.findByRole("button", { name: newId })).toHaveClass("selected");
  fireEvent.click(screen.getByRole("button", { name: "对比分析（1）" }));
  expect(screen.getByLabelText(`图例：${newId}`)).toHaveValue("Custom legend");
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  await waitFor(() => expect(object).toHaveValue(newId));
  expect(within(object).queryByRole("option", { name: "1009_latest" })).toBeNull();
  await waitFor(() => expect(screen.getByLabelText("Welch 段长")).toHaveValue(512));
  fireEvent.change(object, { target: { value: "" } });
  expect(screen.getByLabelText("Welch 段长")).toHaveValue(2000);
  fireEvent.change(object, { target: { value: newId } });
  await waitFor(() => expect(screen.getByRole("button", { name: "选择全部分析步骤" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(`${BASE}/runs/${encodeURIComponent(newId)}/analysis`, {
    method: "POST", body: JSON.stringify({ revision: 4, step_ids: ["B01", "B02", "B03", "B04"], analysis_parameters: { NOISE_NPERSEG: 512 } }),
  }));
});

it("删除取消不请求，失败保留内容，确认成功清理相关草稿和对比选择", async () => {
  historyRuns = [summary, { ...summary, run_id: "1008_110000_other" }];
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  await renderPage();
  const object = screen.getByLabelText("分析操作对象");
  fireEvent.change(object, { target: { value: "1008_110000_other" } });
  await waitFor(() => expect(screen.getByLabelText("Welch 段长")).toHaveValue(600));
  fireEvent.change(screen.getByLabelText("Welch 段长"), { target: { value: "256" } });
  fireEvent.change(object, { target: { value: "1009_latest" } });
  await waitFor(() => expect(screen.getByLabelText("Welch 段长")).toHaveValue(800));
  fireEvent.change(screen.getByLabelText("Welch 段长"), { target: { value: "512" } });
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "选择 1009_latest 进行对比" }));
  fireEvent.click(screen.getByRole("button", { name: "单次详情" }));
  fireEvent.click(screen.getByRole("button", { name: "删除本次实验" }));
  expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/永久删除.*参数.*raw\/ 原始数据.*results\/ 分析结果.*不可恢复/));
  expect(mockApi.mock.calls.some(([, options]) => options?.method === "DELETE")).toBe(false);
  confirm.mockReturnValue(true);
  managementError = "目录被占用";
  fireEvent.click(screen.getByRole("button", { name: "删除本次实验" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("目录被占用");
  expect(screen.getByRole("button", { name: "1009_latest" })).toBeVisible();
  expect(screen.getByRole("button", { name: "对比分析（1）" })).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(object).toHaveValue("1009_latest");
  expect(screen.getByLabelText("Welch 段长")).toHaveValue(512);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  managementError = "";
  fireEvent.click(screen.getByRole("button", { name: "删除本次实验" }));
  await screen.findByText("本次实验数据已永久删除。");
  expect(mockApi).toHaveBeenCalledWith(`/api/runs/${ID}/1009_latest`, { method: "DELETE" });
  expect(screen.getByRole("button", { name: "对比分析（0）" })).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  expect(object).toHaveValue("");
  expect(within(object).queryByRole("option", { name: "1009_latest" })).toBeNull();
  expect(screen.getByLabelText("Welch 段长")).toHaveValue(2000);
  fireEvent.change(object, { target: { value: "1008_110000_other" } });
  await waitFor(() => expect(screen.getByLabelText("Welch 段长")).toHaveValue(256));
});

it("删除末页最后一条后回到有效页", async () => {
  historyRuns = Array.from({ length: 51 }, (_, index) => ({ ...summary, run_id: `1009_120000_run${index}` }));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  await renderPage();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  fireEvent.click(await screen.findByRole("button", { name: "1009_120000_run50" }));
  fireEvent.click(await screen.findByRole("button", { name: "删除本次实验" }));
  await screen.findByText("本次实验数据已永久删除。");
  expect(await screen.findByRole("button", { name: "1009_120000_run0" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "历史运行（50）" })).toBeVisible();
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
});

it("重命名等待时防止重复提交，失败保留输入与当前目录，星标失败也不改变记录", async () => {
  await renderPage();
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("button", { name: "重命名" }));
  fireEvent.change(screen.getByLabelText("新名称"), { target: { value: "草稿名称" } });
  const implementation = mockApi.getMockImplementation()!;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  mockApi.mockImplementation(async (url, options) => {
    if (url.endsWith("/rename")) await pending;
    return implementation(url, options);
  });
  managementError = "名称冲突";
  fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
  expect(screen.getByRole("button", { name: "保存名称" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "删除本次实验" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "星标 1009_latest" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
  expect(mockApi.mock.calls.filter(([url]) => url.endsWith("/rename"))).toHaveLength(1);
  await act(async () => release());
  expect(await screen.findByRole("alert")).toHaveTextContent("名称冲突");
  expect(screen.getByLabelText("新名称")).toHaveValue("草稿名称");
  expect(screen.getByRole("button", { name: "1009_latest" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  fireEvent.click(screen.getByRole("button", { name: "星标 1009_latest" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "星标 1009_latest" })).toBeEnabled());
  expect(screen.getByRole("button", { name: "星标 1009_latest" })).toHaveAttribute("aria-pressed", "false");
});

async function selectTwoRuns() {
  pageTotal = 51;
  await renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "选择 1009_latest 进行对比" }));
  expect(screen.getByRole("button", { name: "生成对比" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "选择 1008_older 进行对比" }));
}

it.each(["rename", "delete"])("%s 来源后当前对比预览失效，保留自定义英文图例", async action => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  await selectTwoRuns();
  fireEvent.change(screen.getByLabelText("图例：1008_older"), { target: { value: "Custom legend" } });
  fireEvent.click(screen.getByRole("button", { name: "生成对比" }));
  await screen.findByRole("button", { name: "取消任务" });
  comparisonJob = { ...comparisonJob, status: "completed", result: { runs: comparisonRuns, warnings: [],
    artifacts: { "full_analysis_comparison.png": "/compare.png", "full_analysis_comparison.pdf": "/compare.pdf" } } };
  await act(async () => sources.find(source => source.url === "/api/jobs/comparison/events")!.onmessage?.({ data: JSON.stringify({ done: true, status: "completed" }) }));
  await screen.findByAltText("X 响应与 RF 灵敏度对比");
  fireEvent.click(screen.getByRole("button", { name: "单次详情" }));
  if (action === "rename") {
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    fireEvent.change(screen.getByLabelText("新名称"), { target: { value: "最佳" } });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
    await screen.findByText("已重命名为 1009_120000_最佳");
  } else {
    fireEvent.click(screen.getByRole("button", { name: "删除本次实验" }));
    await screen.findByText("本次实验数据已永久删除。");
  }
  fireEvent.click(screen.getByRole("button", { name: `对比分析（${action === "rename" ? 2 : 1}）` }));
  expect(screen.getByText("选择或图例已修改，请重新生成对比。")).toBeVisible();
  expect(screen.queryByAltText("X 响应与 RF 灵敏度对比")).toBeNull();
  expect(screen.queryByRole("link", { name: "下载 PNG" })).toBeNull();
  expect(screen.getByLabelText("图例：1008_older")).toHaveValue("Custom legend");
  if (action === "rename") {
    fireEvent.click(screen.getByRole("button", { name: "生成对比" }));
    await waitFor(() => expect(comparisonRuns).toEqual([
      { run_id: "1009_120000_最佳", legend: "Run 1009_120000" }, { run_id: "1008_older", legend: "Custom legend" },
    ]));
  }
});

it("跨页勾选、图例和 Tab 切换保留顺序，生成后下载，编辑后隐藏旧图", async () => {
  await selectTwoRuns();
  fireEvent.change(screen.getByLabelText("图例：1009_latest"), { target: { value: " Control A " } });
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(screen.getByLabelText("图例：1009_latest")).toHaveValue(" Control A ");
  expect(screen.getByRole("checkbox", { name: "选择 1008_older 进行对比" })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "生成对比" }));
  await screen.findByRole("button", { name: "取消任务" });
  expect(comparisonRuns).toEqual([{ run_id: "1009_latest", legend: "Control A" }, { run_id: "1008_older", legend: "1008_older" }]);
  expect(screen.getByRole("button", { name: "正在生成对比…" })).toBeDisabled();
  expect(screen.getByLabelText("图例：1009_latest")).toBeDisabled();
  comparisonJob = { ...comparisonJob, status: "completed", result: {
    runs: comparisonRuns, warnings: ["1008_older：数值斜率回退，灵敏度谱未作洛伦兹校正。"],
    artifacts: { "full_analysis_comparison.png": "/compare.png", "full_analysis_comparison.pdf": "/compare.pdf" }
  } };
  await act(async () => sources.find(source => source.url === "/api/jobs/comparison/events")!.onmessage?.({ data: JSON.stringify({ done: true, status: "completed" }) }));
  expect(await screen.findByAltText("X 响应与 RF 灵敏度对比")).toHaveAttribute("src", "/compare.png");
  expect(screen.getByText("1008_older：数值斜率回退，灵敏度谱未作洛伦兹校正。")).toBeVisible();
  expect(screen.getByRole("link", { name: "下载 PNG" })).toHaveAttribute("href", "/compare.png");
  expect(screen.getByRole("link", { name: "下载 PDF" })).toHaveAttribute("download");
  fireEvent.change(screen.getByLabelText("图例：1009_latest"), { target: { value: "Changed" } });
  expect(screen.getByText("选择或图例已修改，请重新生成对比。")).toBeVisible();
  expect(screen.queryByAltText("X 响应与 RF 灵敏度对比")).toBeNull();
  expect(screen.queryByRole("link", { name: "下载 PDF" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "移除 1008_older" }));
  expect(screen.getByRole("button", { name: "生成对比" })).toBeDisabled();
});

it("不可对比的运行显示原因且禁用选择，图例校验在提交前生效", async () => {
  summary = { ...summary, can_compare: false, comparison_block_reason: "分析尚未完成，请先重新分析" };
  await renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  expect(await screen.findByRole("checkbox", { name: "选择 1009_latest 进行对比" })).toBeDisabled();
  expect(screen.getByText("不可对比：分析尚未完成，请先重新分析")).toBeVisible();
});

it("无效图例阻止生成，取消后保留选择并允许重试", async () => {
  await selectTwoRuns();
  fireEvent.change(screen.getByLabelText("图例：1009_latest"), { target: { value: "中文" } });
  expect(screen.getByRole("button", { name: "生成对比" })).toBeDisabled();
  expect(screen.getByText("图例请使用单行英文文字、数字或符号。")).toBeVisible();
  fireEvent.change(screen.getByLabelText("图例：1009_latest"), { target: { value: "Control" } });
  fireEvent.click(screen.getByRole("button", { name: "生成对比" }));
  fireEvent.click(await screen.findByRole("button", { name: "取消任务" }));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith("/api/jobs/comparison/cancel", { method: "POST" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "生成对比" })).toBeEnabled());
  expect(screen.getByLabelText("图例：1009_latest")).toHaveValue("Control");
  expect(screen.queryByAltText("X 响应与 RF 灵敏度对比")).toBeNull();
});

it("提交失败保留选择与图例，并显示错误", async () => {
  await selectTwoRuns();
  mockApi.mockRejectedValueOnce(new Error("所选运行正在重新分析"));
  fireEvent.click(screen.getByRole("button", { name: "生成对比" }));
  expect(await screen.findByText(/所选运行正在重新分析/)).toBeVisible();
  expect(screen.getByRole("button", { name: "生成对比" })).toBeEnabled();
  expect(screen.getByLabelText("图例：1009_latest")).toBeVisible();
  expect(screen.getByLabelText("图例：1008_older")).toBeVisible();
});

it("恢复正在执行的对比任务，完成后恢复图例和预览", async () => {
  jobs = [{ id: "comparison", kind: `comparison:${ID}`, status: "running" }];
  await renderPage();
  await screen.findByLabelText("运行标签");
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  await screen.findByRole("button", { name: "取消任务" });
  comparisonJob = { ...comparisonJob, status: "completed", result: {
    runs: [{ run_id: "1009_latest", legend: "Recovered A" }, { run_id: "1008_older", legend: "Recovered B" }],
    warnings: [], artifacts: { "full_analysis_comparison.png": "/recovered.png", "full_analysis_comparison.pdf": "/recovered.pdf" }
  } };
  await act(async () => sources.find(source => source.url === "/api/jobs/comparison/events")!.onmessage?.({ data: JSON.stringify({ done: true, status: "completed" }) }));
  expect(await screen.findByAltText("X 响应与 RF 灵敏度对比")).toHaveAttribute("src", "/recovered.png");
  expect(screen.getByLabelText("图例：1009_latest")).toHaveValue("Recovered A");
  expect(screen.getByLabelText("图例：1008_older")).toHaveValue("Recovered B");
});

it("RF 节点按显式选择提交，缺前置保留选择，质量失败可结束会话", async () => {
  await renderPage();
  fireEvent.click(screen.getByRole("button", { name: "创建分步会话" }));
  const panel = within(screen.getByRole("region", { name: "交互实验流程图" }));
  fireEvent.click(panel.getByLabelText("A13 有符号 Z RF 幅度扫描"));
  await waitFor(() => expect(panel.getByRole("button", { name: "运行所选 / 重跑" })).toBeEnabled());
  fireEvent.click(panel.getByRole("button", { name: "运行所选 / 重跑" }));
  expect(await screen.findByText("Error: 缺少前置：A12 RF 解调器校相")).toBeVisible();
  expect(panel.getByLabelText("A12 RF 解调器校相")).not.toBeChecked();
  const path = `${BASE}/sessions/rf-session/steps`;
  expect(JSON.parse(String(mockApi.mock.calls.find(([url]) => url === path)![1]?.body))).toEqual({ revision: 0, step_ids: ["A13"] });
  stepResult = { ...activeSession, revision: 1, steps: { ...activeSession.steps,
    A09: { status: "quality_failed", error: "XY 校相 R 低于门槛" } } };
  fireEvent.click(panel.getByRole("button", { name: "选择全部采集步骤" }));
  expect(panel.getByLabelText("A16 安全收尾 / 结束会话")).not.toBeChecked();
  fireEvent.click(panel.getByRole("button", { name: "运行所选 / 重跑" }));
  expect(await panel.findByText("质量未通过")).toBeVisible();
  expect(panel.getByText("XY 校相 R 低于门槛")).toBeVisible();
  const all = mockApi.mock.calls.filter(([url]) => url === path).at(-1)!;
  expect(JSON.parse(String(all[1]?.body)).step_ids).toEqual(flow.acquisition.slice(0, -1).map(stage => stage.id));
  expect(panel.getByRole("link", { name: "下载可编辑流程图" })).toHaveAttribute("href", `${BASE}/workflow/diagram`);
  fireEvent.click(panel.getByRole("button", { name: "结束会话" }));
  await waitFor(() => expect(mockApi).toHaveBeenCalledWith(`${BASE}/sessions/rf-session/finish`, {
    method: "POST", body: JSON.stringify({ revision: 1 })
  }));
});

it("带宽节点位于噪声之后，缺前置提示 A14，保留显式选择提交后端", async () => {
  activeSession = makeSession();
  activeSession.parameters.LINEWIDTH_MODE = "frequency_sweep";
  for (const id of Object.keys(activeSession.steps).filter(id => /^A\d+$/.test(id) && Number(id.slice(1)) <= 13)) activeSession.steps[id].status = "completed";
  await renderPage();
  const panel = within(screen.getByRole("region", { name: "交互实验流程图" }));
  const noise = panel.getByLabelText("A14 零 RF 工作点 X 噪声采集");
  const bandwidth = panel.getByLabelText("A13B Z RF 扫频带宽测量（可选）");
  expect(noise.compareDocumentPosition(bandwidth) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(bandwidth);
  await waitFor(() => expect(panel.getByRole("button", { name: "运行所选 / 重跑" })).toBeEnabled());
  fireEvent.click(panel.getByRole("button", { name: "运行所选 / 重跑" }));
  expect(await screen.findByText("Error: 缺少前置：A14 零 RF 工作点 X 噪声采集")).toBeVisible();
  expect(noise).not.toBeChecked();
  fireEvent.click(noise);
  fireEvent.click(panel.getByRole("button", { name: "运行所选 / 重跑" }));
  const path = `${BASE}/sessions/rf-session/steps`;
  await waitFor(() => expect(mockApi.mock.calls.filter(([url]) => url === path)).toHaveLength(2));
  const call = mockApi.mock.calls.filter(([url]) => url === path).at(-1)!;
  expect(JSON.parse(String(call[1]?.body)).step_ids).toEqual(["A13B", "A14"]);
});

it("恢复 RF 会话优先于旧任务，共享参数和分类同步，草稿应用携带修订", async () => {
  activeSession = { ...makeSession(), revision: 7 };
  activeSession.parameters.RUN_TAG = "session-tag";
  for (const id of Object.keys(activeSession.steps).filter(id => id !== "A16")) activeSession.steps[id].status = "completed";
  jobs = [{ id: "acquisition", kind: `experiment:${ID}`, status: "completed", started_at: "yesterday" },
    { id: "session-job", kind: `session:${ID}`, status: "running" }];
  await renderPage();
  await waitFor(() => expect(screen.getByLabelText("运行标签")).toHaveValue("session-tag"));
  expect(mockApi.mock.calls.some(([url]) => url === "/api/jobs/acquisition")).toBe(false);
  expect(screen.getByText(/分步等待期间保持输出状态/)).toBeVisible();
  fireEvent.click(screen.getByLabelText("A13 有符号 Z RF 幅度扫描"));
  fireEvent.change(screen.getByLabelText("共同触发FREQ"), { target: { value: "150" } });
  expect(screen.getByRole("button", { name: "运行所选 / 重跑" })).toBeDisabled();
  expect(screen.getByText(/预计需重跑的已完成步骤：A07/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("共同触发FREQ"), { target: { value: "100" } });
  expect(screen.queryByText(/预计需重跑的已完成步骤：/)).toBeNull();
  fireEvent.change(screen.getByLabelText("共同触发FREQ"), { target: { value: "150" } });
  const waits = screen.getAllByLabelText("恢复温控后等待");
  expect(waits.length).toBeGreaterThan(1);
  fireEvent.change(waits[0], { target: { value: "0.8" } });
  waits.forEach(input => expect(input).toHaveValue(0.8));
  fireEvent.click(screen.getAllByRole("button", { name: "恢复温控后等待 移至高级参数" })[0]);
  document.querySelectorAll<HTMLDetailsElement>(".xy-node-parameters details").forEach(node => { node.open = true; });
  expect(screen.getAllByRole("button", { name: "恢复温控后等待 移至基础参数" })).toHaveLength(waits.length);
  parameterApplyError = true;
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  expect(await screen.findByText("Error: 参数校验失败")).toBeVisible();
  expect(screen.getByText("参数草稿尚未应用，请先应用参数再执行模块。")).toBeVisible();
  parameterApplyError = false;
  parameterResult = { ...activeSession, revision: 8, parameters: { ...activeSession.parameters,
    XY_TRIGGER_FREQ: 150, Z_TRIGGER_FREQ: 150, TEMP_SWITCH_ON_LAG_S: 0.8 },
    steps: Object.fromEntries(Object.entries(activeSession.steps).map(([id, state]) => [id,
      id >= "A07" && id <= "A15" ? { status: "stale", error: "" } : state])) };
  fireEvent.click(screen.getByRole("button", { name: "应用参数到会话" }));
  await waitFor(() => expect(screen.queryByText("参数草稿尚未应用，请先应用参数再执行模块。")).toBeNull());
  expect(screen.getAllByText("需重跑")).toHaveLength(10);
  const request = mockApi.mock.calls.filter(([url]) => url.endsWith("rf-session/parameters")).at(-1)!;
  expect(JSON.parse(String(request[1]?.body))).toMatchObject({ revision: 7,
    parameters: { XY_TRIGGER_FREQ: 150, Z_TRIGGER_FREQ: 150, TEMP_SWITCH_ON_LAG_S: 0.8 } });
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  const saved = JSON.parse(String(mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)![1]?.body));
  expect(saved.parameter_layout.advanced).toContain("TEMP_SWITCH_ON_LAG_S");
  expect(saved.parameters.TEMP_SWITCH_ON_LAG_S).toBe(0.8);
});

it("历史分析草稿按目录隔离，保存默认值只保存下一次设置，RF 分析使用原键", async () => {
  pageTotal = 51;
  activeSession = makeSession();
  await renderPage();
  const object = screen.getByLabelText("分析操作对象");
  const nperseg = () => screen.getByLabelText("Welch 段长");
  expect(nperseg()).toHaveValue(2000);
  fireEvent.change(object, { target: { value: "1009_latest" } });
  await waitFor(() => expect(nperseg()).toHaveValue(800));
  fireEvent.change(nperseg(), { target: { value: "512" } });
  expect(screen.getByText(/预计需重算的已完成步骤：B03/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "保存参数与布局" }));
  await screen.findByText("参数与布局已保存");
  const saved = JSON.parse(String(mockApi.mock.calls.find(([url]) => url === `${BASE}/defaults`)![1]?.body));
  expect(saved.parameters.NOISE_NPERSEG).toBe(2000);
  fireEvent.click(screen.getByRole("tab", { name: "历史" }));
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: "1008_older" });
  fireEvent.click(screen.getByRole("tab", { name: "运行" }));
  fireEvent.change(object, { target: { value: "1008_older" } });
  await waitFor(() => expect(nperseg()).toHaveValue(600));
  fireEvent.change(nperseg(), { target: { value: "256" } });
  fireEvent.change(object, { target: { value: "" } });
  await waitFor(() => expect(nperseg()).toHaveValue(2000));
  fireEvent.change(object, { target: { value: "1009_latest" } });
  await waitFor(() => expect(nperseg()).toHaveValue(512));
  fireEvent.click(screen.getByRole("button", { name: "选择全部分析步骤" }));
  fireEvent.click(screen.getByRole("button", { name: "运行所选分析 / 重算" }));
  await waitFor(() => expect(mockApi.mock.calls.some(([url, options]) =>
    url === `${BASE}/runs/1009_latest/analysis` && options?.method === "POST")).toBe(true));
  const request = mockApi.mock.calls.find(([url, options]) => url === `${BASE}/runs/1009_latest/analysis` && options?.method === "POST")!;
  expect(JSON.parse(String(request[1]?.body))).toEqual({ revision: 4,
    step_ids: ["B01", "B02", "B03", "B04"], analysis_parameters: { NOISE_NPERSEG: 512 } });
  expect(screen.getByRole("button", { name: "结束会话" })).toBeEnabled();
});
