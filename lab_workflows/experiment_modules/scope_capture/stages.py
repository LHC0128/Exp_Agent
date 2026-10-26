"""脚本、会话、参数失效预览和 GUI 共用的执行清单。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import Stage, initial_states

from .run_format import EXPERIMENT_ID


SCOPE_PARAMETERS = (
    "sampling_rate_sa_s", "duration_s", "channel", "trigger_channel",
    "trigger_mode", "trigger_slope", "trigger_level_v",
    "vertical_scale_v_div", "vertical_offset_v",
)
ACQUISITION = (
    Stage("A01", "参数与接线预检", "准备", (),
          quality_gates=("参数校验、scope_waveform 映射与 Temp_Switch 条件检查全部通过",)),
    Stage("A02", "连接 SDS 与温度开关", "准备", ("A01",), ("disable_temperature_control",),
          quality_gates=("SDS 已连接；启用门控时参考时钟严格回读通过且 Temp_Switch 为 5 V ON",)),
    Stage("A03", "示波器采集配置与回读校验", "采集与结束", ("A02",), SCOPE_PARAMETERS,
          quality_gates=("FSRate 模式、回读采样率、实际点数 2--50,000,000 与垂直档位读回全部有效",)),
    Stage("A04", "温控门控采集与波形保存", "采集与结束", ("A03",),
          ("SYSTEM_SETTLE_S", "temperature_switch_on_settle_s", "run_tag"),
          ("raw/waveform.npz",),
          display_parameters=("SYSTEM_SETTLE_S", "temperature_switch_on_settle_s", "run_tag", "mode"),
          quality_gates=("完整帧点数与 preamble 一致且实际时长足够；恢复温控后才写盘",)),
    Stage("A05", "安全收尾 / 结束会话", "采集与结束", (),
          quality_gates=("SDS 停止并断开，温控开关恢复 5 V ON 后断开；清理错误写入运行配置",)),
)
ANALYSIS = (
    Stage("B01", "加载配置与原始波形", "离线分析", (), artifacts=("analysis_inputs.json",),
          quality_gates=("运行身份与原始格式合格；时间轴一维等长严格递增且值有限",)),
    Stage("B02", "Welch PSD 计算", "离线分析", ("B01",), artifacts=("psd.npz", "psd.json"),
          quality_gates=("实际采样率为正有限数，PSD 谱线与计算元数据完整",)),
    Stage("B03", "显示数据与诊断绘图", "离线分析", ("B02",),
          artifacts=("display.json", "waveform_psd.png", "waveform_psd.pdf"),
          quality_gates=("显示抽样按时间桶保留窄脉冲；量程与超量程诊断完整后才发布 display.json",)),
)


def invalidated_stages(stages, stage_id):
    start = next(i for i, stage in enumerate(stages) if stage.id == stage_id)
    return tuple(stage for stage in stages[start:] if stage.id != "A05")


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
    if "A05" in selected and selected[-1] != "A05":
        raise ValueError("安全收尾必须最后执行")
    ordered = [item for item in ids if item in selected]
    available = {key for key, record in states.items() if record["status"] == "completed"}
    for item in ordered:
        stage = stages[ids.index(item)]
        missing = set(stage.dependencies) - available
        if missing:
            raise ValueError(f"{item} {stage.title} 缺少前置模块：" + "、".join(
                f"{key} {stages[ids.index(key)].title}" for key in ids if key in missing))
        if item not in ("A01", "A05"):
            available.difference_update(s.id for s in invalidated_stages(stages, item))
        available.add(item)
    return ordered


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(stage.parameters
            if stage.display_parameters is None else stage.display_parameters)}
    return {"run_step_id": "A04", "finish_step_id": "A05",
            "acquisition": [public(stage) for stage in ACQUISITION],
            "analysis": [public(stage) for stage in ANALYSIS],
            "parameter_impacts": {"acquisition": parameter_impacts(ACQUISITION),
                                  "analysis": parameter_impacts(ANALYSIS)},
            "diagram_url": f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram"}
