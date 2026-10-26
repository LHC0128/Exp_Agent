"""脚本、会话、参数失效预览和 GUI 共用的执行清单。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import Stage, initial_states

from .run_format import EXPERIMENT_ID


CONTROL_PARAMETERS = ("CONTROL_VERSION", "CONTROL_RESULTS_ROOT", "Z_CALIBRATION_SOURCE_RUN",
    "CONTROL_SCALE", "Z_AW_OUTPUT_VPP", "Z_AW_OUTPUT_OFFSET", "CONTROL_BURST_PHASE_DEG",
    "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP", "TRIGGER_OFFSET_V", "TRIGGER_DUTY_PERCENT")
SCOPE_PARAMETERS = ("SCOPE_CYCLES", "SCOPE_MEASURED_CHANNEL", "SCOPE_TRIGGER_CHANNEL",
    "SCOPE_INITIAL_SCALE", "SCOPE_SCALE_MIN", "SCOPE_SCALE_MAX", "SCOPE_VERTICAL_DIVISIONS",
    "SCOPE_AUTO_RANGE_LOW_FRACTION", "SCOPE_AUTO_RANGE_HIGH_FRACTION",
    "SCOPE_AUTO_RANGE_MAX_ATTEMPTS", "SCOPE_TRIGGER_LEVEL_V")
ACQUISITION = (
    Stage("A01", "参数与接线预检", "准备", (), quality_gates=("参数、控制源资格、映射与安全限值全部通过",)),
    Stage("A02", "连接设备与同步时钟", "准备", ("A01",), quality_gates=("已连接设备参考时钟严格回读一致",)),
    Stage("A03", "控制源与任意波触发配置", "波形与 Scope", ("A02",), CONTROL_PARAMETERS),
    Stage("A04", "Scope 配置与触发就绪", "波形与 Scope", ("A03",), SCOPE_PARAMETERS,
          display_parameters=("SCOPE_CYCLES",)),
    Stage("A05", "重复采集与原始帧保存", "采集与结束", ("A04",), ("SCOPE_REPEATS", "RUN_TAG"),
          ("experiment_config.yaml", "raw/applied_control_waveform.npz", "raw/scope_capture_*.npz")),
    Stage("A06", "安全收尾 / 结束会话", "采集与结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载配置与原始帧", "离线分析", (), artifacts=("analysis_inputs.json",),
          quality_gates=("理论与实测波形字段完整、时间轴严格递增、值有限且存在有效触发下降沿",)),
    Stage("B02", "触发对齐与波形相似度", "离线分析", ("B01",),
          artifacts=("aligned_waveforms.npz", "similarity_diagnostics.npz", "similarity_metrics.json"),
          quality_gates=("存在共同有效时间窗且波形标准差非零；记录相似度与饱和诊断",)),
    Stage("B03", "诊断绘图与报告导出", "离线分析", ("B02",),
          artifacts=("waveform_similarity.png", "waveform_similarity.pdf", "similarity_report.yaml",
                     "similarity_report.json")),
)


def invalidated_stages(stages, stage_id):
    start = next(i for i, stage in enumerate(stages) if stage.id == stage_id)
    return tuple(stage for stage in stages[start:] if stage.id != "A06")


def invalidate(stages, states, stage_id):
    for stage in invalidated_stages(stages, stage_id):
        states[stage.id] = {"status": "pending" if states[stage.id]["status"] == "pending" else "stale",
                            "error": ""}


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
    if "A06" in selected and selected[-1] != "A06":
        raise ValueError("安全收尾必须最后执行")
    ordered = [item for item in ids if item in selected]
    available = {key for key, record in states.items() if record["status"] == "completed"}
    for item in ordered:
        stage = stages[ids.index(item)]
        missing = set(stage.dependencies) - available
        if missing:
            raise ValueError(f"{item} {stage.title} 缺少前置模块：" + "、".join(
                f"{key} {stages[ids.index(key)].title}" for key in ids if key in missing))
        if item not in ("A01", "A06"):
            available.difference_update(s.id for s in invalidated_stages(stages, item))
        available.add(item)
    return ordered


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(stage.parameters
            if stage.display_parameters is None else stage.display_parameters)}
    return {"run_step_id": "A05", "finish_step_id": "A06",
            "acquisition": [public(stage) for stage in ACQUISITION],
            "analysis": [public(stage) for stage in ANALYSIS],
            "parameter_impacts": {"acquisition": parameter_impacts(ACQUISITION),
                                  "analysis": parameter_impacts(ANALYSIS)},
            "diagram_url": f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram"}
