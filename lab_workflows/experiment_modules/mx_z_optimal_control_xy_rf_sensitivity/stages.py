"""XY 网格的真实步骤、参数编辑位置与失效依赖。"""
from dataclasses import asdict
from ..mx_z_optimal_control_rf_sensitivity.stages import (
    Stage, TEMP, QUALITY, ANALYSIS_PARAMETERS, FINISH_ID, validate_selection,
    initial_states, invalidate, invalidated_stages, parameter_impacts)

ANALYSIS_KEYS = ()
AXES = ("X_FIELD_START_V", "X_FIELD_STOP_V", "X_FIELD_POINTS",
    "Y_FIELD_START_V", "Y_FIELD_STOP_V", "Y_FIELD_POINTS")
SOURCES = ("CONTROL_WAVEFORM_SOURCE", "CORRECTED_CONTROL_SOURCE_RUN", "CONTROL_RESULTS_ROOT",
    "CONTROL_VERSION", "Z_CALIBRATION_SOURCE_RUN", "CONTROL_SCALE", "Z_AW_OUTPUT_VPP", "Z_AW_OUTPUT_OFFSET")
ACQUISITION = (
    Stage("A01", "预检与双来源冻结", "准备", (), SOURCES,
        quality_gates=("参数、映射、锁相能力及完整电压包络有效；来源内容冻结",)),
    Stage("A02", "本轮目录、连接与初态", "准备", ("A01",), ("RUN_TAG",),
        ("raw/source_manifest.yaml", "raw/applied_control_waveform.npz")),
    Stage("A03", "时钟、TEC 目标与中心 Mx 工作点", "初始条件", ("A02",),
        ("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power",
         "FIXED_PARAMS.temperature", "PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP",
         "PUMP_GATE_VOLTAGE_V", "CONTROL_BURST_PHASE_DEG", "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP",
         "TRIGGER_OFFSET_V", "TRIGGER_DUTY_PERCENT", "Y_RF_FREQUENCY_HZ", "DEMOD_IDX", "DEMOD_OSC_IDX",
         "HF2_SIGNAL_RANGE_V", "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER",
         "PHASE_CAL_RF_AMPLITUDE_VPP", *AXES),
        quality_gates=("时钟严格回读；中心为扫描轴算术中心；只写 TEC 目标，不判稳",)),
    Stage("A04", "中心唯一一次 RF 正交校相", "采集", ("A03",),
        ("PHASE_SCAN_START_DEG", "PHASE_SCAN_STOP_DEG", "PHASE_SCAN_STEP_DEG", "RESPONSE_DURATION_S",
         "PHASE_FIT_R_SQUARED_MIN", "PHASE_FIT_AMPLITUDE_SIGMA_MIN", "PHASE_OUTLIER_SIGMA_THRESHOLD",
         "PHASE_OUTLIER_MAX_REACQUIRE_POINTS", *TEMP, *QUALITY),
        ("raw/phase*.npz", "results/phase_calibration.yaml", "results/phase_calibration.png"),
        quality_gates=("点内复噪声、正交拟合及单轮异常点重采合格后才进入网格",)),
    Stage("A05", "蛇形 XY 网格响应与 RF-off 噪声", "采集", ("A04",),
        ("Y_RF_AMP_START_VPP", "Y_RF_AMP_STOP_VPP", "Y_RF_AMP_POINTS", "RESPONSE_DURATION_S",
         "NOISE_N_AVG", "NOISE_DURATION_S", "NOISE_RATE_SA_S", "NOISE_TIME_CONSTANT_S", "NOISE_DEMOD_ORDER",
         *TEMP, *QUALITY), ("raw/point_manifest.yaml", "raw/xy_bias_grid.npz", "raw/points/*/*.npz"),
        quality_gates=("响应质量重试耗尽仅标记当前 XY 点无效并继续；硬件或恢复故障立即停止",)),
    Stage("A06", "恢复响应模式与安全封存", "结束", ("A05",),
        quality_gates=("X/Y 关闭归零、温控与 GS200 原态恢复成功；Z 与共同触发保持",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "运行身份、格式与网格原始数据资格", "独立分析", (),
        quality_gates=("已安全封存；明确 raw1 或已验证 schema v1/v2 网格；索引与坐标有效",)),
    Stage("B02", "逐点原拟合、Welch、热图与排名", "独立分析", ("B01",),
        tuple(key for key in ANALYSIS_PARAMETERS if key != "FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX"),
        ("optimization.npz", "optimization.yaml", "optimization.json", "points",
         "sensitivity_heatmap.png", "slope_heatmap.png", "zero_point_sensitivity_heatmap.png",
         "zero_point_slope_heatmap.png", "hwhm_heatmap.png", "best_point_full_analysis.png",
         "best_point_full_analysis.pdf", "best_zero_point_full_analysis.png", "best_zero_point_full_analysis.pdf"),
        quality_gates=("V0=0 初值，γ 相对不确定度只诊断；无有效点仍导出诊断并阻断后继",)),
    Stage("B03", "来源、中心相位与完整分析汇总", "独立分析", ("B02",),
        artifacts=("analysis.yaml", "analysis.json")),
)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(acquisition=[public(stage) for stage in ACQUISITION], analysis=[public(stage) for stage in ANALYSIS],
        parameter_impacts={"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        diagram_url="/api/experiments/mx-z-optimal-control-xy-rf-sensitivity/workflow/diagram")
