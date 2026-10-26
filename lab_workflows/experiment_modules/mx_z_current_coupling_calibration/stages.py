"""本 ID 采集与独立分析的真实步骤、参数影响和质量门槛。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import Stage, initial_states


FINISH_ID = "A11"
RUN_ID = "A09"
ANALYSIS_KEYS = ("LINEAR_R_SQUARED_MIN",)
PREDICTION_KEYS = ("ZERO_BIAS_CENTER_FREQUENCY_HZ", "Z_INITIAL_HZ_PER_V", "Z_PREDICTION_POLARITY", "Z_BIAS_START_V")
SCOPE_KEYS = (
    "SENSE_SCOPE_SAMPLE_RATE_SA_S", "SENSE_SCOPE_DURATION_S", "SENSE_SCOPE_INITIAL_SCALE_V_DIV",
    "SENSE_SCOPE_SCALE_MIN_V_DIV", "SENSE_SCOPE_SCALE_MAX_V_DIV", "SENSE_SCOPE_VERTICAL_DIVISIONS",
    "SENSE_SCOPE_AUTO_RANGE_LOW_FRACTION", "SENSE_SCOPE_AUTO_RANGE_HIGH_FRACTION",
    "SENSE_SCOPE_AUTO_OFFSET_TOLERANCE_FRACTION", "SENSE_SCOPE_AUTO_RANGE_MAX_ATTEMPTS",
    "SENSE_SCOPE_AUTO_RANGE_ALLOW_SHRINK",
)
CURRENT_KEYS = ("SENSE_RESISTOR_OHM",
    "SENSE_RESISTOR_POWER_RATING_W", "SENSE_RESISTOR_POWER_DERATING", "MAXIMUM_CURRENT_A")
ACQUISITION = (
    Stage("A01", "启动预检", "准备", (), display_parameters=("SENSE_RESISTOR_TOLERANCE_PERCENT",),
        quality_gates=("参数、安全限值、设备映射与完整锁相频率窗口预检通过",)),
    Stage("A02", "设备连接与初态回读", "准备", ("A01",), ("RUN_TAG",)),
    Stage("A03", "参考时钟同步与回读", "准备", ("A02",),
        quality_gates=("所有已连接时钟设备的实际参考源与设备库目标一致",)),
    Stage("A04", "辅助场归零、光功率与固定主场", "初始条件", ("A03",),
        ("FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power", "FIXED_PARAMS.main_magnetic_field")),
    Stage("A05", "TEC 目标温度", "初始条件", ("A04",), ("FIXED_PARAMS.temperature",)),
    Stage("A06", "Pump 连续载波与常开门控", "激励", ("A05",),
        ("PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V")),
    Stage("A07", "Y RF 正弦与 R 解调准备", "激励", ("A06",),
        (*PREDICTION_KEYS, "Y_RF_AMPLITUDE_VPP", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V",
         "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER")),
    Stage("A08", "SDS CH3 固定采样率与自动量程准备", "采集准备", ("A07",),
        (*SCOPE_KEYS, "SENSE_SCOPE_CHANNEL"), display_parameters=SCOPE_KEYS,
        quality_gates=("SDS 固定采样率存储模式回读一致，实际率与记录点数满足采集要求，量程和偏置有效",)),
    Stage(RUN_ID, "逐 Z 电流与局部 Y RF 联合扫描", "采集", ("A08",),
        (*PREDICTION_KEYS, "Z_BIAS_STOP_V", "Z_BIAS_STEP_V", "BIDIRECTIONAL_SCAN", "CURRENT_SETTLE_TIME_S",
         "FREQUENCY_HALF_WIDTH_HZ", "FREQUENCY_STEP_HZ", "Y_RF_AMPLITUDE_VPP", "FREQUENCY_DURATION_S",
         "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S", "R_BAD_POINT_STD_THRESHOLD_V", "R_POINT_MAX_ATTEMPTS",
         *CURRENT_KEYS), ("raw/z_scan_index.npz", "raw/z_*/*.npz"), quality_gates=(
             "实际 Y RF 正弦及 SDS 存储/触发模式与已执行准备一致",
             "SDS 完整记录且电压有限，显示贴边比例 < 0.98，峰值电流及电阻功率不超参数限值",
             "每个 R 点 std(R) <= R_BAD_POINT_STD_THRESHOLD_V；最多 R_POINT_MAX_ATTEMPTS 次，不通过即阻断后继",
             "每个窗口先恢复温控再保存；恢复或写盘失败停止后继并抢救已读数据")),
    Stage("A10", "本轮安全收尾与封存", "结束", (RUN_ID,),
        quality_gates=("安全收尾报告 completed 为真后才封存为 completed",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "运行身份、格式与完整频扫加载", "分析准备", (),
        artifacts=("analysis_inputs.json",), quality_gates=(
            "本实验身份和原始格式有效，采集已结束，至少一份完整频扫具备同长 R 数组及实际电流/电阻电压字段",)),
    Stage("B02", "逐 Z 共振拟合与排除诊断", "共振拟合", ("B01",),
        artifacts=("curve_fits.json",)),
    Stage("B03", "实际电流和电阻电压加权标定", "线性标定", ("B02",),
        ANALYSIS_KEYS, ("calibration_evaluation.json",)),
    Stage("B04", "科学图、结果导出与质量结论", "结果导出", ("B03",),
        artifacts=("analysis.json", "analysis.yaml", "calibration_results.npz",
                   "current_coupling_calibration.png", "frequency_response_fits.png"),
        quality_gates=(
            "诊断始终导出；标定可用须满足实际电流加权线性拟合成功、R2 >= LINEAR_R_SQUARED_MIN、电阻电压线性拟合有效且无显示贴边或饱和",)),
)


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
            result[key] = list(dict.fromkeys([*result.get(key, ()),
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
        if missing := set(stage.dependencies) - available:
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
    return {"run_step_id": "A09", "finish_step_id": FINISH_ID,
            "acquisition": [public(stage) for stage in ACQUISITION],
        "analysis": [public(stage) for stage in ANALYSIS],
        "parameter_impacts": {"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        "diagram_url": "/api/experiments/mx-z-current-coupling-calibration/workflow/diagram"}
