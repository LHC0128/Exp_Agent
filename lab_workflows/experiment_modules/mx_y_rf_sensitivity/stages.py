"""Mx Y RF 的唯一模块清单、编辑归属与失效规则。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import DependencyRules, Stage, initial_states


FINISH_ID = "A12"
TEMP = ("SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S")
QUALITY = ("R_BAD_POINT_STD_THRESHOLD_V", "R_POINT_MAX_ATTEMPTS")
FIT = ("FIT_R_SQUARED_MIN", "FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX")
ANALYSIS_KEYS = (*FIT, "R_BAD_POINT_STD_THRESHOLD_V", "Y_RF_NT_PER_VPP",
    "LOW_FREQ_SKIP_HZ", "LINEAR_CHECK_GAMMA_FRACTION", "SLOPE_AGREEMENT_TOLERANCE")
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
    Stage("A08", "线宽模式与可选扫频质量门槛", "采集", ("A07",),
        ("LINEWIDTH_MODE", "FREQUENCY_START_HZ", "FREQUENCY_STOP_HZ", "FREQUENCY_POINTS",
         "FREQUENCY_RF_AMPLITUDE_VPP", "FREQUENCY_DURATION_S", *TEMP, *QUALITY, *FIT),
        ("raw/frequency*.npz", "results/frequency_gate.yaml"),
        quality_gates=("扫频模式中 R 点质量、FIT_R_SQUARED_MIN 和 FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX 均通过；幅度等效线宽模式保留既有免扫频规则",)),
    Stage("A09", "带符号 Y RF 幅度响应", "采集", ("A08",),
        ("Y_RF_AMP_START_VPP", "Y_RF_AMP_STOP_VPP", "Y_RF_AMP_POINTS", "RESPONSE_DURATION_S", *TEMP, *QUALITY),
        ("raw/amplitude*.npz",), quality_gates=("R 点标准差合格；重采用尽后保存诊断并阻断后继",)),
    Stage("A10", "零 Y RF 的 R 噪声", "采集", ("A09",),
        ("NOISE_N_AVG", "NOISE_DURATION_S", "NOISE_RATE_SA_S", "NOISE_TIME_CONSTANT_S", "NOISE_DEMOD_ORDER", *TEMP),
        ("raw/noise_*.npz",)),
    Stage("A11", "响应模式恢复与本轮安全收尾", "结束", ("A10",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载配置与原始数据资格", "独立分析", (), quality_gates=("运行身份、原始格式及必需响应/噪声文件与字段符合要求，目录已封存",)),
    Stage("B02", "绝对色散、线宽、R PSD 与灵敏度", "独立分析", ("B01",), ANALYSIS_KEYS,
        ("evaluation.json", "evaluation_diagnostics.yaml")),
    Stage("B03", "结果图与两种斜率报告", "独立分析", ("B02",), artifacts=(
        "response_fit.npz", "noise_psd.npz", "sensitivity.npz", "sensitivity_zero_point.npz",
        "amplitude_response.png", "amplitude_response.pdf", "noise_psd.png", "noise_psd.pdf",
        "frequency_linewidth.png", "frequency_linewidth.pdf", "full_analysis.png", "full_analysis.pdf",
        "full_analysis_zero_point.png", "full_analysis_zero_point.pdf", "analysis.yaml", "analysis.json")),
)


_rules = DependencyRules(finish_id=FINISH_ID)
invalidated_stages = _rules.invalidated_stages
invalidate = _rules.invalidate
parameter_impacts = _rules.parameter_impacts
validate_selection = _rules.validate_selection


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return {"run_step_id": "A09", "finish_step_id": FINISH_ID,
            "acquisition": [public(stage) for stage in ACQUISITION],
        "analysis": [public(stage) for stage in ANALYSIS],
        "parameter_impacts": {"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        "diagram_url": "/api/experiments/mx-y-rf-sensitivity/workflow/diagram"}
