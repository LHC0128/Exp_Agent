"""Z 链路零点与脉冲角稳定性的唯一执行、编辑归属和失效清单。"""
from dataclasses import asdict

from lab_workflows.workflow_stages import DependencyRules, Stage

from .run_format import EXPERIMENT_ID

FINISH_ID = "A06"
RUN_ID = "A05"
ANALYSIS_KEYS: tuple[str, ...] = ()
CHAIN = ("AMPLIFIER_GAIN_DB", "AMPLIFIER_INPUT_IMPEDANCE_OHM", "AMPLIFIER_OUTPUT_IMPEDANCE_OHM",
         "AMPLIFIER_WRITE_SETTINGS", "SENSE_RESISTOR_OHM", "DAMPING_RESISTOR_OHM", "COIL_RESISTANCE_OHM",
         "RESISTOR_RATED_W", "SETTLE_S", "SCOPE_ACQUIRE_DELAY_S", "SCOPE_CURRENT_CHANNEL",
         "SCOPE_SYNC_CHANNEL", "SYNC_TRIGGER_LEVEL_V", "SYNC_SCALE_V_DIV", "MAX_MEMORY_DEPTH")
ZERO = ("ZERO_WINDOW_S", "ZERO_FRAMES", "ZERO_LOG_INTERVAL_S", "ZERO_LOG_DURATION_S",
        "ZERO_SAVE_ALL_FRAMES", "ZERO_RAW_SAVE_INTERVAL_S")
BASELINE = ("BASELINE_CURRENT_MA", "CONVERSION_AMPS_PER_VOLT", "CONVERSION_OFFSET_MA")
PULSE = ("PULSE_PERIOD_US", "PULSE_WIDTH_US", "PULSE_EDGE_US", "PULSE_PEAK_CURRENT_MA",
         "PULSE_BASELINE_V", "PULSE_CAPTURE_INTERVAL_S", "PULSE_CAPTURE_PERIODS", "PULSE_DURATION_S",
         "PULSE_RAW_SAVE_INTERVAL_S", "PULSE_TAIL_WINDOW_US", "COIL_CONSTANT_HZ_PER_MA")
ACQUISITION = (
    Stage("A01", "参数、映射与安全限值预检", "准备", (), ("RUN_TAG",), display_parameters=("RUN_TAG",),
          quality_gates=("不连接硬件；z_pulse_coil_current 与 Z_magnetic_field 必须有有限上下界，"
                         "计划电流、基线电压、脉冲峰谷与电阻功耗全部在限内",)),
    Stage("A02", "连接信号源、1200B、DHO900 并同步时钟", "准备", ("A01",), CHAIN,
          quality_gates=("连接前过硬件门控；按参数写入 1200B 增益与输入/输出阻抗，并把信号源输出负载配成一致；"
                         "Z 信号源参考时钟严格回读，失败不开启任何输出",)),
    Stage("A03", "DG 0 V 零点（可勾选）", "测量", ("A02",), (*CHAIN, *ZERO),
          ("raw/zero_dg0.csv", "raw/zero_dg0_*.npz", "raw/zero_off_*.npz", "raw/zero_on_*.npz"),
          quality_gates=("功放关/开两态、DG 0 V 测零偏；长记逐点追加 CSV，默认每帧原始波形另存 NPZ；"
                         "不写任何 I–V 电压",)),
    Stage("A04", "目标电流与手动换算基线（可勾选）", "测量", ("A02",), (*CHAIN, *ZERO, *BASELINE),
          ("raw/zero_baseline.csv", "raw/zero_baseline_*.npz", "raw/baseline_setup.npz"),
          quality_gates=("目标电流经手动 I–V 换算（k 默认取线圈常数标定值）写电压；写入前校验电压与目标电流；"
                         "设置后测实际电流再开始长记；不做 I–V 拟合",)),
    Stage(RUN_ID, "±π 双脉冲角分布（可勾选）", "测量", ("A02",), (*CHAIN, *PULSE),
          ("raw/pulse_angles.csv", "raw/pulse_*.npz"),
          quality_gates=("100 µs 周期、两个 4 µs 反号脉冲连续输出；同步沿触发、触发居中；"
                         "按间隔捕获短窗并逐脉冲积分，θ = 2π·K·∫I dt",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (),
          quality_gates=("先关闭 1200B 输出，再将 Z 通道 DC 0 V、关闭同步与输出，停止 DHO900 并断开三台设备",)),
)
ANALYSIS = (
    Stage("B01", "零点漂移、Allan、窗口内噪声与电流/场换算", "独立分析", (), artifacts=(
        "zero_summary.json", "zero_timeseries.png", "zero_allan.png",
        "zero_window_noise.png", "zero_window_psd.png"),
        quality_gates=("运行目录已停止写入；缺少某一段零点数据时该段留空并写占位图，不阻塞另一段；"
                       "没有逐帧 NPZ 时窗口内噪声留空",)),
    Stage("B02", "±π 脉冲角分布与不对称", "独立分析", ("B01",), artifacts=(
        "pulse_summary.json", "pulse_timeseries.png", "pulse_hist.png"),
        quality_gates=("缺少脉冲 CSV 时写空摘要、原因与占位图；不把分析完成当作科学质量通过",)),
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
