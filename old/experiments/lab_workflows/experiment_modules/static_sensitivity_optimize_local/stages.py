"""局部优化的唯一执行清单；每候选内部复用完整静磁场 DC 流程。"""
from dataclasses import asdict
from ..static_sensitivity.stages import Stage
from .models import StaticSensitivityOptimizeLocalParams
from .run_format import EXPERIMENT_ID, MANIFEST

FINISH_ID = "A06"
RUN_ID = "A04"
PLAN_KEYS = ("RUN_TAG_PREFIX", "BASELINE_PARAMS", "OPTIMIZE_SEQUENCE")
POINT_KEYS = tuple(field["name"] for field in StaticSensitivityOptimizeLocalParams().schema()["fields"]
    if field["name"] not in PLAN_KEYS)
ACQUISITION = (
    Stage("A01", "全部候选的无硬件预检", "准备", (),
        quality_gates=("基准与每个候选的输出限值、GS200 量程、映射和锁相能力均通过。",)),
    Stage("A02", "冻结单变量候选计划", "准备", ("A01",), PLAN_KEYS, (MANIFEST,),
        quality_gates=("保持维度和值的原顺序，基准重复点不去重，不累计最佳值。",)),
    Stage("A03", "连接仪器、同步时钟与回读", "准备", ("A02",),
        quality_gates=("复用同一静磁场设备会话；全部时钟严格回读一致。",)),
    Stage("A04", "逐候选校相、DC 色散与 Z OFF 噪声", "局部优化", ("A03",), POINT_KEYS, (MANIFEST,),
        quality_gates=("每候选重设基准和唯一覆盖量，重新校相；分组恢复温控后保存。",
            "硬件、恢复和写盘错误停止后继；仅离线拟合质量失败记录坏点后继续。",)),
    Stage("A05", "核对安全恢复并封存批次", "结束", ("A04",),
        quality_gates=("最后子运行的安全恢复已成功；全无效点允许封存并导出诊断。",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "批次身份、候选与子运行资格", "独立分析", (), artifacts=("analysis_inputs.json",),
        quality_gates=("批次已停止写入；路径无越界；DC 子运行身份与格式正确。",)),
    Stage("B02", "基准重复、漂移与最佳点", "独立分析", ("B01",), artifacts=("optimization_decision.json",),
        quality_gates=("只比较拟合有效且灵敏度有限的点；保留坏点原因，不插值补值。",)),
    Stage("B03", "汇总表、局部扫描图与建议", "独立分析", ("B02",), artifacts=(
        "optimization_local_summary.csv", "optimization_local_summary.yaml", "optimization_local_summary.png",
        "optimization_local_summary.pdf", "analysis.json", "analysis.yaml"),
        quality_gates=("最佳点只生成建议，不写仪器；父分析不重新拟合子运行或覆盖采集参数。",)),
)


def initial_states(stages):
    return {item.id: dict(status="pending", error="") for item in stages}


def invalidated_stages(stages, stage_id):
    affected = {stage_id}
    for stage in stages:
        if affected.intersection(stage.dependencies):
            affected.add(stage.id)
    return tuple(item for item in stages if item.id in affected and item.id != FINISH_ID)


def invalidate(stages, states, stage_id):
    for item in invalidated_stages(stages, stage_id):
        states[item.id] = dict(status="pending" if states[item.id]["status"] == "pending" else "stale", error="")


def validate_selection(stages, states, selected):
    ids = [item.id for item in stages]
    if not selected or len(set(selected)) != len(selected) or any(item not in ids for item in selected):
        raise ValueError("请选择有效且不重复的模块")
    available = {key for key, value in states.items() if value["status"] == "completed"}
    ordered = [item for item in stages if item.id in selected]
    for item in ordered:
        missing = set(item.dependencies) - available
        if missing:
            raise ValueError(f"{item.id} {item.title} 缺少前置模块：" + "、".join(
                stage.id + " " + stage.title for stage in stages if stage.id in missing))
        if item.id != "A01":
            available.difference_update(stage.id for stage in invalidated_stages(stages, item.id))
        available.add(item.id)
    return [item.id for item in ordered]


def parameter_impacts(stages):
    result = {}
    for item in stages:
        for key in item.parameters:
            result.setdefault(key, [])
            result[key] = list(dict.fromkeys([*result[key], *(stage.id for stage in invalidated_stages(stages, item.id))]))
    return result


def definition():
    def public(item):
        return asdict(item) | {"display_parameters": list(item.parameters if item.display_parameters is None else item.display_parameters)}
    return dict(acquisition=[public(item) for item in ACQUISITION], analysis=[public(item) for item in ANALYSIS],
        parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
        diagram_url=f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram")
