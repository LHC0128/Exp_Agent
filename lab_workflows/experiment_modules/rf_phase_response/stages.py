"""射频相位响应的唯一执行、编辑归属和失效清单。"""

from dataclasses import asdict

from lab_workflows.workflow_stages import DependencyRules, Stage

from .run_format import EXPERIMENT_ID

FINISH_ID = "A04"
RUN_ID = "A03"
ANALYSIS_KEYS: tuple[str, ...] = ()
# A02 建立链路、锁相与激励；这些参数改动会同时失效相位扫描。
CHAIN = ("AMPLIFIER_GAIN_DB", "X_BIAS_V", "Y_BIAS_V", "MAIN_FIELD_CURRENT_MA",
         "AMPLIFIER_INPUT_IMPEDANCE_OHM", "AMPLIFIER_OUTPUT_IMPEDANCE_OHM",
         "AMPLIFIER_WRITE_SETTINGS", "SENSE_RESISTOR_OHM", "DAMPING_RESISTOR_OHM", "COIL_RESISTANCE_OHM",
         "RESISTOR_RATED_W", "Y_RF_FREQ_HZ", "Y_RF_VPP", "Y_RF_PHASE_DEG", "DEMOD0_IDX",
         "DEMOD0_SIGNAL_RANGE", "DEMOD0_ORDER", "DEMOD0_TC", "DEMOD0_RATE")
# A03 改射频 burst 相位并读 R/θ；脉宽、拖尾与相位网格决定写入的波形与扫描轴。
TAIL = ("Z_TAIL_COMP_ENABLED", "Z_TAIL_S_F", "Z_TAIL_TAU_E_US")
SCAN = ("Z_PEAK_CURRENT_MA", "Z_PULSE_EDGE_US", "Z_PULSE_WIDTH_US", *TAIL,
        "Y_RF_PHASE_START_DEG", "Y_RF_PHASE_STOP_DEG", "Y_RF_PHASE_STEP_DEG",
        "N_REPEAT", "SCAN_POINT_DURATION_S", "SYSTEM_SETTLE_S", "TEMP_GROUP_SIZE",
        "TEMP_SWITCH_ON_LAG_S", "Y_RF_FREQ_HZ", "Y_RF_VPP")
ACQUISITION = (
    Stage("A01", "参数、映射与安全限值预检", "准备", (), ("RUN_TAG",), display_parameters=("RUN_TAG",),
          quality_gates=("不连接硬件；相位网格由起点/终点/步进生成且非空；脉宽大于两倍沿宽且不超过半个周期；"
                         "z_pulse_coil_current、Z/Y 场电平、触发电平与电阻功耗全部在限内"
                         "（Z_TAIL_COMP_ENABLED 时功耗按实际波形 mean(v²)）",)),
    Stage("A02", "连接 DG、MFLI、1200B、DHO900、GS200 并同步时钟", "准备", ("A01",), CHAIN,
          quality_gates=("连接前过硬件门控；写 1200B 增益/阻抗并配平 Z 通道负载；"
                         "按 X_BIAS_V 设 X 静态偏置、Y 射频正弦叠加 Y_BIAS_V 直流 offset；"
                         "MFLI 第 0 路解调器按参数配置并记录回读；射频按无限 burst 与 Y_RF_PHASE_DEG 起振；"
                         "GS200 主磁场输出回读为 OFF 才继续；DG 与 MFLI 参考时钟严格回读一致",)),
    Stage(RUN_ID, "扫射频相位并读 MFLI R/θ", "测量", ("A02",), (*CHAIN, *SCAN),
          ("raw/phase_response.csv",),
          quality_gates=("Z 双脉冲按 Z_PULSE_WIDTH_US 只写一次并 arm 无限 burst；逐相位按 DirectAW 顺序改 "
                         "射频 burst 相位后等 SYSTEM_SETTLE_S 再读 N_REPEAT 次；读数按 TEMP_GROUP_SIZE 分组，"
                         "组首关闭温控、组末恢复温控并等 TEMP_SWITCH_ON_LAG_S 后落盘，关温控窗口内不写文件；"
                         "取消保留已完成读数",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (),
          quality_gates=("先关 1200B，Z 通道 DC 0 V 并关 burst/sync、关 Y 射频、停 MFLI 解调传输与 DHO900，"
                         "再恢复温控与 GS200 原状态并断开全部设备",)),
)
ANALYSIS = (
    Stage("B01", "相位响应汇总、三分量分解与最佳读相位", "独立分析", (), (),
          artifacts=("phase_response_summary.json", "phase_response.png", "phase_response_phasor.png"),
          quality_gates=("运行目录已停止写入；逐相位汇总 R/θ，按 Z(φ)=c0+c+1e^{iφ}+c−1e^{−iφ} 最小二乘分解；"
                         "给模型残差、最佳读相位（模型 |Z| 最大处）与实测 R 最大相位；"
                         "相位响应图与相量图都用 paper 样式输出；"
                         "缺数据或相位不足 3 个时写原因、空摘要与占位图",)),
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
