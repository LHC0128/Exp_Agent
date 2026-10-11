"""XY 正弦实验的流程定义；脚本、后端和界面共用。"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    group: str
    dependencies: tuple[str, ...]
    parameters: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()


ACQUISITION = (
    Stage("A01", "启动预检", "启动准备", ()),
    Stage("A02", "设备连接", "启动准备", ("A01",)),
    Stage("A03", "参考时钟同步", "启动准备", ("A02",)),
    Stage("A04", "初始输出设置", "初始条件", ("A03",),
          ("FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power",
           "FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Temp_Switch")),
    Stage("A05", "温度稳定", "初始条件", ("A04",), ("FIXED_PARAMS.temperature",)),
    Stage("A06", "Pump 配置", "激励与触发", ("A05",),
          ("PUMP_MOD_FREQ", "PUMP_MOD_AMPLITUDE", "PUMP_MOD_DUTY", "RF_GATE_AMPLITUDE", "RF_GATE_OFFSET")),
    Stage("A07", "共用触发配置", "激励与触发", ("A06",),
          ("XY_TRIGGER_FREQ", "XY_TRIGGER_AMPLITUDE", "XY_TRIGGER_OFFSET", "XY_TRIGGER_DUTY", "XY_TRIGGER_PHASE")),
    Stage("A08", "Demod0 配置与校相", "相位校准", ("A07",),
          ("HF2_DEMOD_IDX", "HF2_OSC_FREQ", "HF2_SIGNAL_RANGE", "HF2_DEMOD_ORDER",
           "HF2_DEMOD_TC", "HF2_DEMOD_RATE", "TEMP_SWITCH_ON_SETTLE_S")),
    Stage("A09", "XY 正弦与 Burst 初始化", "相位校准", ("A08",),
          ("XY_CTRL_FREQ", "XY_CTRL_PHASE", "XY_CTRL_QUAD", "XY_CALIB_ENVELOPE_V")),
    Stage("A10", "XY 迭代校相", "相位校准", ("A09",),
          ("XY_PHASE_CAL_TOL_DEG", "XY_PHASE_CAL_MAX_ITER", "XY_PHASE_CAL_MIN_R_V", "XY_PHASE_CAL_MIN_R_RATIO")),
    Stage("A11", "DAQ 准备", "扫描与结束", ("A10",),
          ("HF2_DAQ_DURATION", "HF2_DAQ_TC", "HF2_DAQ_RATE", "ANALYSIS_BIN_WIDTH_HZ",
           "TARGET_NOISE_FREQ_START_HZ", "TARGET_NOISE_FREQ_STOP_HZ", "TARGET_NOISE_FREQ_POINTS",
           "XY_CTRL_K_HZ_PER_V", "XY_CTRL_B_HZ", "XY_CALIBRATION_MIN_ENVELOPE_V", "XY_CALIBRATION_MAX_ENVELOPE_V")),
    Stage("A12", "扫描采集", "扫描与结束", ("A11",), ("XY_SETTLE_TIME", "RUN_TAG"),
          ("raw/control_scan_axes.npz", "raw/waveform_C*.npy", "experiment_config.yaml")),
    Stage("A13", "解调器恢复", "扫描与结束", ("A12",)),
    Stage("A14", "安全收尾 / 结束会话", "扫描与结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载配置与准备分析", "加载配置与准备分析", ()),
    Stage("B02", "计算 Welch PSD", "Welch PSD", ("B01",),
          ("ANALYSIS_BIN_WIDTH_HZ", "ANALYSIS_FREQUENCY_MIN_HZ", "ANALYSIS_FREQUENCY_MAX_HZ"),
          ("psd_matrix.npz", "noise_spectrum_2d.png", "noise_spectrum_2d.pdf")),
    Stage("B03", "移动脊线重标定", "移动脊线重标定", ("B02",), (),
          ("calibration.npz", "calibration.png", "calibration.pdf")),
    Stage("B04", "全控制轴拟合与背景验收", "拟合与背景验收", ("B03",),
          ("ANALYSIS_FIT_HALF_WIDTH_HZ",),
          ("popt_fit.npz", "noise_spectra.npz", "noise_spectra.csv", "fit_diagnostics.npz",
           "noise_spectra_extracted.png", "noise_spectra_extracted.pdf",
           "psd_column_diagnostics.png", "psd_column_diagnostics.pdf", "fit_quality.png", "fit_quality.pdf",
           "background_identifiability.png", "background_identifiability.pdf")),
    Stage("B05", "Mx 对照与分析汇总", "Mx 对照与汇总", ("B04",), (), ("global_comparison.npz",)),
)


def initial_states(stages):
    return {stage.id: {"status": "pending", "error": ""} for stage in stages}


def invalidate(stages, states, stage_id):
    """前置重跑后，历史记录保留但后续不得作为有效凭据。"""
    start = next(i for i, stage in enumerate(stages) if stage.id == stage_id)
    for stage in stages[start:]:
        if stage.id != "A14":
            states[stage.id] = {"status": "stale" if states[stage.id]["status"] != "pending" else "pending", "error": ""}


def validate_selection(stages, states, selected):
    ids = [stage.id for stage in stages]
    if not selected or len(set(selected)) != len(selected) or any(item not in ids for item in selected):
        raise ValueError("请选择有效且不重复的模块")
    ordered = [item for item in ids if item in selected]
    if "A14" in selected and selected[-1] != "A14":
        raise ValueError("安全收尾必须最后执行")
    available = {key for key, value in states.items() if value["status"] == "completed"}
    for item in ordered:
        stage = stages[ids.index(item)]
        missing = [key for key in stage.dependencies if key not in available]
        if missing:
            raise ValueError(f"{item} {stage.title} 缺少前置模块：" + "、".join(
                f"{key} {stages[ids.index(key)].title}" for key in missing))
        if item not in ("A01", "A14"):
            available.difference_update(ids[ids.index(item) + 1:])
        available.add(item)
    return ordered


def definition():
    return {"acquisition": [asdict(stage) for stage in ACQUISITION],
            "analysis": [asdict(stage) for stage in ANALYSIS],
            "diagram_url": "/api/experiments/noise-spectrum-xy/workflow/diagram"}
