"""新实验唯一的执行清单、分析产物、参数展示和失效规则。"""
from dataclasses import asdict

from ...workflow_stages import DependencyRules, Stage
from . import run_format
from .models import RfPhaseSensitivityParams

# 采集链：整周扫描 → 局部 K₀ → 基带频响 → 无调制噪声。A06 完成才算一整轮结束。
MEASUREMENT_IDS = ("A03", "A04", "A05", "A06")
FINISH_ID = "A07"
RUN_ID = MEASUREMENT_IDS[-1]
ANALYSIS_KEYS: tuple[str, ...] = ()
# 本轮采集的共用条件：任一项变化都要重做整条采集链。
SETUP_KEYS = (
    "Y_RF_FREQ_HZ", "Y_RF_VPP", "X_BIAS_V", "Y_BIAS_V", "MAIN_FIELD_CURRENT_MA",
    "Z_PEAK_CURRENT_MA", "Z_PULSE_WIDTH_US", "Z_PULSE_EDGE_US", "Z_TAIL_COMP_ENABLED",
    "Z_TAIL_S_F", "Z_TAIL_TAU_E_US", "AMPLIFIER_GAIN_DB", "AMPLIFIER_INPUT_IMPEDANCE_OHM",
    "AMPLIFIER_OUTPUT_IMPEDANCE_OHM", "SENSE_RESISTOR_OHM", "DAMPING_RESISTOR_OHM",
    "COIL_RESISTANCE_OHM", "RESISTOR_RATED_W", "DEMOD0_IDX", "DEMOD0_SIGNAL_RANGE",
    "DEMOD0_ORDER", "DEMOD0_TC", "DEMOD0_RATE", "STATIC_POINT_DURATION_S", "SYSTEM_SETTLE_S",
    "TEMP_SWITCH_ON_LAG_S", "SET_TEC_TARGET", "TARGET_TEMPERATURE_C",
)
LOCAL_KEYS = ("LOCAL_SPAN_DEG",)
SWEEP_KEYS = ("DEMOD0_DYNAMIC_TC", "DEMOD0_DYNAMIC_RATE", "PM_FREQUENCIES_HZ", "PM_TONE_DURATION_S")
NOISE_KEYS = ("NOISE_DURATION_S", "WELCH_SEGMENT_S")
MEASUREMENT_KEYS = tuple(SETUP_KEYS + LOCAL_KEYS + SWEEP_KEYS + NOISE_KEYS)
assert set(MEASUREMENT_KEYS) == set(RfPhaseSensitivityParams.external_names()) - {"RUN_TAG"}
ACQUISITION = (
    Stage("A01", "参数与映射预检", "准备", (), ("RUN_TAG",),
          quality_gates=("Y 向 5 kHz 载波由 MFLI Out1（MF-MOD）输出且 Y_RF_VPP 可调；核对 mfli_dev30661_signal_output_0、Demod 4 与 Z 脉冲限值。",)),
    Stage("A02", "连接设备并同步公共参考时钟", "准备", ("A01",), (),
          quality_gates=("连接设备并验证 MFLI MF-MD/MF-MOD 选件；先写入再严格回读 10 MHz 外部时钟。",)),
    Stage("A03", "整周相位响应扫描与工作点选择", "测量", ("A02",), SETUP_KEYS,
          artifacts=("raw/coarse_*.npz",),
          quality_gates=("开启 MF-MOD 连续无调制载波并整周扫描拟合最大切线；将独立 Demod 4 相位转到 sample.y（回读偏差不超过 0.5°）。",)),
    Stage("A04", "局部 K₀ 标定", "测量", ("A03",), LOCAL_KEYS,
          artifacts=("raw/local_*_xy.npz",),
          quality_gates=("在工作点两侧以 MFLI 指令角加密细扫，按 Demod 4 Y 正交分量理论式 (2.2.2) 非线性拟合计算工作点斜率 K₀ 与 X 正交泄漏（含首尾线性漂移扣除）；信噪比或正交泄漏超限记为告警但不中断采集。",)),
    Stage("A05", "基带频率响应测量", "测量", ("A04",), SWEEP_KEYS,
          artifacts=("raw/pm_*.npz",),
          quality_gates=("仅对 A05/A06 将 MFLI Demod 4 切至高带宽 DEMOD0_DYNAMIC_TC / DEMOD0_DYNAMIC_RATE（保持 A03/A04 滤波参数不变）并保持对齐相位；开启 MF-MOD 固定 2° 峰值相干边带 PM，按 PM_FREQUENCIES_HZ 单次顺序切换 Osc 3 采集各档 XY 帧。",)),
    Stage("A06", "无调制噪声测量", "测量", ("A05",), NOISE_KEYS,
          artifacts=("raw/noise_*.npz",),
          quality_gates=("保持 A05 高带宽滤波设置不变，关闭上下边带保留 5 kHz 连续载波在工作点，仅在 MFLI Demod 4 采集无调制噪声 XY 帧，严格守住温控窗口只读不写。",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (), (), quality_gates=(
        "先关 1200B，再关 MFLI Out1/MOD 并清零偏置与幅值，Z/X 关闭归零并回读，停 Demod 4 传输并恢复温控与主场；共同触发保持。",)),
)
ANALYSIS = (
    Stage("B01", "相位频响与输入等效噪声分析", "独立分析", (), (),
          artifacts=(run_format.SUMMARY, run_format.TRANSFER_TABLE, run_format.NOISE_TABLE,
                     run_format.RESPONSE_PLOT, run_format.LOCAL_PLOT,
                     run_format.TRANSFER_PLOT, run_format.NOISE_PLOT),
          quality_gates=("绘制 A03 含放大插图相图与理论式 (2.2.2) 波形、A04 左相图右理论色散响应、A05 零频 √Lorentzian 拟合与 H₀ 归一化频响及 A06 相位灵敏度谱（µrad/√Hz，含全频段 √Lorentzian 回补）；参考检查仅作告警与标注，不扣留出图。",)),
)
_rules = DependencyRules(finish_id=FINISH_ID)
invalidated_stages = _rules.invalidated_stages
invalidate = _rules.invalidate
parameter_impacts = _rules.parameter_impacts
validate_selection = _rules.validate_selection


def definition():
    def public(stage):
        shown = stage.parameters if stage.display_parameters is None else stage.display_parameters
        return asdict(stage) | {"display_parameters": list(shown)}
    return dict(run_step_id=RUN_ID, finish_step_id=FINISH_ID,
                acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
                parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
                diagram_url=f"/api/experiments/{run_format.EXPERIMENT_ID}/workflow/diagram")
