"""Probe/PZT 优化的执行清单、编辑归属与失效规则。"""
from dataclasses import asdict
from ..mx_y_rf_sensitivity.stages import (
    Stage, initial_states, invalidate, invalidated_stages, parameter_impacts,
    validate_selection as _validate_selection,
)

FINISH_ID = "A09"
ANALYSIS_KEYS = (
    "FIT_R_SQUARED_MIN", "FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX",
    "R_BAD_POINT_STD_THRESHOLD_V", "Y_RF_NT_PER_VPP", "LOW_FREQ_SKIP_HZ",
    "LINEAR_CHECK_GAMMA_FRACTION", "SLOPE_AGREEMENT_TOLERANCE",
)
RESPONSE = ("Y_RF_FREQUENCY_HZ", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V",
    "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER")
SCAN = ("PROBE_POWER_START_V", "PROBE_POWER_STOP_V", "PROBE_POWER_POINTS",
    "PZT_VOLTAGE_START_V", "PZT_VOLTAGE_STOP_V", "PZT_VOLTAGE_POINTS", "PZT_SETTLE_TIME_S",
    "Y_RF_AMP_START_VPP", "Y_RF_AMP_STOP_VPP", "Y_RF_AMP_POINTS", "RESPONSE_DURATION_S",
    "R_BAD_POINT_STD_THRESHOLD_V", "R_POINT_MAX_ATTEMPTS", "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S",
    "NOISE_N_AVG", "NOISE_DURATION_S", "NOISE_RATE_SA_S", "NOISE_TIME_CONSTANT_S", "NOISE_DEMOD_ORDER")
ACQUISITION = (
    Stage("A01", "启动预检", "准备", (), quality_gates=("参数、映射、锁相采集能力和安全限值有效；不连接 TEC",)),
    Stage("A02", "连接与 DLC pro 初态回读", "准备", ("A01",), ("RUN_TAG",),
        quality_gates=("DLC pro 序列号、健康、联锁及出光状态有效；初始扫描包络可安全恢复",)),
    Stage("A03", "参考时钟同步与严格回读", "准备", ("A02",),
        quality_gates=("全部已连接时钟设备的参考源回读一致",)),
    Stage("A04", "主场、光功率与辅助场归零", "初始条件", ("A03",),
        ("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power")),
    Stage("A05", "Pump 连续载波与常开门控", "激励", ("A04",),
        ("PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V")),
    Stage("A06", "静态 PZT 与响应解调准备", "激励", ("A05",),
        (*RESPONSE, "FREQUENCY_RF_AMPLITUDE_VPP"), display_parameters=RESPONSE),
    Stage("A07", "Probe/PZT 蛇形扫描与完整灵敏度采集", "采集", ("A06",), (*SCAN, *RESPONSE),
        ("raw/probe_detuning_grid.npz", "raw/point_manifest.yaml", "raw/points/*/*.npz"),
        display_parameters=(*SCAN, "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER"),
        quality_gates=("单点 R 标准差重试；仅质量失败且安全恢复成功时继续，通信/恢复错误终止",)),
    Stage("A08", "恢复 PZT/扫描与本轮安全收尾", "结束", ("A07",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载网格与原始数据资格", "独立分析", (),
        artifacts=("grid_inputs.json",),
        quality_gates=("目录已停止写入、实验身份与原始格式、网格索引和文件引用有效；不完整点保留诊断",)),
    Stage("B02", "双斜率拟合、灵敏度排名与结果图", "独立分析", ("B01",), ANALYSIS_KEYS,
        ("optimization.npz", "optimization.yaml", "optimization.json",
         "sensitivity_heatmap.png", "slope_heatmap.png", "zero_point_sensitivity_heatmap.png",
         "zero_point_slope_heatmap.png", "hwhm_heatmap.png"),
        quality_gates=("无有效最优点时仍保存逐点诊断和无效掩码，标记质量未通过；不自动施加最优点",)),
)


def validate_selection(stages, states, selected):
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("安全结束必须最后执行")
    return _validate_selection(stages, states, selected)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(acquisition=[public(stage) for stage in ACQUISITION],
        analysis=[public(stage) for stage in ANALYSIS],
        parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
        diagram_url="/api/experiments/mx-y-rf-probe-detuning-optimization/workflow/diagram")
