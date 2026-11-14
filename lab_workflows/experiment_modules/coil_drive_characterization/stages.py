"""线圈驱动特性的唯一执行、编辑归属和失效清单。"""
from dataclasses import asdict

from lab_workflows.workflow_stages import DependencyRules, Stage

from .run_format import EXPERIMENT_ID

FINISH_ID = "A06"
RUN_ID = "A05"
ANALYSIS_KEYS = ("AREA_TOLERANCE",)
# 每帧采集共用：改动后从 A03 起全部失效。
CHAIN = ("AMPLIFIER_GAIN_DB", "AMPLIFIER_INPUT_IMPEDANCE_OHM", "AMPLIFIER_OUTPUT_IMPEDANCE_OHM",
         "AMPLIFIER_WRITE_SETTINGS",
         "SENSE_RESISTOR_OHM", "DAMPING_RESISTOR_OHM", "COIL_RESISTANCE_OHM", "RESISTOR_RATED_W",
         "SETTLE_S", "SCOPE_ACQUIRE_DELAY_S", "SCOPE_CURRENT_CHANNEL", "SCOPE_SYNC_CHANNEL", "SYNC_TRIGGER_LEVEL_V",
         "SYNC_SCALE_V_DIV", "MAX_MEMORY_DEPTH")
SINE = ("SINE_PEAK_CURRENT_MA", "FREQUENCY_START_HZ", "FREQUENCY_STOP_HZ", "POINTS_PER_DECADE", "SINE_CYCLES")
PULSE = ("PULSE_CURRENT_MA", "PULSE_WIDTHS_US", "PULSE_REPETITION_HZ", "PULSE_WINDOW_MULTIPLE", "PULSE_MIN_WINDOW_US")
SHARED = ("SETTLE_S", "SCOPE_ACQUIRE_DELAY_S", "MAX_MEMORY_DEPTH")
ACQUISITION = (
    Stage("A01", "参数、映射与安全限值预检", "准备", (), ("RUN_TAG",), display_parameters=("RUN_TAG",),
          quality_gates=("不连接硬件；z_pulse_coil_current 必须有有限上下界，计划电流、信号源电平与电阻功耗全部在限内",)),
    Stage("A02", "连接信号源、1200B、DHO900 并同步时钟", "准备", ("A01",),
          quality_gates=("连接前过硬件门控；按参数写入 1200B 增益与输入/输出阻抗，并把信号源输出负载配成与输入阻抗一致；"
                         "Z 信号源参考时钟严格回读，失败不开启任何输出",)),
    Stage("A03", "功放零偏与增益核对", "基线", ("A02",),
          (*CHAIN, "ZERO_FRAMES", "ZERO_WINDOW_S", "GAIN_CHECK_CURRENT_MA", "GAIN_TOLERANCE", "GAIN_LOWER_RATIO"),
          ("raw/zero_off_*.npz", "raw/zero_on_*.npz", "raw/gain_check.npz"),
          quality_gates=("功放关/开两态、信号源 0 V 测零偏；小直流实测 I/V 低于预测 GAIN_LOWER_RATIO 倍或超过 GAIN_TOLERANCE 倍即中止",
                         "每帧实测电流越过 z_pulse_coil_current 即中止并安全收尾",)),
    Stage("A04", "正弦扫频传递函数", "测量", ("A03",), SINE, ("raw/sine_*.npz",),
          display_parameters=(*SINE, *SHARED),
          quality_gates=("同步上升沿触发；削顶自动放大量程，最大量程仍削顶即中止",)),
    Stage(RUN_ID, "矩形脉冲阶跃响应", "测量", ("A03",), PULSE, ("raw/pulse_*.npz",),
          display_parameters=(*PULSE, *SHARED),
          quality_gates=("0→I 单极性脉冲，触发居中，记录窗 = max(倍数 × 脉宽, 下限)、触发后可用 ≥2.5 倍脉宽；"
                       "最宽脉冲占空比 <50%",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (),
          quality_gates=("先关闭 1200B 输出，再将 Z 通道 DC 0 V、关闭同步与输出，停止 DHO900 并断开三台设备",)),
)
ANALYSIS = (
    Stage("B01", "零偏、增益与正弦传递函数", "独立分析", (), artifacts=("transfer.json",),
          quality_gates=("运行目录已停止写入；必需零偏帧齐全；扫频帧可缺省（未运行 A04 时传递函数为空）",)),
    Stage("B02", "脉冲指标与最短可用脉宽", "独立分析", ("B01",), ANALYSIS_KEYS, ("summary.json", "coil_drive_pulse.png"),
          quality_gates=("面积偏差 ≤ AREA_TOLERANCE 且峰值达到平台 1−AREA_TOLERANCE 的最短实测脉宽；不外推",)),
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
                diagram_url=f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram")
