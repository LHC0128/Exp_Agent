"""电流频响唯一的步骤、节点编辑归属及参数失效定义。"""
from dataclasses import asdict
from ..z_coil_inductance_frequency_response.stages import (
    Stage, initial_states, invalidate, invalidated_stages, parameter_impacts, validate_selection,
)
from .run_format import EXPERIMENT_ID

FINISH_ID = "A07"
FREQUENCIES = ("FREQUENCY_START_HZ", "FREQUENCY_STOP_HZ", "FREQUENCY_POINTS_PER_DECADE",
    "FREQUENCY_POINTS", "FREQUENCY_SPACING")
SCOPE = ("SCOPE_SAMPLE_RATE_SA_S", "SCOPE_MIN_SAMPLE_RATE_SA_S", "SCOPE_SAMPLES_PER_CYCLE",
    "SCOPE_CYCLES", "RESPONSE_SETTLE_CYCLES", "SCOPE_INITIAL_SCALE", "SCOPE_MEASURED_CHANNEL",
    "SCOPE_TRIGGER_CHANNEL", "SCOPE_SCALE_MIN", "SCOPE_SCALE_MAX", "SCOPE_VERTICAL_DIVISIONS",
    "SCOPE_AUTO_RANGE_LOW_FRACTION", "SCOPE_AUTO_RANGE_HIGH_FRACTION", "SCOPE_AUTO_RANGE_MAX_ATTEMPTS",
    "SCOPE_TRIGGER_LEVEL_V", "SCOPE_REFERENCE_CHANNEL", "SCOPE_REFERENCE_SCALE",
    "SCOPE_CENTERED_CAPTURE", "CAPTURE_MARGIN_CYCLES")
CURRENT = ("SENSE_RESISTOR_OHM", "SENSE_RESISTOR_TOLERANCE_PERCENT", "SENSE_RESISTOR_POWER_RATING_W",
    "SENSE_RESISTOR_POWER_DERATING", "MAXIMUM_CURRENT_A")
ACQUISITION = (
    Stage("A01", "参数与低端接线预检", "启动准备", (),
        quality_gates=("参数、低端采样接线、采样电阻与安全限值全部通过预检",)),
    Stage("A02", "设备连接", "启动准备", ("A01",)),
    Stage("A03", "参考时钟同步与回读", "启动准备", ("A02",), quality_gates=("参考时钟严格回读一致",)),
    Stage("A04", "相干正弦与共同触发配置", "激励与采集", ("A03",),
        ("DRIVE_AMPLITUDE_VPP", "DRIVE_OFFSET_V", "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP",
         "TRIGGER_OFFSET_V", "TRIGGER_DUTY_PERCENT", "COHERENT_REARM", *FREQUENCIES),
        display_parameters=("DRIVE_AMPLITUDE_VPP", "DRIVE_OFFSET_V", "TRIGGER_FREQUENCY_HZ",
         "TRIGGER_AMPLITUDE_VPP", "TRIGGER_OFFSET_V", "TRIGGER_DUTY_PERCENT", *FREQUENCIES[:4])),
    Stage("A05", "动态 Scope 首频配置与回读", "激励与采集", ("A04",), SCOPE,
        display_parameters=("SCOPE_SAMPLE_RATE_SA_S", "SCOPE_MIN_SAMPLE_RATE_SA_S", "SCOPE_SAMPLES_PER_CYCLE",
            "SCOPE_CYCLES", "RESPONSE_SETTLE_CYCLES", "SCOPE_INITIAL_SCALE", "SCOPE_REFERENCE_CHANNEL", "SCOPE_REFERENCE_SCALE")),
    Stage("A06", "对数扫频、电流校验与重复帧保存", "激励与采集", ("A05",),
        ("RUN_TAG", "FREQUENCY_SETTLE_S", "SCOPE_REPEATS", "SCOPE_CAPTURE_GUARD_S", "SCOPE_CYCLES",
         "RESPONSE_SETTLE_CYCLES", *CURRENT),
        ("experiment_config.yaml", "raw/scope_f*_r*.npz", "raw/frequency_index.json", "raw/frequency_index.npz"),
        quality_gates=("校验完整 Scope 帧、电流峰值、采样电阻功率和波形饱和",)),
    Stage(FINISH_ID, "安全收尾 / 结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "配置、原始帧与触发资格", "独立离线分析", (), artifacts=("analysis_inputs.json",),
        quality_gates=("运行身份、原始协议、采样电阻与触发波形有效；历史格式按已验证规则加载",)),
    Stage("B02", "电流相量、参考与可靠频带", "独立离线分析", ("B01",),
        artifacts=("frequency_evaluation.json", "frequency_response.npz"),
        quality_gates=("至少一个幅值可靠点才可导出；无闭环相位资格仍保留诊断",
            "闭环相位合格须同帧实测参考、至少两次有效重复且圆周相位标准差 < 15°",)),
    Stage("B03", "电流频响诊断图与报告", "独立离线分析", ("B02",), artifacts=(
        "frequency_response.yaml", "frequency_response.json", "transfer_function.png", "transfer_function.pdf")),
)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return {"acquisition": [public(stage) for stage in ACQUISITION],
        "analysis": [public(stage) for stage in ANALYSIS],
        "parameter_impacts": {"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        "diagram_url": f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram"}
