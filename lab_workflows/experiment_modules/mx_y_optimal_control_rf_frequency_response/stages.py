"""本实验真实模块、依赖、参数归属和质量门槛。"""
from dataclasses import asdict, dataclass
from ..mx_y_rf_sensitivity.stages import initial_states, invalidate, invalidated_stages, parameter_impacts, validate_selection


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
POINT = ("SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S", "R_BAD_POINT_STD_THRESHOLD_V", "R_POINT_MAX_ATTEMPTS",
    "FREQUENCY_START_HZ", "FREQUENCY_STOP_HZ", "FREQUENCY_POINTS", "FREQUENCY_RF_AMPLITUDE_VPP", "FREQUENCY_DURATION_S")
ACQUISITION = (
    Stage("A01", "预检、闭环冻结与常数标定来源", "准备", (),
        ("CORRECTED_CONTROL_SOURCE_RUN", "COMPARISON_ENABLED", "CONSTANT_CONTROL_LARMOR_FREQUENCY_HZ", "CONSTANT_CONTROL_CALIBRATION_SOURCE_RUN"),
        quality_gates=("参数、映射和采集能力有效；闭环冻结波形通过版本与一致性校验；启用对照时电压反解及安全限值通过",)),
    Stage("A02", "设备连接、目录与来源快照", "准备", ("A01",), ("RUN_TAG",),
        ("raw/source_corrected_control_waveform.npz", "raw/source_manifest.yaml", "raw/applied_control_waveform.npz")),
    Stage("A03", "时钟、Mx工作点、Z任意波与R解调", "初始条件", ("A02",),
        ("FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power", "FIXED_PARAMS.main_magnetic_field",
         "FIXED_PARAMS.temperature", "FIXED_PARAMS.X_magnetic_field", "FIXED_PARAMS.Y_magnetic_field",
         "PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V",
         "CONTROL_BURST_PHASE_DEG", "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP", "TRIGGER_OFFSET_V", "TRIGGER_DUTY_PERCENT",
         "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V", "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER"),
        quality_gates=("参考时钟严格回读一致；Z输出使用冻结波形并通过完整电压包络安全校验",)),
    Stage("A04", "最优控制频率×Burst相位扫描", "采集", ("A03",),
        (*POINT, "PHASE_SCAN_START_DEG", "PHASE_SCAN_STOP_DEG", "PHASE_SCAN_STEP_DEG"),
        ("raw/optimal_frequency*_attempt_*.npz", "raw/optimal_control_phase_frequency_scan.npz"),
        quality_gates=("每点至少8个有效样本且std(R)不超过阈值；限定重采用尽保存诊断并阻断后继",)),
    Stage("A05", "可选恒定Z DC、0°同频段对照", "采集", ("A04",), POINT,
        ("raw/constant_frequency*_attempt_*.npz", "raw/constant_control_frequency_scan.npz"),
        quality_gates=("启用时确认Z为DC且输出ON，每点R质量通过；关闭时保持原最优控制",)),
    Stage("A06", "恢复Z最优控制并封存本轮", "结束", ("A05",),
        quality_gates=("Z任意波恢复与公共安全收尾成功后才标记采集完成",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "运行身份、格式与原始矩阵资格", "独立分析", (),
        quality_gates=("运行已封存且身份、原始格式、频率/相位轴与R矩阵匹配；启用对照时常数矩阵齐备",)),
    Stage("B02", "逐频率有效相位中位数", "独立分析", ("B01",),
        artifacts=("optimal_control_median_response.npz", "median_diagnostics.yaml"),
        quality_gates=("每频率至少一个有限相位结果；保留有效相位数，不插值或新增拟合",)),
    Stage("B03", "热图、常数对照与科学报告", "独立分析", ("B02",),
        artifacts=("r_phase_frequency.png", "r_phase_frequency.pdf", "rf_frequency_response_comparison.png",
            "rf_frequency_response_comparison.pdf", "analysis.yaml", "analysis.json")),
)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(acquisition=[public(stage) for stage in ACQUISITION], analysis=[public(stage) for stage in ANALYSIS],
        parameter_impacts={"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        diagram_url="/api/experiments/mx-y-optimal-control-rf-frequency-response/workflow/diagram")
