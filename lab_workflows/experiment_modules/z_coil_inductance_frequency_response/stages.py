"""完整运行、分步会话、参数编辑和失效预览共用的执行清单。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import Stage, initial_states
from .run_format import EXPERIMENT_ID


FINISH_ID = "A07"
FREQUENCIES = ("FREQUENCY_START_HZ", "FREQUENCY_STOP_HZ", "FREQUENCY_POINTS")
SCOPE = ("SCOPE_SAMPLE_RATE_SA_S", "SCOPE_CYCLES", "SCOPE_INITIAL_SCALE",
    "SCOPE_MEASURED_CHANNEL", "SCOPE_TRIGGER_CHANNEL", "SCOPE_SCALE_MIN", "SCOPE_SCALE_MAX",
    "SCOPE_VERTICAL_DIVISIONS", "SCOPE_AUTO_RANGE_LOW_FRACTION", "SCOPE_AUTO_RANGE_HIGH_FRACTION",
    "SCOPE_AUTO_RANGE_MAX_ATTEMPTS", "SCOPE_TRIGGER_LEVEL_V")
ACQUISITION = (
    Stage("A01", "参数与接线预检", "启动准备", (),
        quality_gates=("参数、安全限值、接线映射及独立通道兼容检查全部通过",)),
    Stage("A02", "设备连接", "启动准备", ("A01",)),
    Stage("A03", "参考时钟同步与回读", "启动准备", ("A02",),
        quality_gates=("参考时钟写入后严格回读一致，失败先收尾再断开",)),
    Stage("A04", "正弦与共同触发配置", "激励与采集", ("A03",),
        ("DRIVE_AMPLITUDE_VPP", "DRIVE_OFFSET_V", "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP",
         "TRIGGER_OFFSET_V", "TRIGGER_DUTY_PERCENT", *FREQUENCIES)),
    Stage("A05", "Scope 首频配置与回读", "激励与采集", ("A04",), SCOPE,
        display_parameters=("SCOPE_SAMPLE_RATE_SA_S", "SCOPE_CYCLES", "SCOPE_INITIAL_SCALE")),
    Stage("A06", "线性扫频与重复帧保存", "激励与采集", ("A05",),
        ("RUN_TAG", "FREQUENCY_SETTLE_S", "SCOPE_REPEATS", "SCOPE_CYCLES"),
        ("experiment_config.yaml", "raw/scope_f*_r*.npz", "raw/frequency_index.json", "raw/frequency_index.npz"),
        quality_gates=("Scope 完整帧与自动量程检查通过；读取或写盘失败保留已取得的帧并停止后续采集",)),
    Stage(FINISH_ID, "安全收尾 / 结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "配置与原始帧资格校验", "独立离线分析", (), artifacts=("analysis_inputs.json",),
        quality_gates=("实验身份、原始格式、必需波形、正频率与下降沿均有效",)),
    Stage("B02", "逐帧正弦与频响模型拟合", "独立离线分析", ("B01",),
        artifacts=("frequency_evaluation.json", "frequency_response.npz"),
        quality_gates=("存在有效幅值供参考归一化；不足四个有效频率点时模型不可用，保留测量诊断供导出",)),
    Stage("B03", "频响诊断图与报告导出", "独立离线分析", ("B02",), artifacts=(
        "frequency_response.yaml", "frequency_response.json", "waveform_overview.png", "waveform_overview.pdf",
        "frequency_response.png", "frequency_response.pdf")),
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
            result.setdefault(key, [])
            result[key] = list(dict.fromkeys([*result[key], *(s.id for s in invalidated_stages(stages, stage.id))]))
    return result


def validate_selection(stages, states, selected):
    ids = [stage.id for stage in stages]
    if not selected or len(set(selected)) != len(selected) or any(item not in ids for item in selected):
        raise ValueError("请选择有效且不重复的模块")
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("安全收尾必须最后执行")
    available = {key for key, value in states.items() if value["status"] == "completed"}
    ordered = [item for item in ids if item in selected]
    for item in ordered:
        stage = stages[ids.index(item)]
        missing = set(stage.dependencies) - available
        if missing:
            raise ValueError(f"{item} {stage.title} 缺少前置模块：" + "、".join(
                f"{key} {stages[ids.index(key)].title}" for key in ids if key in missing))
        if item != "A01":
            available.difference_update(s.id for s in invalidated_stages(stages, item))
        available.add(item)
    return ordered


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return {"run_step_id": "A06", "finish_step_id": FINISH_ID,
            "acquisition": [public(stage) for stage in ACQUISITION],
        "analysis": [public(stage) for stage in ANALYSIS],
        "parameter_impacts": {"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        "diagram_url": f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram"}
