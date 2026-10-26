"""ConstXY 唯一执行清单、节点编辑归属及真实参数失效范围。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import DependencyRules, Stage, initial_states
from .run_format import EXPERIMENT_ID

FINISH_ID = "A11"
RESPONSE_KEYS = ("DAQ_RESPONSE_METHOD", "LOCAL_PEAK_REL_WINDOW", "MAX_PICK_FREQ_ERROR_HZ", "OFFLINE_LOCKIN_USE_INTEGER_CYCLES")
FIT_KEYS = ("EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH", "ENABLE_LORENTZIAN_FIT", "FIT_MODEL", "LORENTZIAN_FIT_MAX_HWHM_HZ",
    "LORENTZIAN_FIT_MIN_POINTS", "LORENTZIAN_FIT_USE_SUSPICIOUS", "DLZ_GAMMA_MIN_HZ", "DLZ_GAMMA_MAX_HZ", "DLZ_OMEGA_INIT_HZ",
    "DLZ_OMEGA_MIN_HZ", "DLZ_OMEGA_MAX_HZ", "DLZ_OFFSET_INIT_V", "DLZ_INCLUDE_OFFSET", "DLZ_MIN_POINTS", "DLZ_USE_SUSPICIOUS")
ANALYSIS_KEYS = (*RESPONSE_KEYS, *FIT_KEYS)
LOCKIN = ("HF2_DEMOD0_IDX", "HF2_DEMOD0_OSC_IDX", "HF2_DEMOD0_RATE", "HF2_DEMOD0_TC_CALIB", "HF2_DEMOD0_ORDER",
    "HF2_DEMOD0_SIGNAL_RANGE", "HF2_DEMOD0_OSC_FREQ", "DEMOD3_IDX", "DEMOD3_OSC_IDX", "DEMOD3_RATE", "DEMOD3_TC_MIN_s",
    "DEMOD3_TC_PERIOD_FRACTION", "DEMOD3_ORDER", "CONFIGURE_AUXOUT2_TO_DEMOD0_Y", "AUXOUT_INDEX", "AUXOUT_SOURCE_DEMOD_IDX",
    "AUXOUT_SOURCE_SELECT", "AUXOUT_SCALE", "AUXOUT_OFFSET", "SIGIN2_INDEX", "SIGIN2_RANGE")
XY = ("LARMOR_FREQ_HZ", "A_ENV_K", "A_ENV_B", "XY_CARRIER_FREQ", "X_CARRIER_AMPLITUDE", "Y_CARRIER_AMPLITUDE",
    "X_CARRIER_PHASE", "Y_CARRIER_PHASE", "X_AM_DEPTH", "Y_AM_DEPTH", "ENABLE_XY_CARRIER_PHASE_CAL",
    "XY_PHASE_CAL_MAX_ITER_X", "XY_PHASE_CAL_MAX_ITER_Y", "XY_PHASE_CAL_TOL_DEG")
TEMP = ("SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG")
SCAN = ("RUN_TAG", "ACQUISITION_MODE", "Z_RF_FREQ_START", "Z_RF_FREQ_STOP", "Z_RF_FREQ_POINTS", "Z_RF_FREQ_LOG_SPACED",
    "Z_RF_AMPLITUDE", "POINT_DURATION_s", "DEMOD3_N_AVG", "DEMOD3_AVG_INTERVAL_s")
PLOTS = ("frequency_response", "frequency_response_comparison", "frequency_response_absolute", "frequency_response_fits",
    "fft_vs_offline_lockin_ratio", "hardware_vs_offline_lockin_ratio", "offline_lockin_phase", "hardware_vector_vs_scalar",
    "hardware_scalar_over_vector_ratio", "hardware_phase_vector", "daq_picked_frequency_error", "response_spectrum_examples")
ACQUISITION = (
    Stage("A01", "参数、映射与安全预检", "准备", (), quality_gates=("无硬件预检；双主电压回环仅允许 HF2，拒绝 MFLI",)),
    Stage("A02", "连接设备与输入初始化", "准备", ("A01",), quality_gates=("按资源复用；不占用 rf_coil 别名；TEC 串口失败告警跳过",)),
    Stage("A03", "参考时钟同步与严格回读", "准备", ("A02",), quality_gates=("已连接时钟按设备库同步，失败不启动输出",)),
    Stage("A04", "主场、光功率与 TEC 目标温度", "初始条件", ("A03",),
        ("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power", "FIXED_PARAMS.temperature"),
        quality_gates=("输出写入前安全校验；TEC 只写目标，不启停、不判稳；温控 5 V DC + ON",)),
    Stage("A05", "Pump 脉冲、SYNC 与 Z 外触发源", "激励", ("A04",),
        ("PUMP_MOD_FREQ", "PUMP_MOD_AMPLITUDE", "PUMP_MOD_DUTY", "RF_GATE_AMPLITUDE", "RF_GATE_OFFSET")),
    Stage("A06", "主解调与 Aux 物理回环准备", "锁相", ("A05",), LOCKIN,
        quality_gates=("主输入高阻/DC；Aux 必须有明确限值，主解调 Y → Aux → Signal Input 2，严格回读",)),
    Stage("A07", "关闭 XY／Z 后主解调校相", "校相", ("A06",), TEMP,
        quality_gates=("主解调读数有限、R 足够且 theta 在 5 次内收敛至 1°；恢复温控后保存",)),
    Stage("A08", "恒定 XY 包络与载波校相", "校相", ("A07",), XY,
        display_parameters=(*XY, *TEMP), quality_gates=("DC 包络由标定换算且不超 AM 输入限值；保留原 X 单开→XY 同开迭代；未收敛仅记录诊断",)),
    Stage("A09", "测量时间常数与模式回读", "采集", ("A08",), ("HF2_DEMOD0_TC",),
        display_parameters=("HF2_DEMOD0_TC", "HF2_DEMOD0_RATE"), quality_gates=("保存请求及实际设置；后续模式漂移拒绝采集",)),
    Stage("A10", "逐点 RF 扫频、保存与本轮安全收尾", "采集", ("A09",), SCAN,
        ("raw/freq_*.npz", "raw/frequency_index.json", "raw/frequency_index.npz"), display_parameters=(*SCAN, *TEMP),
        quality_gates=("0 Hz 只关 Z、XY 保持开启；每频点恢复温控后保存；故障抢救 Y 和已读 RF 样本；统一收尾成功才封存完成",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (), quality_gates=("无前置；恢复温控，关闭 XY／Z，保留统一策略规定的主场、光功率、Pump／时序和锁相设置，只断开 TEC",)),
)
ANALYSIS = (
    Stage("B01", "加载原始记录、FFT 与软件锁相", "独立分析", (), RESPONSE_KEYS, ("responses.json",),
        quality_gates=("身份和格式已验证，索引路径及波形／实际率有效；0 Hz 不参与响应和带宽",)),
    Stage("B02", "三路带宽、原 Lorentzian／Bloch 拟合", "独立分析", ("B01",), FIT_KEYS,
        ("fit_data.json", "analysis.yaml", "analysis.json", "frequency_response.npz"),
        quality_gates=("保留原 FFT 背景扣除和两种硬件均值；拟合失败仍导出诊断，不伪造科学质量通过",)),
    Stage("B03", "原响应、比值、残差与相位图导出", "独立分析", ("B02",), artifacts=("plot_manifest.json",)),
)


_rules = DependencyRules(finish_id=FINISH_ID)
invalidated_stages = _rules.invalidated_stages
invalidate = _rules.invalidate
parameter_impacts = _rules.parameter_impacts
_validate = _rules.validate_selection


def validate_selection(stages, states, selected):
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("安全结束必须最后执行")
    return _validate(stages, states, selected)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(run_step_id="A10", finish_step_id=FINISH_ID,
        acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
        parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
        diagram_url=f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram")
