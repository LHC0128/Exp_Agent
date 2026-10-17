"""三维平衡网格的真实步骤、参数编辑归属与失效范围。"""
from dataclasses import asdict
from ..mx_z_optimal_control_rf_sensitivity.stages import (
    Stage, TEMP, QUALITY, FINISH_ID, initial_states, invalidate,
    invalidated_stages, parameter_impacts, validate_selection)

ANALYSIS_KEYS = ()
AXES = ("X_FIELD_START_V", "X_FIELD_STOP_V", "X_FIELD_POINTS",
    "Y_FIELD_START_V", "Y_FIELD_STOP_V", "Y_FIELD_POINTS",
    "Z_FIELD_START_MA", "Z_FIELD_STOP_MA", "Z_FIELD_POINTS")
SOURCES = ("CONTROL_RESULTS_ROOT", "CONTROL_VERSION", "Z_CALIBRATION_SOURCE_RUN",
    "CONTROL_SCALE", "Z_AW_OUTPUT_VPP", "Z_AW_OUTPUT_OFFSET")
ACQUISITION = (
    Stage("A01", "预检与理论控制来源冻结", "准备", (), SOURCES,
        quality_gates=("参数、映射、锁相能力及完整电压包络有效；理论波形、参数与 Z 标定内容冻结",)),
    Stage("A02", "本轮目录、连接与初态", "准备", ("A01",), ("RUN_TAG",),
        ("raw/source_manifest.yaml", "raw/applied_control_waveform.npz")),
    Stage("A03", "时钟、TEC 目标与 Mx 工作点", "初始条件", ("A02",),
        ("FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power",
         "FIXED_PARAMS.temperature", "PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V",
         "CONTROL_BURST_PHASE_DEG", "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP", "TRIGGER_OFFSET_V",
         "TRIGGER_DUTY_PERCENT", "DEMOD_FREQUENCY_HZ", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V",
         "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER", *AXES),
        quality_gates=("参考时钟严格回读；TEC 只写 UI 目标；X/Y 置零关闭后启动 Z 控制，GS200 在 Z 起点开启",)),
    Stage("A04", "蛇形 XYZ 网格与 R 重试采集", "采集", ("A03",),
        (*AXES, "RESPONSE_DURATION_S", *TEMP, *QUALITY),
        ("raw/grid_*_attempt_*.npz", "raw/xyz_balance_scan.npz"),
        display_parameters=(*AXES, "RESPONSE_DURATION_S", *TEMP, *QUALITY, "CONTROL_BURST_PHASE_DEG"),
        quality_gates=("每次尝试先恢复温控再写盘；R 标准差通过，重试用尽阻断；三轴零设定点也保持 Output ON",)),
    Stage("A05", "三轴原态恢复与安全封存", "结束", ("A04",),
        quality_gates=("温控、GS200 与运行前 X/Y 状态恢复成功；Z 最优控制与共同触发保持当前状态",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "运行身份、格式与完整三维网格资格", "独立分析", (),
        quality_gates=("明确 raw1 或已验证 schema v1/v2 历史；Z/X/Y 轴、矩阵与实际采样率有效",)),
    Stage("B02", "实测最小点、逐层 V 形拟合与平面科学图", "独立分析", ("B01",),
        artifacts=("evaluation.json", "balance_results.npz", "xyz_balance_grid.csv",
         "xyz_balance_slices.png", "xyz_balance_slices.pdf", "balance_linear_fit.npz",
         "xyz_balance_linear_fit.png", "xyz_balance_linear_fit.pdf"),
        quality_gates=("实测最小点按 R 最小、采集最早选择；逐层一维拟合只作工作点诊断，不改变实测结论",)),
    Stage("B03", "保存完整三维平衡汇总", "独立分析", ("B02",),
        artifacts=("analysis.yaml", "analysis.json")),
)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
        parameter_impacts={"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        diagram_url="/api/experiments/mx-z-optimal-control-xyz-balance/workflow/diagram")
