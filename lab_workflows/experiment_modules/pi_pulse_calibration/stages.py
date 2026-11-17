"""π 脉冲面积标定的唯一执行、编辑归属和失效清单。"""

from dataclasses import asdict

from lab_workflows.workflow_stages import DependencyRules, Stage

from .run_format import EXPERIMENT_ID

FINISH_ID = "A04"
RUN_ID = "A03"
ANALYSIS_KEYS: tuple[str, ...] = ()
CHAIN = ("AMPLIFIER_GAIN_DB", "AMPLIFIER_INPUT_IMPEDANCE_OHM", "AMPLIFIER_OUTPUT_IMPEDANCE_OHM",
         "AMPLIFIER_WRITE_SETTINGS", "SENSE_RESISTOR_OHM", "DAMPING_RESISTOR_OHM", "COIL_RESISTANCE_OHM",
         "RESISTOR_RATED_W", "SETTLE_S", "SCOPE_ACQUIRE_DELAY_S", "SCOPE_CURRENT_CHANNEL",
         "SCOPE_SYNC_CHANNEL", "SYNC_TRIGGER_LEVEL_V", "SYNC_SCALE_V_DIV", "MAX_MEMORY_DEPTH")
SCAN = ("PULSE_PERIOD_US", "PULSE_WIDTHS_US", "PULSE_EDGE_US", "PULSE_PEAK_CURRENT_MA", "PULSE_BASELINE_V",
        "SHOTS_PER_WIDTH", "CAPTURE_INTERVAL_S", "CAPTURE_PERIODS", "PULSE_TAIL_WINDOW_US",
        "COIL_CONSTANT_HZ_PER_MA", "TARGET_ANGLE_PI")
ACQUISITION = (
    Stage("A01", "参数、映射与安全限值预检", "准备", (), ("RUN_TAG",), display_parameters=("RUN_TAG",),
          quality_gates=("不连接硬件；z_pulse_coil_current 与 Z_magnetic_field 必须有有限上下界，"
                         "脉冲峰值、偏置电平与电阻功耗全部在限内",)),
    Stage("A02", "连接信号源、1200B、DHO900 并同步时钟", "准备", ("A01",), CHAIN,
          quality_gates=("连接前过硬件门控；按参数写入 1200B 增益与输入/输出阻抗，并把信号源输出负载配成一致；"
                         "Z 信号源参考时钟严格回读，失败不开启任何输出",)),
    Stage(RUN_ID, "扫宽度并逐发记录 θ（可勾选）", "测量", ("A02",), (*CHAIN, *SCAN),
          ("raw/scan.csv", "raw/scan_*.npz"),
          quality_gates=("固定峰值、逐宽度打 ±π 双脉冲；每帧存原始波形并逐发积分；"
                         "实测电流越限或同步缺失即中止",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (),
          quality_gates=("先关闭 1200B 输出，再将 Z 通道 DC 0 V、关闭同步与输出，停止 DHO900 并断开三台设备",)),
)
ANALYSIS = (
    Stage("B01", "θ(宽度) 线性内插与 π 宽度", "独立分析", (), artifacts=(
        "scan_summary.json", "scan_fit.png"),
        quality_gates=("运行目录已停止写入；至少两个宽度点才能内插；缺数据时写空摘要、原因与占位图",)),
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
