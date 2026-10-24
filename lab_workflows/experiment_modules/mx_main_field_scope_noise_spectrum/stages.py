"""主场示波器噪声谱的唯一执行、参数展示与失效清单。"""
from dataclasses import asdict
from ..mx_y_rf_sensitivity.stages import (
    Stage, initial_states, invalidate, invalidated_stages, parameter_impacts,
    validate_selection as _validate_selection,
)
from .run_format import EXPERIMENT_ID

FINISH_ID = "A10"
AXIS = ("CONTROL_FREQUENCY_START_HZ", "CONTROL_FREQUENCY_STOP_HZ", "CONTROL_FREQUENCY_POINTS",
        "MAIN_FIELD_CALIBRATION_HZ_PER_MA", "MAIN_FIELD_CALIBRATION_INTERCEPT_HZ")
SCOPE = ("SCOPE_SAMPLE_RATE", "SCOPE_DURATION", "SCOPE_AC_COUPLING", "SCOPE_INITIAL_SCALE",
         "SCOPE_PD_CHANNEL", "SCOPE_TRIGGER_MODE", "SCOPE_OFFSET", "SCOPE_VERTICAL_DIVISIONS",
         "SCOPE_SCALE_MIN", "SCOPE_SCALE_MAX", "SCOPE_AUTO_RANGE_LOW_FRACTION",
         "SCOPE_AUTO_RANGE_HIGH_FRACTION", "SCOPE_AUTO_OFFSET_TOLERANCE_FRACTION",
         "SCOPE_AUTO_RANGE_MAX_ATTEMPTS", "WELCH_NPERSEG")
ANALYSIS_KEYS = ("WELCH_NPERSEG", "FIT_PEAK_MARGIN_HZ", "FIT_HALF_WIDTH_HZ",
    "FIT_GAMMA_GUESS_HZ", "FIT_EXAMPLE_PLOT_COUNT", "GLOBAL_2D_ANALYSIS_ENABLED",
    "GLOBAL_FIT_FREQUENCY_MIN_HZ", "GLOBAL_CORE_FIT_FREQUENCY_MIN_HZ",
    "GLOBAL_FIT_FREQUENCY_MAX_HZ", "GLOBAL_BACKGROUND_MARGIN_HZ", "GLOBAL_CV_FOLDS")
ACQUISITION = (
    Stage("A01", "参数、映射与接线预检", "准备", (),
          quality_gates=("频率轴、实际使用端点及电压/电流安全约束有效；不连接硬件",)),
    Stage("A02", "连接设备与 GS200 初态回读", "准备", ("A01",),
          quality_gates=("连接复用与 GS200 模式、量程、输出、限流初态快照完整",)),
    Stage("A03", "参考时钟同步与严格回读", "准备", ("A02",),
          quality_gates=("全部已连接时钟设备回读一致",)),
    Stage("A04", "主场、XY DC 与光功率准备", "初始条件", ("A03",),
          (*AXIS, "FIXED_PARAMS.X_magnetic_field", "FIXED_PARAMS.Y_magnetic_field",
           "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power")),
    Stage("A05", "TEC 目标温度", "初始条件", ("A04",), ("FIXED_PARAMS.temperature",),
          quality_gates=("仅写目标温度；串口不可用告警跳过，不启停、不判稳",)),
    Stage("A06", "Pump 连续载波与常开门控", "激励", ("A05",),
          ("PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V")),
    Stage("A07", "SDS 固定采样率与量程回读", "采集", ("A06",), SCOPE,
          display_parameters=("SCOPE_SAMPLE_RATE", "SCOPE_DURATION", "SCOPE_AC_COUPLING", "SCOPE_INITIAL_SCALE", "WELCH_NPERSEG"),
          quality_gates=("FSRate、实际采样率/点数与 Nyquist 通过；AUTO/FTRIG 完整帧采集",)),
    Stage("A08", "主场扫描、自适应量程与波形保存", "采集", ("A07",),
          ("RUN_TAG", "MAIN_FIELD_CALIBRATION_SOURCE_RUN", "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_SETTLE_S"),
          ("raw/main_field_scope_noise_scan_axes.npz", "raw/waveform_I*.npz"),
          quality_gates=("每次配置后关温控、稳定、读入内存、恢复后保存；失败抢救已读帧",)),
    Stage("A09", "本轮安全恢复与封存", "结束", ("A08",),
          quality_gates=("恢复 GS200、SDS DC 耦合和温控；Z/XY 归零关闭；恢复失败不标记完成",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载原始数据与采样资格", "独立分析", (), artifacts=("scope_noise_inputs.json",),
          quality_gates=("身份、raw 格式、至少 10 点、真实时间轴/采样率和标定关系有效；目录已停止写入",)),
    Stage("B02", "Welch、局部诊断与二维定量分解/出图", "独立分析", ("B01",), ANALYSIS_KEYS,
          ("psd_matrix.npz", "popt_fit.npz", "noise_spectra.npz", "noise_spectra.csv",
           "lorentzian_fit_parameters.csv", "analysis.json", "analysis.yaml"),
          quality_gates=("保留原局部拟合掩码、二维交叉验证和内部不确定度；无有效频率仍导出诊断",)),
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
        diagram_url=f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram")
