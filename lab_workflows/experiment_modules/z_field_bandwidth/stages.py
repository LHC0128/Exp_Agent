"""Z 带宽的唯一执行清单、参数编辑归属与失效范围。"""
from dataclasses import asdict
from ..mx_y_rf_sensitivity.stages import Stage, initial_states, invalidate, invalidated_stages, parameter_impacts, validate_selection as _validate
from .run_format import EXPERIMENT_ID

FINISH_ID = "A10"
RESPONSE_KEYS = ("DAQ_RESPONSE_METHOD", "LOCAL_PEAK_REL_WINDOW", "MAX_PICK_FREQ_ERROR_HZ", "OFFLINE_LOCKIN_USE_INTEGER_CYCLES")
BANDWIDTH_KEYS = ("EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH",)
ANALYSIS_KEYS = (*RESPONSE_KEYS, *BANDWIDTH_KEYS)
LOCKIN = ("HF2_DEMOD0_IDX", "HF2_DEMOD0_OSC_IDX", "HF2_DEMOD0_RATE", "HF2_DEMOD0_TC_CALIB", "HF2_DEMOD0_ORDER",
    "HF2_DEMOD0_SIGNAL_RANGE", "HF2_DEMOD0_OSC_FREQ", "DEMOD_R_IDX", "DEMOD_R_OSC_IDX", "DEMOD_R_RATE", "DEMOD_R_TC_MIN",
    "DEMOD_R_TC_PER_PERIOD", "DEMOD_R_ORDER", "CONFIGURE_AUXOUT2_TO_DEMOD0_Y", "AUXOUT_INDEX", "AUXOUT_SOURCE_DEMOD_IDX",
    "AUXOUT_SOURCE_SELECT", "AUXOUT_SCALE", "AUXOUT_OFFSET", "SIGIN2_INDEX", "SIGIN2_RANGE")
TEMP = ("SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG")
SCAN = ("RUN_TAG", "ACQUISITION_MODE", "Z_FREQ_START", "Z_FREQ_STOP", "Z_FREQ_POINTS", "Z_DRIVE_AMPLITUDE",
    "POINT_DURATION", "DEMOD_R_N_AVG", "DEMOD_R_AVG_INTERVAL")
PLOTS = ("frequency_response", "frequency_response_comparison", "frequency_response_absolute", "fft_vs_offline_lockin_ratio",
    "hardware_vs_offline_lockin_ratio", "offline_lockin_phase", "hardware_vector_vs_scalar", "hardware_scalar_over_vector_ratio",
    "hardware_phase_vector", "daq_picked_frequency_error", "response_spectrum_examples")
ACQUISITION = (
    Stage("A01", "参数、映射与安全预检", "准备", (), quality_gates=("无硬件预检；双主电压回环只允许 HF2，MFLI 连接前拒绝",)),
    Stage("A02", "设备连接与锁相输入初始化", "准备", ("A01",), quality_gates=("按资源复用；TEC 串口失败告警跳过；连接失败清理已连接设备",)),
    Stage("A03", "参考时钟同步与严格回读", "准备", ("A02",), quality_gates=("按设备库同步所有已连接时钟；失败禁止启动输出",)),
    Stage("A04", "主场、光功率、XY 预设及 TEC 目标", "初始条件", ("A03",),
        ("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power", "FIXED_PARAMS.temperature",
         "FIXED_PARAMS.X_magnetic_field", "FIXED_PARAMS.Y_magnetic_field"),
        quality_gates=("所有输出写入前校验；XY 预设后保持 OFF；TEC 只写目标、不启停不判稳",)),
    Stage("A05", "Pump 载波、脉冲门控与时序方波", "激励", ("A04",),
        ("PUMP_MOD_FREQ", "PUMP_MOD_AMPLITUDE", "PUMP_MOD_DUTY", "RF_GATE_AMPLITUDE", "RF_GATE_OFFSET", "FIXED_PARAMS.Time_sequence")),
    Stage("A06", "主解调与 Aux 物理回环准备", "锁相", ("A05",), LOCKIN,
        quality_gates=("主解调 Y → Aux → Signal Input 2；高阻/DC、明确 Aux 安全限值、路由严格回读",)),
    Stage("A07", "关闭 XY／Z 后主解调校相", "校相", ("A06",), TEMP, ("experiment_config.yaml", "acquisition_state.json"),
        quality_gates=("读数有限且 R 足够，5 次内收敛至 1°；质量失败保存诊断并阻断后继，可重试",)),
    Stage("A08", "测量时间常数与实际模式回读", "采集", ("A07",), ("HF2_DEMOD0_TC",),
        display_parameters=("HF2_DEMOD0_TC", "HF2_DEMOD0_RATE"), quality_gates=("保存请求与实际设置；采集前模式漂移须重新准备",)),
    Stage("A09", "逐点扫频、恢复后保存及本轮收尾", "采集", ("A08",), SCAN,
        ("raw/freq_*.npz", "raw/frequency_index.json", "raw/frequency_index.npz"), display_parameters=(*SCAN, *TEMP),
        quality_gates=("0 Hz 只作 Z OFF 背景；正频率连续正弦；每点恢复温控后保存，故障抢救 Y 与已读 R；收尾成功才封存",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (), quality_gates=("无前置；关闭 XYZ、恢复温控，只断开 TEC；保持已执行主场、光功率、Pump、时序及锁相配置",)),
)
ANALYSIS = (
    Stage("B01", "原始资格、FFT 与软件锁相", "独立分析", (), RESPONSE_KEYS, ("responses.json",),
        quality_gates=("身份、格式、索引路径、波形及最终实际率有效；0 Hz 不参与响应",)),
    Stage("B02", "三路 −3 dB 带宽与比值", "独立分析", ("B01",), BANDWIDTH_KEYS,
        ("bandwidth_data.json", "analysis.yaml", "analysis.json", "frequency_response.npz"),
        quality_gates=("保留原 FFT 背景扣除、硬件矢量/标量均值及截断标记；无有效带宽仍保存诊断，不伪造质量通过",)),
    Stage("B03", "响应、幅值比及相位诊断图", "独立分析", ("B02",), artifacts=("plot_manifest.json",)),
)


def validate_selection(stages, states, selected):
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("安全结束必须最后执行")
    return _validate(stages, states, selected)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
        parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
        diagram_url=f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram")
