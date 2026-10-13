"""DirectAW 的唯一流程清单、参数归属和依赖失效规则。"""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    group: str
    dependencies: tuple[str, ...]
    parameters: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    display_parameters: tuple[str, ...] | None = None


FINISH_ID = "A16"
TEMP = ("TEMP_OFF_SETTLE_S", "TEMP_SWITCH_ON_LAG_S")
SCAN = (*TEMP, "TEMP_GROUP_SIZE", "RF_STEP_SETTLE_S", "SCAN_POINT_DURATION_S")
ACQUISITION = (
    Stage("A01", "启动预检", "启动准备", ()),
    Stage("A02", "设备连接", "启动准备", ("A01",)),
    Stage("A03", "参考时钟同步", "启动准备", ("A02",)),
    Stage("A04", "初始输出设置", "初始条件", ("A03",),
          ("FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power",
           "FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Temp_Switch")),
    Stage("A05", "温度稳定", "初始条件", ("A04",), ("FIXED_PARAMS.temperature",)),
    Stage("A06", "Pump 载波与门控", "激励与触发", ("A05",),
          ("PUMP_MOD_FREQ", "PUMP_MOD_AMPLITUDE", "PUMP_MOD_DUTY", "RF_GATE_AMPLITUDE", "RF_GATE_OFFSET")),
    Stage("A07", "XYZ 共同触发", "激励与触发", ("A06",),
          tuple(f"{axis}_TRIGGER_{name}" for axis in ("XY", "Z")
                for name in ("FREQ", "AMPLITUDE", "OFFSET", "DUTY", "PHASE")),
          display_parameters=tuple(f"XY_TRIGGER_{name}" for name in ("FREQ", "AMPLITUDE", "OFFSET", "DUTY", "PHASE"))),
    Stage("A08", "Bell-Bloom / Demod0 校相", "四级相位校准", ("A07",),
          ("DEMOD0_IDX", "DEMOD0_OSC_IDX", "DEMOD0_SIGNAL_RANGE", "DEMOD0_ORDER",
           "DEMOD0_TC_CALIB", "DEMOD0_RATE", *TEMP)),
    Stage("A09", "XY DirectAW 上传与迭代校相", "四级相位校准", ("A08",),
          ("ARB_WAVEFORM_FILE", "AW_REPEAT_FREQ", "XY_CTRL_K_HZ_PER_V", "XY_CTRL_B_HZ",
           "XY_CTRL_PHASE", "XY_CTRL_QUAD", "XY_CTRL_PHASE_TOL_DEG", "XY_CTRL_PHASE_MAX_ITER",
           "XY_CTRL_PHASE_MIN_R_V", "XY_CTRL_PHASE_MIN_R_RATIO"),
          ("raw/xy_aw_waveforms_initial.npz", "raw/xy_aw_waveforms.npz"),
          display_parameters=("ARB_WAVEFORM_FILE", "AW_REPEAT_FREQ", "XY_CTRL_K_HZ_PER_V", "XY_CTRL_B_HZ",
           "XY_CTRL_PHASE", "XY_CTRL_QUAD", "XY_CTRL_PHASE_TOL_DEG", "XY_CTRL_PHASE_MAX_ITER",
           "XY_CTRL_PHASE_MIN_R_V", "XY_CTRL_PHASE_MIN_R_RATIO", *TEMP)),
    Stage("A10", "HF2 回环与 Z RF 响应准备", "四级相位校准", ("A09",),
          ("DEMOD0_TC_MEAS", "DEMOD3_IDX", "DEMOD3_OSC_IDX", "DEMOD3_ADC_SELECT",
           "DEMOD3_ORDER", "DEMOD3_TC", "DEMOD3_RATE", "LOOPBACK_AUX_SCALE", "LOOPBACK_AUX_OFFSET_V",
           "LOOPBACK_INPUT_RANGE_V",
           "LOOPBACK_INPUT_DIFF", "Z_RF_FREQ", "Z_RF_AMPLITUDE_INIT", "Z_BURST_PHASE_INIT")),
    Stage("A11", "控制 AM 与 Z RF 触发相位扫描", "四级相位校准", ("A10",),
          ("Z_PHASE_SCAN_START_DEG", "Z_PHASE_SCAN_STOP_DEG", "Z_PHASE_SCAN_STEP_DEG",
           "TEMP_GROUP_SIZE", "RF_STEP_SETTLE_S", "SCAN_POINT_DURATION_S"),
          ("raw/z_phase_scan.npz", "results/z_phase_scan.png", "results/z_phase_scan.pdf"),
          display_parameters=("Z_PHASE_SCAN_START_DEG", "Z_PHASE_SCAN_STOP_DEG", "Z_PHASE_SCAN_STEP_DEG",
                              "Z_RF_AMPLITUDE_INIT", *SCAN)),
    Stage("A12", "Demod3 校相", "四级相位校准", ("A11",),
          display_parameters=("Z_RF_AMPLITUDE_INIT", *TEMP)),
    Stage("A13", "有符号 Z RF 幅度扫描", "采集与结束", ("A12",),
          ("RUN_TAG", "RF_AMP_START", "RF_AMP_STOP", "RF_AMP_POINTS", "Z_V_TO_NT"),
          ("raw/response_data.npz", "experiment_config.yaml"),
          display_parameters=("RUN_TAG", "RF_AMP_START", "RF_AMP_STOP", "RF_AMP_POINTS", "Z_V_TO_NT", *SCAN)),
    Stage("A14", "零 RF 工作点 X 噪声采集", "采集与结束", ("A13",),
          ("NOISE_N_AVG", "NOISE_DURATION", "NOISE_RATE", "DEMOD3_NOISE_TC", "DEMOD3_NOISE_ORDER"),
          ("raw/noise_D3_*.npz",),
          display_parameters=("NOISE_N_AVG", "NOISE_DURATION", "NOISE_RATE", "DEMOD3_NOISE_TC", "DEMOD3_NOISE_ORDER", *TEMP)),
    Stage("A15", "响应恢复与本轮安全收尾", "采集与结束", ("A14",)),
    Stage(FINISH_ID, "安全收尾 / 结束会话", "采集与结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载配置与校验原始数据", "独立离线分析", ()),
    Stage("B02", "X 色散响应与斜率", "独立离线分析", ("B01",), artifacts=("response_fit.npz",)),
    Stage("B03", "Welch X 噪声 PSD", "独立离线分析", ("B02",), ("NOISE_NPERSEG",), ("psd_avg.npz",)),
    Stage("B04", "RF 灵敏度、频响补偿与结果图", "独立离线分析", ("B03",), artifacts=(
        "sensitivity.npz", "analysis.yaml", "analysis.json", "response_curve.png", "response_curve.pdf",
        "noise_psd.png", "noise_psd.pdf", "sensitivity.png", "sensitivity.pdf", "full_analysis.png", "full_analysis.pdf")),
)


def initial_states(stages):
    return {stage.id: {"status": "pending", "error": ""} for stage in stages}


def invalidated_stages(stages, stage_id):
    affected = {stage_id}
    for stage in stages:
        if affected.intersection(stage.dependencies):
            affected.add(stage.id)
    return tuple(stage for stage in stages if stage.id in affected and stage.id != FINISH_ID)


def invalidate(stages, states, stage_id):
    for stage in invalidated_stages(stages, stage_id):
        states[stage.id] = {"status": "pending" if states[stage.id]["status"] == "pending" else "stale", "error": ""}


def parameter_impacts(stages):
    result = {}
    for stage in stages:
        for name in stage.parameters:
            result.setdefault(name, [item.id for item in invalidated_stages(stages, stage.id)])
    return result


def validate_selection(stages, states, selected):
    ids = [stage.id for stage in stages]
    if not selected or len(set(selected)) != len(selected) or any(item not in ids for item in selected):
        raise ValueError("请选择有效且不重复的模块")
    ordered = [item for item in ids if item in selected]
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("安全收尾必须最后执行")
    available = {key for key, value in states.items() if value["status"] == "completed"}
    for item in ordered:
        stage = stages[ids.index(item)]
        missing = [key for key in stage.dependencies if key not in available]
        if missing:
            raise ValueError(f"{item} {stage.title} 缺少前置模块：" + "、".join(
                f"{key} {stages[ids.index(key)].title}" for key in missing))
        if item != "A01":
            available.difference_update(stage.id for stage in invalidated_stages(stages, item))
        available.add(item)
    return ordered


def definition(*, acquisition=ACQUISITION, analysis=ANALYSIS, experiment_id="rf-sensitivity-aw"):
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return {"acquisition": [public(stage) for stage in acquisition],
            "analysis": [public(stage) for stage in analysis],
            "parameter_impacts": {"acquisition": parameter_impacts(acquisition), "analysis": parameter_impacts(analysis)},
            "diagram_url": f"/api/experiments/{experiment_id}/workflow/diagram"}
