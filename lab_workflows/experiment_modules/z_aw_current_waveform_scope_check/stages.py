"""实际电流验证的节点、参数编辑归属与失效范围。"""
from dataclasses import asdict

from ..z_aw_waveform_scope_check.stages import (
    Stage, SCOPE_PARAMETERS, initial_states, invalidate, invalidated_stages,
    parameter_impacts, validate_selection,
)
from .run_format import EXPERIMENT_ID

CONTROL_PARAMETERS = (
    "CONTROL_WAVEFORM_SOURCE", "CORRECTED_CONTROL_SOURCE_RUN", "CONTROL_VERSION",
    "CONTROL_RESULTS_ROOT", "COMMAND_Z_CALIBRATION_SOURCE_RUN", "Z_CALIBRATION_SOURCE_RUN",
    "CURRENT_COUPLING_CALIBRATION_SOURCE_RUN", "SENSE_RESISTOR_OHM",
    "SENSE_RESISTOR_POWER_RATING_W", "MAXIMUM_CURRENT_A", "CONTROL_SCALE",
    "Z_AW_OUTPUT_VPP", "Z_AW_OUTPUT_OFFSET", "CONTROL_BURST_PHASE_DEG",
    "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP", "TRIGGER_OFFSET_V", "TRIGGER_DUTY_PERCENT",
)
ACQUISITION = (
    Stage("A01", "参数与接线预检", "准备", (),
          quality_gates=("采样电阻、功率、电流上限、来源哈希及映射全部通过",)),
    Stage("A02", "连接设备与同步时钟", "准备", ("A01",),
          quality_gates=("已连接设备参考时钟严格回读一致",)),
    Stage("A03", "电流目标与任意波触发配置", "波形与 Scope", ("A02",), CONTROL_PARAMETERS,
          display_parameters=tuple(key for key in CONTROL_PARAMETERS if key != "Z_CALIBRATION_SOURCE_RUN")),
    Stage("A04", "Scope 配置与触发就绪", "波形与 Scope", ("A03",), SCOPE_PARAMETERS,
          display_parameters=("SCOPE_CYCLES",)),
    Stage("A05", "重复采集与原始帧保存", "采集与结束", ("A04",), ("SCOPE_REPEATS", "RUN_TAG"),
          ("experiment_config.yaml", "raw/applied_control_waveform.npz", "raw/scope_capture_*.npz")),
    Stage("A06", "安全收尾 / 结束会话", "采集与结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载配置与电流原始帧", "离线分析", (), artifacts=("analysis_inputs.json",),
          quality_gates=("身份、格式、电阻、目标电流、时间轴及 CH4 下降沿有效",)),
    Stage("B02", "电流对齐与相似度评估", "离线分析", ("B01",),
          artifacts=("current_waveforms.npz", "current_metrics.json"),
          quality_gates=("波形非恒定且存在有效周期；记录 CH3 饱和诊断",)),
    Stage("B03", "诊断绘图与报告导出", "离线分析", ("B02",),
          artifacts=("current_waveform_similarity.png", "current_waveform_similarity.pdf",
                     "current_similarity_report.yaml", "current_similarity_report.json")),
)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(stage.parameters
            if stage.display_parameters is None else stage.display_parameters)}
    return dict(acquisition=[public(stage) for stage in ACQUISITION],
        analysis=[public(stage) for stage in ANALYSIS],
        parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis={}),
        diagram_url=f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram")
