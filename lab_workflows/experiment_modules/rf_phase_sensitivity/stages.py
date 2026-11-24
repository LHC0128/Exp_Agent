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
SWEEP_KEYS = ("PM_FREQUENCIES_HZ", "PM_TONE_DURATION_S")
NOISE_KEYS = ("NOISE_DURATION_S", "WELCH_SEGMENT_S")
MEASUREMENT_KEYS = tuple(SETUP_KEYS + LOCAL_KEYS + SWEEP_KEYS + NOISE_KEYS)
assert set(MEASUREMENT_KEYS) == set(RfPhaseSensitivityParams.external_names()) - {"RUN_TAG"}
ACQUISITION = (
    Stage("A01", "参数与映射预检", "准备", (), ("RUN_TAG",),
          quality_gates=("固定 Y 向 5 kHz / 50 mVpp；核对驱动能力、映射、电平、电流与实际脉冲功耗。",)),
    Stage("A02", "连接设备并同步公共参考时钟", "准备", ("A01",), (),
          quality_gates=("所有参与参考时钟的设备写后回读；DHO924 验证接线所需通道和面板状态。",)),
    Stage("A03", "整周相位响应扫描与工作点选择", "测量", ("A02",), SETUP_KEYS,
          artifacts=("raw/coarse_*.npz",),
          quality_gates=("整周扫描拟合最大切线；MFLI 解调相位转回读偏差不超过 0.5°。",)),
    Stage("A04", "局部 K₀ 标定", "测量", ("A03",), LOCAL_KEYS,
          artifacts=("raw/local_*_scope.npz", "raw/local_*_xy.npz"),
          quality_gates=("按 CH4 相对 CH3 实测角度、Y 正交分量和对称内外两档一致拟合静态斜率；载波监视幅度变化不超过 5%。",)),
    Stage("A05", "基带频率响应测量", "测量", ("A04",), SWEEP_KEYS,
          artifacts=("raw/pm_*.npz",),
          quality_gates=("固定 1° 原生 PM 后才回调载波中心；扫频只改基带频率，每点核对载波中心、深度与残余 AM。",)),
    Stage("A06", "无调制噪声测量", "测量", ("A05",), NOISE_KEYS,
          artifacts=("raw/noise_*.npz",),
          quality_gates=("无调制但保留载波；前后复核物理相位、载波幅值和同一锁相配置，关闭温控窗口只读不写。",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (), (), quality_gates=(
        "先关 1200B，再关 Y/X/Z 输出并归零、恢复温控、主场与示波器；共同触发保持。",)),
)
ANALYSIS = (
    Stage("B01", "相位频响与输入等效噪声分析", "独立分析", (), (),
          artifacts=(run_format.SUMMARY, run_format.TRANSFER_TABLE, run_format.NOISE_TABLE,
                     run_format.TRANSFER_PLOT, run_format.NOISE_PLOT),
          quality_gates=("核对同轮低频标度和锚点复测；仅在相邻合格频点覆盖的连续频段内折算 Welch 噪声。"
                         "质量不足时只导出可用诊断，不填补被拒频点或外推。",)),
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
