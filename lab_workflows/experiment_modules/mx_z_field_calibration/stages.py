"""Z 电压频率标定的步骤、参数编辑归属和后端失效规则。"""
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
    quality_gates: tuple[str, ...] = ()


FINISH_ID = "A10"
ANALYSIS_KEYS = ("LINEAR_R_SQUARED_MIN",)
PREDICTION_KEYS = ("ZERO_BIAS_CENTER_FREQUENCY_HZ", "Z_INITIAL_HZ_PER_V", "Z_PREDICTION_POLARITY", "Z_BIAS_START_V")
ACQUISITION = (
    Stage("A01", "启动预检", "准备", (), quality_gates=("参数、接线映射、独立通道与安全限值有效",)),
    Stage("A02", "设备连接与初态回读", "准备", ("A01",), ("RUN_TAG",)),
    Stage("A03", "参考时钟同步与回读", "准备", ("A02",), quality_gates=("参考时钟严格回读一致",)),
    Stage("A04", "辅助场归零、光功率与固定主场", "初始条件", ("A03",),
          ("FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power", "FIXED_PARAMS.main_magnetic_field")),
    Stage("A05", "TEC 目标温度", "初始条件", ("A04",), ("FIXED_PARAMS.temperature",)),
    Stage("A06", "Pump 连续载波与常开门控", "激励", ("A05",),
          ("PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V")),
    Stage("A07", "Y RF 正弦与 R 解调准备", "激励", ("A06",),
          (*PREDICTION_KEYS, "Y_RF_AMPLITUDE_VPP", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V",
           "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER")),
    Stage("A08", "Z 电压与局部 Y RF 扫频", "采集", ("A07",),
          (*PREDICTION_KEYS, "Z_BIAS_STOP_V", "Z_BIAS_STEP_V", "FREQUENCY_HALF_WIDTH_HZ", "FREQUENCY_STEP_HZ",
           "Y_RF_AMPLITUDE_VPP", "FREQUENCY_DURATION_S", "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S",
           "R_BAD_POINT_STD_THRESHOLD_V", "R_POINT_MAX_ATTEMPTS"),
          ("raw/z_scan_index.npz", "raw/z_*/*.npz"),
          quality_gates=("每点 R 标准差不超过 R_BAD_POINT_STD_THRESHOLD_V；重采用尽后保留诊断并阻断后继",)),
    Stage("A09", "本轮安全收尾与封存", "结束", ("A08",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "配置与原始频扫资格", "独立分析", (), quality_gates=("实验身份、原始格式、索引路径和 R 频扫字段有效",)),
    Stage("B02", "逐 Z 电压 Lorentzian 共振中心", "独立分析", ("B01",), artifacts=("curve_fits.json",)),
    Stage("B03", "加权线性 Z 电压标定", "独立分析", ("B02",), ANALYSIS_KEYS,
          ("calibration_evaluation.json",),
          quality_gates=("记录有效 Lorentzian 中心与 LINEAR_R_SQUARED_MIN 判据；不合格保留掩码、NaN 和原因，仍可导出诊断",)),
    Stage("B04", "标定报告与结果图", "独立分析", ("B03",), artifacts=(
        "analysis.yaml", "analysis.json", "calibration_results.npz", "frequency_response_fits.png",
        "z_frequency_calibration.png", "z_frequency_residuals.png")),
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
        for key in stage.parameters:
            result.setdefault(key, [])
            result[key] = list(dict.fromkeys([*result[key], *(item.id for item in invalidated_stages(stages, stage.id))]))
    return result


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
        missing = set(stage.dependencies) - available
        if missing:
            raise ValueError(f"{item} {stage.title} 缺少前置模块：" + "、".join(
                f"{key} {stages[ids.index(key)].title}" for key in ids if key in missing))
        if item != "A01":
            available.difference_update(stage.id for stage in invalidated_stages(stages, item))
        available.add(item)
    return ordered


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return {"acquisition": [public(stage) for stage in ACQUISITION],
        "analysis": [public(stage) for stage in ANALYSIS],
        "parameter_impacts": {"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        "diagram_url": "/api/experiments/mx-z-field-calibration/workflow/diagram"}
