"""π 脉冲宽度原子域定标的唯一执行、编辑归属和失效清单。"""

from dataclasses import asdict

from lab_workflows.workflow_stages import DependencyRules, Stage

from .run_format import EXPERIMENT_ID

FINISH_ID = "A05"
RUN_ID = "A04"
ANALYSIS_KEYS: tuple[str, ...] = ()
# A02 建立链路、锁相与激励；这些参数改动会同时失效相位粗扫与扫描。
CHAIN = ("AMPLIFIER_GAIN_DB", "AMPLIFIER_INPUT_IMPEDANCE_OHM", "AMPLIFIER_OUTPUT_IMPEDANCE_OHM",
         "AMPLIFIER_WRITE_SETTINGS", "SENSE_RESISTOR_OHM", "DAMPING_RESISTOR_OHM", "COIL_RESISTANCE_OHM",
         "RESISTOR_RATED_W", "Y_RF_FREQ_HZ", "Y_RF_VPP", "DEMOD0_IDX", "DEMOD0_SIGNAL_RANGE",
         "DEMOD0_ORDER", "DEMOD0_TC", "DEMOD0_RATE")
# A03 只改射频 burst 相位；Z 脉冲宽度沿用预测值，不动扫描网格。
PHASE = ("PHASE_CHECK_ENABLED", "Y_RF_PHASE_START_DEG", "Y_RF_PHASE_STOP_DEG", "Y_RF_PHASE_STEP_DEG",
         "Y_RF_PHASE_REPEAT")
# A04 的扫描参数；Y_RF_PHASE_DEG 是 A03 未运行时的回落相位，所以两处都归属。
SCAN = ("SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S", "TEMP_GROUP_SIZE", "SCAN_POINT_DURATION_S", "N_REPEAT",
        "Z_PEAK_CURRENT_MA", "Z_PULSE_EDGE_US", "Z_PULSE_WIDTH_START_US", "Z_PULSE_WIDTH_STOP_US",
        "Z_PULSE_WIDTH_STEP_US", "Y_RF_PHASE_DEG",
        "COIL_CONSTANT_HZ_PER_MA", "TARGET_ANGLE_PI", "Y_RF_FREQ_HZ", "Y_RF_VPP")
# 电学参考宽度只进入 B01 的比值对照，不影响采集。
REFERENCE = ("REFERENCE_PI_WIDTH_US",)
ACQUISITION = (
    Stage("A01", "参数、映射与安全限值预检", "准备", (), ("RUN_TAG",), display_parameters=("RUN_TAG",),
          quality_gates=("不连接硬件；脉宽列表由起点/终点/步进生成，逐项检查非空、不超过半个周期且不小于两倍沿宽；"
                         "射频相位粗扫的起点/终点/步进能生成非空角度列表；"
                         "z_pulse_coil_current、Z/Y 场电平与电阻功耗全部在限内",)),
    Stage("A02", "连接 DG、MFLI、1200B、DHO900、GS200 并同步时钟", "准备", ("A01",), CHAIN,
          quality_gates=("连接前过硬件门控；写 1200B 增益/阻抗并配平 Z 通道负载；MFLI 第 0 路解调器按参数配置并记录回读；"
                         "射频按无限 burst 与 Y_RF_PHASE_DEG 起振；GS200 主磁场输出回读为 OFF 才继续；"
                         "DG 与 MFLI 参考时钟严格回读一致",)),
    Stage("A03", "射频相位粗扫（可勾选）", "测量", ("A02",), (*CHAIN, *PHASE),
          ("raw/phase_scan.csv", "results/phase_vs_phase.png"),
          quality_gates=("Z 脉冲串按预测 π 宽度只写一次；每个角度按 DirectAW 顺序改射频 burst 相位"
                         "（关 burst → 写相位 → 开 burst → 开 output）后等 SYSTEM_SETTLE_S 再读 "
                         "Y_RF_PHASE_REPEAT 次；取 R 峰并用相邻三点抛物线精化；"
                         "温控关闭窗口内只读不写盘；PHASE_CHECK_ENABLED=false 时整阶段跳过且不写文件",)),
    Stage(RUN_ID, "扫脉宽并读 MFLI R/θ（可勾选）", "测量", ("A02",), (*CHAIN, *SCAN),
          ("raw/scan.csv", "raw/R_*.csv", "experiment_config.yaml"),
          quality_gates=("射频相位取 A03 的 chosen_deg（有 refined_deg 时优先），A03 未运行或关闭时回落 "
                         "Y_RF_PHASE_DEG 并在摘要记 phase_source；每个宽度上传一对 +π/−π 脉冲并由 "
                         "Time_sequence_2 外触发无限 burst；温控关闭窗口内只读不写盘，"
                         "每 TEMP_GROUP_SIZE 个读数恢复温控后再落盘；取消保留已完成点",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (),
          quality_gates=("先关 1200B，Z 通道 DC 0 V 并关 burst/sync、关 Y 射频、停 MFLI 解调传输与 DHO900，"
                         "再恢复温控与 GS200 原状态并断开全部设备",)),
)
ANALYSIS = (
    Stage("B01", "R(宽度) 拟合与 π 宽度", "独立分析", (), REFERENCE,
          artifacts=("scan_summary.json", "r_vs_width.png"),
          quality_gates=("运行目录已停止写入；至少四个宽度点才能拟合三个参数；"
                         "缺数据或点不足时写原因、空摘要与占位图；"
                         "电学参考宽度取自 REFERENCE_PI_WIDTH_US，R_max 小于单点标准误中位数 5 倍时提示 signal_low",)),
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
                parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION),
                                       analysis=parameter_impacts(ANALYSIS)),
                diagram_url=f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram")
