"""射频相位响应的唯一执行清单、参数展示、产物与失效规则。"""

from dataclasses import asdict

from lab_workflows.workflow_stages import DependencyRules, Stage

from .run_format import EXPERIMENT_ID

FINISH_ID = "A04"
RUN_ID = "A03"
ANALYSIS_KEYS: tuple[str, ...] = ()
# A02 建立 MFLI MF-MOD 载波、Z/X/共同触发链路、Demod 4 读出与主磁场；改任一项都要重做整周扫描。
CHAIN = ("Y_RF_FREQ_HZ", "Y_RF_VPP", "Y_BIAS_V", "X_BIAS_V", "MAIN_FIELD_CURRENT_MA",
         "Z_DRIVE_SOURCE_IMPEDANCE_OHM", "SENSE_RESISTOR_OHM", "DAMPING_RESISTOR_OHM",
         "COIL_RESISTANCE_OHM", "RESISTOR_RATED_W", "DEMOD0_IDX", "DEMOD0_SIGNAL_RANGE",
         "DEMOD0_ORDER", "DEMOD0_TC", "DEMOD0_RATE", "SET_TEC_TARGET", "TARGET_TEMPERATURE_C")
# A03 改载波相位并读 Demod 4；脉宽、拖尾、相位网格与逐点温控门控决定写入波形与扫描轴。
TAIL = ("Z_TAIL_COMP_ENABLED", "Z_TAIL_S_F", "Z_TAIL_TAU_E_US")
SCAN = ("Z_PULSE_VPP", "Z_PULSE_EDGE_US", "Z_PULSE_WIDTH_US", *TAIL,
        "Y_RF_PHASE_START_DEG", "Y_RF_PHASE_STOP_DEG", "Y_RF_PHASE_STEP_DEG",
        "SCAN_POINT_DURATION_S", "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S")
ACQUISITION = (
    Stage("A01", "参数、映射与安全限值预检", "准备", (), ("RUN_TAG",),
          display_parameters=("RUN_TAG",),
          quality_gates=("不连接硬件；必需映射只含 Z_magnetic_field、X_magnetic_field、Time_sequence_2、"
                         "Temp_Switch、lockin_r、main_magnetic_field 与 temperature；5 kHz 载波、Demod 4 读出、"
                         "相位网格非空、脉宽大于两倍沿宽且不超过半个周期；MFLI Signal Output 1 的偏置与调相包络、"
                         "Z_PULSE_VPP、按回路电阻估算的峰值电流、X/触发电平与电阻平均功耗全部在限内",)),
    Stage("A02", "连接设备、同步 10 MHz 并准备 MF-MOD 载波", "准备", ("A01",), CHAIN,
          quality_gates=("连接 DG 的 Z/X/trigger/temp、MFLI、GS200 与可选 TEC；不连 Y 通道、1200B 与示波器；"
                         "DG 直驱 Z 线圈（高阻档）并按 X_BIAS_V 设静态偏置；共同触发按 5 kHz 方波回读；"
                         "MFLI Demod 4 按参数配置、参考 Osc 2，Signal Output 1 输出无调制载波并逐节点回读；"
                         "主磁场按参数快照后设置并回读；DG 与 MFLI 参考时钟严格回读一致",)),
    Stage(RUN_ID, "整周扫描射频相位并读 Demod 4", "测量", ("A02",), (*CHAIN, *SCAN),
          ("raw/coarse_*.npz",),
          quality_gates=("Z 双脉冲按 Z_PULSE_WIDTH_US 上传一次并 arm 无限 burst（DG 直驱，无 1200B 动作）；"
                         "共享 A03 扫描逐点把 MFLI 载波相位写到网格点，每点在关温控窗口内采集 "
                         "SCAN_POINT_DURATION_S 的同步 X/Y，恢复温控并回读、等待 TEMP_SWITCH_ON_LAG_S 后落盘；"
                         "扫描结束后按实测切线把 Demod 4 相位转到工作点并回读；取消保留已完成帧",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (),
          quality_gates=("先关 MFLI Out1，再清 MF-MOD 载波幅值/边带/输出并归零偏置，逐项回读；"
                         "Z/X 关闭归零并回读；停 Demod 4 传输；恢复温控与 GS200 原状态；"
                         "无 1200B/示波器动作，Time_sequence_2 保持运行",)),
)
ANALYSIS = (
    Stage("B01", "相位响应 (2.2.2) 拟合与相量图", "独立分析", (), (),
          artifacts=("phase_response_summary.json", "phase_response.png", "phase_response_phasor.png"),
          quality_gates=("运行目录已停止写入；新格式按 A03 的 rotation_deg 把逐相位 XY 帧旋转到对齐帧，"
                         "对 Y(φ) 用式 (2.2.2) 整周拟合，给 K₀、Y0/A/r/v0/φc/φz1、残差 rms 与工作点，"
                         "fit_quality 以 rms_v 相对 Y(φ) 峰峰值 5% 判定；历史 CSV 带 X/Y 时同样无旋转拟合，"
                         "只有 R/θ 的 v1 目录保留三分量/最佳相位输出并标 three_component；"
                         "缺帧或点数不足时写原因、空摘要与占位图；图用 paper 样式、英文图注",)),
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
