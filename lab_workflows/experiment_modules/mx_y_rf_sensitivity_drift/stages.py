"""长飘真实采集与父趋势分析节点、参数归属和依赖失效。"""
from dataclasses import asdict
from ..mx_y_rf_sensitivity.stages import ANALYSIS_KEYS as CHILD_ANALYSIS_KEYS
from lab_workflows.workflow_stages import Stage, initial_states

FINISH_ID = "A11"
RUN_ID = "A09"
ANALYSIS_KEYS = ()
ACQUISITION = (
    Stage("A01", "启动预检", "准备", (), quality_gates=("参数、映射、锁相能力和安全限值有效；长飘计划至少一轮",)),
    Stage("A02", "设备连接与初态回读", "准备", ("A01",), ("RUN_TAG",)),
    Stage("A03", "参考时钟同步与回读", "准备", ("A02",), quality_gates=("全部已连接时钟严格回读一致",)),
    Stage("A04", "主场、光功率与辅助场归零", "初始条件", ("A03",),
        ("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power")),
    Stage("A05", "TEC 目标温度", "初始条件", ("A04",), ("FIXED_PARAMS.temperature",)),
    Stage("A06", "Pump 连续载波与常开门控", "激励", ("A05",),
        ("PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V")),
    Stage("A07", "Y RF 正弦与 R 解调准备", "激励", ("A06",),
        ("Y_RF_FREQUENCY_HZ", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V", "FREQUENCY_RF_AMPLITUDE_VPP",
         "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER")),
    Stage("A08", "半开区间的绝对时间轮次计划", "调度", ("A07",),
        ("DRIFT_INTERVAL_S", "DRIFT_DURATION_S", "DRIFT_START_IMMEDIATELY"), ("raw/drift_manifest.yaml",),
        quality_gates=("计划偏移位于 [0, DRIFT_DURATION_S)，超期轮按原顺序补跑",)),
    Stage("A09", "逐轮标准采集、即时分析与趋势", "采集", ("A08",),
        ("Y_RF_AMP_START_VPP", "Y_RF_AMP_STOP_VPP", "Y_RF_AMP_POINTS", "RESPONSE_DURATION_S",
         "NOISE_N_AVG", "NOISE_DURATION_S", "NOISE_RATE_SA_S", "NOISE_TIME_CONSTANT_S", "NOISE_DEMOD_ORDER",
         "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S", "R_POINT_MAX_ATTEMPTS", *CHILD_ANALYSIS_KEYS),
        ("raw/drift_manifest.yaml", "results/drift_summary.*", "results/drift_trend*.png"),
        quality_gates=("单轮 R 点质量不合格或拟合失败记录为无效轮并继续；硬件采集、恢复或原始数据保存异常停止后继轮次",)),
    Stage("A10", "响应模式恢复、安全收尾与封存", "结束", ("A09",),
        quality_gates=("使用最后已执行配置恢复解调与危险输出，成功后才封存父运行",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "父清单与标准子摘要资格", "分析输入", (), (), ("analysis_inputs.json",),
        quality_gates=("父/子身份、原始格式、轮次与路径引用有效；父运行已停止写入；全无效轮仍可导出诊断",)),
    Stage("B02", "轮次表格与数值趋势", "趋势汇总", ("B01",), (),
        ("drift_summary.csv", "drift_summary.json"),
        quality_gates=("只提取已有子 analysis.json；无效与未执行轮保留原索引、状态及排除原因",)),
    Stage("B03", "原趋势图与报告", "趋势报告", ("B02",), (),
        ("drift_trend.png", "drift_trend_clock_time.png", "analysis.yaml", "analysis.json"),
        quality_gates=("B02 数值产物 SHA 仍有效；幅度等效线宽不解释为扫频共振线宽",)),
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
            result[key] = list(dict.fromkeys([*result.get(key, []), *(item.id for item in invalidated_stages(stages, stage.id))]))
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
        return asdict(stage) | {"display_parameters": list(stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(run_step_id="A09", finish_step_id=FINISH_ID,
        acquisition=[public(stage) for stage in ACQUISITION], analysis=[public(stage) for stage in ANALYSIS],
        parameter_impacts={"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        diagram_url="/api/experiments/mx-y-rf-sensitivity-drift/workflow/diagram")
