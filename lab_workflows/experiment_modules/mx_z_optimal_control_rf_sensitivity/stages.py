"""本实验的执行依赖、参数编辑归属与真实质量门槛。"""
from dataclasses import asdict, dataclass
from ..mx_y_rf_sensitivity.stages import initial_states, invalidate, invalidated_stages, parameter_impacts


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    group: str
    dependencies: tuple[str, ...]
    parameters: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    display_parameters: tuple[str, ...] | None = None
    quality_gates: tuple[str, ...] = ()


FINISH_ID = "A12"
ANALYSIS_KEYS = ()
TEMP = ("SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S")
QUALITY = ("R_BAD_POINT_STD_THRESHOLD_V", "R_POINT_MAX_ATTEMPTS")
ANALYSIS_PARAMETERS = ("Y_RF_NT_PER_VPP", "LOW_FREQ_SKIP_HZ", "FIT_R_SQUARED_MIN",
    "FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX", "LINEAR_CHECK_GAMMA_FRACTION", "SLOPE_AGREEMENT_TOLERANCE")
ACQUISITION = (
    Stage("A01", "预检与闭环波形冻结", "准备", (), ("CORRECTED_CONTROL_SOURCE_RUN",),
        quality_gates=("参数、映射、锁相能力与电压包络有效；闭环冻结波形版本与内容一致",)),
    Stage("A02", "连接、初态与本轮目录", "准备", ("A01",), ("RUN_TAG",),
        ("raw/source_corrected_control_waveform.npz", "raw/source_manifest.yaml", "raw/applied_control_waveform.npz")),
    Stage("A03", "时钟、TEC 目标与 Mx 控制工作点", "初始条件", ("A02",),
        ("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power",
         "FIXED_PARAMS.temperature", "FIXED_PARAMS.X_magnetic_field", "FIXED_PARAMS.Y_magnetic_field",
         "PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V",
         "CONTROL_BURST_PHASE_DEG", "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP", "TRIGGER_OFFSET_V",
         "TRIGGER_DUTY_PERCENT", "Y_RF_FREQUENCY_HZ", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V",
         "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER", "PHASE_CAL_RF_AMPLITUDE_VPP"),
        quality_gates=("参考时钟写入后严格回读；Z 波形保持冻结的归一化数组、Vpp、offset 与周期",)),
    Stage("A04", "Y RF 正交校相或 Z 剩磁相位响应", "采集", ("A03",),
        ("PHASE_SCAN_START_DEG", "PHASE_SCAN_STOP_DEG", "PHASE_SCAN_STEP_DEG", "RESPONSE_DURATION_S",
         "PHASE_FIT_R_SQUARED_MIN", "PHASE_FIT_AMPLITUDE_SIGMA_MIN", "PHASE_OUTLIER_SIGMA_THRESHOLD",
         "PHASE_OUTLIER_MAX_REACQUIRE_POINTS", *TEMP, *QUALITY),
        ("raw/phase*.npz", "results/phase_calibration.yaml", "results/phase_calibration.png"),
        quality_gates=("RF 模式：点内复噪声、成对正交拟合和单轮跨相位修正合格；剩磁模式允许弱或平坦拟合，保留诊断",)),
    Stage("A05", "带符号 Y RF 幅度响应", "采集", ("A04",),
        ("Y_RF_AMP_START_VPP", "Y_RF_AMP_STOP_VPP", "Y_RF_AMP_POINTS", "RESPONSE_DURATION_S", *TEMP, *QUALITY),
        ("raw/amplitude*.npz",), quality_gates=("RF 模式每点 R 标准差通过；重试用尽阻断。剩磁模式明确跳过",)),
    Stage("A06", "按 RF 开关采集 R 噪声", "采集", ("A05",),
        ("NOISE_RF_ENABLED", "NOISE_RF_AMPLITUDE_VPP", "NOISE_N_AVG", "NOISE_DURATION_S", "NOISE_RATE_SA_S",
         "NOISE_TIME_CONSTANT_S", "NOISE_DEMOD_ORDER", *TEMP), ("raw/noise_*.npz",)),
    Stage("A07", "响应模式恢复与安全封存", "结束", ("A06",),
        quality_gates=("横向场关闭归零、温控恢复、GS200 原态恢复成功；Z 控制与共同触发保持",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "运行身份、格式与原始数据资格", "独立分析", (),
        quality_gates=("已安全封存；v5/v6 历史或明确 raw1；校相及当前模式的响应/噪声完整",)),
    Stage("B02", "原色散、Welch 与灵敏度或剩磁诊断", "独立分析", ("B01",), ANALYSIS_PARAMETERS,
        ("evaluation.json", "evaluation_diagnostics.yaml"),
        quality_gates=("RF 模式保留原拟合门槛且不以 γ 相对不确定度拒绝；剩磁模式不要求拟合通过",)),
    Stage("B03", "科学图与完整报告", "独立分析", ("B02",), artifacts=(
        "phase_calibration.png", "analysis.yaml", "analysis.json", "response_fit.npz", "noise_psd.npz",
        "sensitivity.npz", "sensitivity_zero_point.npz", "amplitude_response.png", "amplitude_response.pdf",
        "noise_psd.png", "noise_psd.pdf", "full_analysis.png", "full_analysis.pdf",
        "full_analysis_zero_point.png", "full_analysis_zero_point.pdf")),
)


def validate_selection(stages, states, selected):
    ids = [stage.id for stage in stages]
    if not selected or len(set(selected)) != len(selected) or any(item not in ids for item in selected):
        raise ValueError("请选择有效且不重复的模块")
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("安全结束必须最后执行")
    available = {key for key, value in states.items() if value["status"] == "completed"}
    ordered = [item for item in ids if item in selected]
    for item in ordered:
        stage = stages[ids.index(item)]
        if missing := set(stage.dependencies) - available:
            raise ValueError(f"{item} {stage.title} 缺少前置模块：" + "、".join(sorted(missing)))
        available.difference_update(stage.id for stage in invalidated_stages(stages, item))
        available.add(item)
    return ordered


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(acquisition=[public(stage) for stage in ACQUISITION], analysis=[public(stage) for stage in ANALYSIS],
        parameter_impacts={"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        diagram_url="/api/experiments/mx-z-optimal-control-rf-sensitivity/workflow/diagram")
