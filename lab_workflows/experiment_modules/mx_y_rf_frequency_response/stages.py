"""Mx Y RF 频率响应的唯一模块清单、编辑归属与失效规则。"""
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
TEMP = ("SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S")
QUALITY = ("R_BAD_POINT_STD_THRESHOLD_V", "R_POINT_MAX_ATTEMPTS")
FIT = ("FIT_R_SQUARED_MIN", "FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX")
ANALYSIS_KEYS = (*FIT, "R_BAD_POINT_STD_THRESHOLD_V")
ACQUISITION = (
    Stage("A01", "启动预检", "准备", (), quality_gates=("参数、映射、锁相采集能力与安全限值全部有效",)),
    Stage("A02", "设备连接与初态回读", "准备", ("A01",), ("RUN_TAG",)),
    Stage("A03", "参考时钟同步与回读", "准备", ("A02",), quality_gates=("参考时钟严格回读一致",)),
    Stage("A04", "主场、光功率与辅助场归零", "初始条件", ("A03",),
        ("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power")),
    Stage("A05", "TEC 目标温度", "初始条件", ("A04",), ("FIXED_PARAMS.temperature",)),
    Stage("A06", "Pump 连续载波与常开门控", "激励", ("A05",),
        ("PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V")),
    Stage("A07", "Y RF 正弦与 R 解调准备", "激励", ("A06",),
        ("Y_RF_FREQUENCY_HZ", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V",
         "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER")),
    Stage("A08", "嵌套 RF 幅度-频率响应扫描", "采集", ("A07",),
        ("RF_AMPLITUDE_START_VPP", "RF_AMPLITUDE_STOP_VPP", "RF_AMPLITUDE_POINTS",
         "FREQUENCY_START_HZ", "FREQUENCY_STOP_HZ", "FREQUENCY_POINTS",
         "FREQUENCY_DURATION_S", *TEMP, *QUALITY),
        ("raw/response_*.npz", "raw/frequency_response.npz"),
        quality_gates=("每个 (幅度, 频率) 点 std(R) 合格；重采用尽后保存诊断并阻断后继",)),
    Stage("A09", "本轮安全收尾与目录封存", "结束", ("A08",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载配置与原始数据资格", "独立分析", (), quality_gates=(
        "运行身份、独立原始格式、递增扫描轴与 R 矩阵有效；至少一条曲线有 6 个有效点，目录已停止写入",)),
    Stage("B02", "布洛赫线形拟合与饱和度诊断", "独立分析", ("B01",), ANALYSIS_KEYS,
        ("bloch_fit.npz", "fit_diagnostics.yaml"),
        quality_gates=("至少一条幅度曲线通过 R²、γ 相对不确定度（有限时）与 γ 小于扫描跨度一半的门槛",)),
    Stage("B03", "结果图与分析报告", "独立分析", ("B02",), artifacts=(
        "frequency_response.png", "s_vs_amplitude.png", "analysis.yaml", "analysis.json")),
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
        "diagram_url": "/api/experiments/mx-y-rf-frequency-response/workflow/diagram"}
