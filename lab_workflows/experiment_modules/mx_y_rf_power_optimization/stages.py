"""光功率网格的唯一执行顺序、节点编辑归属与参数影响。"""
from dataclasses import asdict
from ..mx_y_rf_sensitivity.stages import Stage

FINISH_ID = "A10"
RUN_ID = "A08"
TEMP = ("SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S")
ANALYSIS_KEYS = ("FIT_R_SQUARED_MIN", "FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX",
    "R_BAD_POINT_STD_THRESHOLD_V", "Y_RF_NT_PER_VPP", "LOW_FREQ_SKIP_HZ",
    "LINEAR_CHECK_GAMMA_FRACTION", "SLOPE_AGREEMENT_TOLERANCE")
ACQUISITION = (
    Stage("A01", "启动预检", "准备", (), quality_gates=("参数、映射、锁相能力与安全限值有效",)),
    Stage("A02", "设备连接与初态回读", "准备", ("A01",), ("RUN_TAG",)),
    Stage("A03", "参考时钟同步与严格回读", "准备", ("A02",),
        quality_gates=("全部已连接时钟设备严格回读一致",)),
    Stage("A04", "主场、基准光功率与辅助场归零", "初始条件", ("A03",),
        ("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power")),
    Stage("A05", "TEC 目标温度", "初始条件", ("A04",), ("FIXED_PARAMS.temperature",)),
    Stage("A06", "Pump 连续载波与常开门控", "激励", ("A05",),
        ("PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V")),
    Stage("A07", "Y RF 正弦与 R 响应解调准备", "激励", ("A06",),
        ("Y_RF_FREQUENCY_HZ", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V",
         "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER")),
    Stage(RUN_ID, "Pump × Probe 蛇形网格：幅度响应与零 RF 噪声", "采集", ("A07",),
        ("PUMP_POWER_START_V", "PUMP_POWER_STOP_V", "PUMP_POWER_POINTS",
         "PROBE_POWER_START_V", "PROBE_POWER_STOP_V", "PROBE_POWER_POINTS",
         "Y_RF_AMP_START_VPP", "Y_RF_AMP_STOP_VPP", "Y_RF_AMP_POINTS", "RESPONSE_DURATION_S",
         "R_BAD_POINT_STD_THRESHOLD_V", "R_POINT_MAX_ATTEMPTS", "NOISE_N_AVG",
         "NOISE_DURATION_S", "NOISE_RATE_SA_S", "NOISE_TIME_CONSTANT_S", "NOISE_DEMOD_ORDER", *TEMP),
        ("raw/power_grid.npz", "raw/point_manifest.yaml", "raw/points/**/*.npz"),
        quality_gates=("R 标准差超限按上限重采；仅在本点安全收尾成功后标记无效并继续，设备与恢复错误立即停止",)),
    Stage("A09", "本轮安全收尾、基准光功率恢复与封存", "结束", (RUN_ID,),
        display_parameters=("FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power")),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载配置、网格及原始数据资格", "独立分析", (),
        quality_gates=("实验身份、格式、点索引与路径合法，目录不在采集中；坏波形沿用原逐点拒绝规则，不阻断其它点",)),
    Stage("B02", "双斜率灵敏度、热图与逐点诊断", "独立分析", ("B01",), ANALYSIS_KEYS,
        ("optimization.npz", "optimization.yaml", "optimization.json", "points",
         "sensitivity_heatmap.png", "sensitivity_heatmap.pdf", "slope_heatmap.png", "slope_heatmap.pdf",
         "zero_point_sensitivity_heatmap.png", "zero_point_sensitivity_heatmap.pdf",
         "zero_point_slope_heatmap.png", "zero_point_slope_heatmap.pdf", "hwhm_heatmap.png", "hwhm_heatmap.pdf",
         "best_point_full_analysis.png", "best_point_full_analysis.pdf",
         "best_zero_point_full_analysis.png", "best_zero_point_full_analysis.pdf"),
        quality_gates=("无有效最优点时保留诊断并标记质量未通过，不执行后继汇总",)),
    Stage("B03", "双排名与最优点报告汇总", "独立分析", ("B02",),
        artifacts=("analysis.yaml", "analysis.json")),
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
            result[key] = list(dict.fromkeys([*result.get(key, []),
                *(item.id for item in invalidated_stages(stages, stage.id))]))
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
    return dict(acquisition=[public(stage) for stage in ACQUISITION], analysis=[public(stage) for stage in ANALYSIS],
        parameter_impacts={"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        diagram_url="/api/experiments/mx-y-rf-power-optimization/workflow/diagram")
