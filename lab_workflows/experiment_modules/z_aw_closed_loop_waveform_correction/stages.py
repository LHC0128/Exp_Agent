"""闭环实际步骤、参数编辑位置与失效范围。"""
from dataclasses import asdict
from ..z_aw_waveform_scope_check.stages import (
    Stage, initial_states, invalidated_stages, invalidate, parameter_impacts, validate_selection,
)
from .run_format import EXPERIMENT_ID

SOURCE = ("CONTROL_SOURCE_SET", "CONTROL_VERSION", "CONTROL_SCALE", "CORRECTION_METHOD",
    "CURRENT_COUPLING_CALIBRATION_SOURCE_RUN", "CURRENT_FREQUENCY_RESPONSE_SOURCE_RUN",
    "Z_AW_OUTPUT_VPP", "ERROR_CUTOFF_HZ", "INVERSE_REGULARIZATION", "HARMONIC_AMPLITUDE_FRACTION",
    "MAXIMUM_HARMONICS", "MAXIMUM_CURRENT_A", "SENSE_RESISTOR_POWER_RATING_W", "SENSE_RESISTOR_POWER_DERATING")
SCOPE = ("SCOPE_CYCLES", "SCOPE_HEADROOM_FACTOR", "SCOPE_MEASURED_CHANNEL", "SCOPE_TRIGGER_CHANNEL",
    "SCOPE_VERTICAL_DIVISIONS", "SCOPE_TRIGGER_LEVEL_V")
LOOP = ("RUN_TAG", "CONTROL_BURST_PHASE_DEG", "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP",
    "TRIGGER_OFFSET_V", "TRIGGER_DUTY_PERCENT", "MAX_ITERATIONS", "ITERATION_REPEATS",
    "ITERATION_DAMPING", "TARGET_RELATIVE_RMS", "REQUIRED_PASSES", "OUTPUT_SETTLE_S",
    "HOLDOUT_REPEATS", "MAXIMUM_STEP_V", "MAXIMUM_BACKTRACKS", "IDENTIFICATION_STEP_V",
    "IDENTIFICATION_MAXIMUM_STEP_V", "IDENTIFICATION_REPEATS", "IDENTIFICATION_MIN_SNR",
    "HARMONIC_REPEATABILITY_FRACTION", "MINIMUM_IMPROVEMENT",
    "REIDENTIFY_INTERVAL", "FINAL_VALIDATION_REPEATS")
ACQUISITION = (
    Stage("A01", "参数、来源与接线预检", "准备", (), quality_gates=("参数、来源资格、映射和安全限值全部通过",)),
    Stage("A02", "连接设备与同步时钟", "准备", ("A01",), quality_gates=("参考时钟严格回读一致",)),
    Stage("A03", "闭环目标与来源准备", "准备", ("A02",), SOURCE,
          quality_gates=("静态标定与理论有效；频响模式严格核对有效重复与相位；谐波模式拒绝直流目标",)),
    Stage("A04", "Scope 量程与触发配置", "闭环采集", ("A03",), SCOPE,
          display_parameters=("SCOPE_CYCLES", "SCOPE_HEADROOM_FACTOR")),
    Stage("A05", "闭环迭代与独立验证", "闭环采集", ("A04",), LOOP,
          ("experiment_config.yaml", "raw/iteration_summary.yaml", "results/iteration_summary.yaml"),
          display_parameters=LOOP + ("SCOPE_HEADROOM_FACTOR",),
          quality_gates=("保留全部尝试；谐波辨识、重复性或最终重载不合格时禁止冻结；未收敛仍保存诊断",)),
    Stage("A06", "安全收尾 / 结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "核验运行版本与保存的反馈", "离线分析", (), artifacts=("analysis_inputs.json",),
          quality_gates=("明确版本和方法；保存数组、时间轴与原始反馈有效",)),
    Stage("B02", "原算法绘图与报告导出", "离线分析", ("B01",),
          artifacts=("final_waveform_report.yaml",),
          quality_gates=("使用保存的指标与命令；质量结论独立于分析是否完成",)),
)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(stage.parameters
            if stage.display_parameters is None else stage.display_parameters)}
    return {"acquisition": [public(stage) for stage in ACQUISITION],
        "analysis": [public(stage) for stage in ANALYSIS],
        "parameter_impacts": {"acquisition": parameter_impacts(ACQUISITION), "analysis": {}},
        "diagram_url": f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram"}
