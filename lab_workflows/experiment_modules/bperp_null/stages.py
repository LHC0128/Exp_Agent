"""横向静态场补平的唯一执行、编辑归属和失效清单。"""

from dataclasses import asdict

from lab_workflows.workflow_stages import DependencyRules, Stage

from .run_format import EXPERIMENT_ID

FINISH_ID = "A04"
RUN_ID = "A03"
ANALYSIS_KEYS: tuple[str, ...] = ()
# A02 建立链路、锁相、触发与初始 X/Y 偏置。
CHAIN = ("X_BIAS_MV", "Y_BIAS_MV", "MAIN_FIELD_ENABLED", "MAIN_FIELD_CURRENT_MA", "PULSE_FREQ_HZ", "AMPLIFIER_GAIN_DB",
         "AMPLIFIER_INPUT_IMPEDANCE_OHM", "AMPLIFIER_OUTPUT_IMPEDANCE_OHM",
         "AMPLIFIER_WRITE_SETTINGS", "SENSE_RESISTOR_OHM", "DAMPING_RESISTOR_OHM",
         "COIL_RESISTANCE_OHM", "RESISTOR_RATED_W", "DEMOD0_IDX", "DEMOD1_IDX",
         "DEMOD_HARMONIC_2F", "DEMOD_PHASE_DEG", "DEMOD0_ORDER", "DEMOD_TC",
         "DEMOD_RATE", "DEMOD0_SIGNAL_RANGE")
# A03 逐点写 X/Y 偏置并按温控分组采集。
SCAN = ("X_SCAN_START_MV", "X_SCAN_STOP_MV", "X_SCAN_STEP_MV",
        "Y_SCAN_START_MV", "Y_SCAN_STOP_MV", "Y_SCAN_STEP_MV",
        "Z_PEAK_CURRENT_MA", "Z_PULSE_EDGE_US", "Z_PULSE_WIDTH_US", "Z_WAVEFORM_FROM_FILE", "Z_WAVEFORM_FILE",
        "XY_TAIL_COMP_ENABLED", "XY_TAIL_X_MV", "XY_TAIL_Y_MV", "XY_TAIL_TAU_E_US",
        "PULSE_FREQ_HZ",
        "N_REPEAT", "SCAN_POINT_DURATION_S", "SYSTEM_SETTLE_S", "TEMP_GROUP_SIZE",
        "TEMP_SWITCH_ON_LAG_S", "COIL_CONSTANT_HZ_PER_MA")
ACQUISITION = (
    Stage("A01", "参数、映射与安全限值预检", "准备", (), ("RUN_TAG",), display_parameters=("RUN_TAG",),
          quality_gates=("不连接硬件；脉宽不超过半个周期且不小于两倍沿宽；X/Y 网格非空；"
                         "X_magnetic_field、Y_magnetic_field、Z 通道电平与 z_pulse_coil_current 全部在限内；"
                         "两路解调器同速率且走 DAQ，MFLI 至少有 2 个解调器；"
                         "用波形文件时校验 16384 点、有限、最大绝对值 1、首尾连续、period_us 与脉冲周期一致、"
                         "width_us 与 Z_PULSE_WIDTH_US 一致，电阻功耗按文件 mean(v²) 计算",)),
    Stage("A02", "连接 DG、MFLI、1200B、GS200 并同步时钟", "准备", ("A01",), CHAIN,
          quality_gates=("连接前过硬件门控；写 1200B 增益/阻抗并配平 Z 通道负载；"
                         "振荡器 0 = 脉冲频率，第 0 路谐波 1、第 1 路谐波 2，相位均 0 并回读；"
                         "按 X_BIAS_MV/Y_BIAS_MV 给 X、Y 线圈加 DC，Y 通道不出射频；"
                         "GS200 主磁场：MAIN_FIELD_ENABLED 关闭时输出回读为 OFF 才继续，开启时按 MAIN_FIELD_CURRENT_MA 设电流并回读电流与输出；DG 与 MFLI 参考时钟严格回读一致",)),
    Stage(RUN_ID, "X/Y 偏置网格测量（可勾选）", "测量", ("A02",), (*CHAIN, *SCAN),
          ("raw/bias_grid.csv", "experiment_config.yaml"),
          quality_gates=("上传内置 ±π 脉冲或选定波形文件，文件原样复制到 raw/z_waveform.csv 并记录 SHA-256；"
                         "XY_TAIL_COMP_ENABLED 开启时 X/Y 在直流偏置上叠加与 Z 同步的补偿波形（外触发无限 burst），逐点只改 offset，偏置 ± 峰值在限内；"
                         "每个网格点写一次 X/Y 偏置；两路解调器在同一个 DAQ 采集里同时读 X/Y；单次读数丢样、含非有限值或样本不足时原地重采，最多 2 次；"
                         "温控关闭窗口内只读不写盘，每 TEMP_GROUP_SIZE 个读数恢复温控后再落盘；"
                         "取消保留已完成点",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (),
          quality_gates=("先关 1200B，Z 通道 DC 0 V 并关 burst/sync、X/Y 归零关输出、停两路解调传输，"
                         "再恢复温控与 GS200 原状态并断开全部设备",)),
)
ANALYSIS = (
    Stage("B01", "复平面拟合与零点", "独立分析", (), ANALYSIS_KEYS,
          artifacts=("null_summary.json", "next_run.yaml", "null_map.png", "null_map_1f.png",
                     "component_maps.png", "complex_plane.png", "residuals.png"),
          quality_gates=("运行目录已停止写入；按 (x,y) 聚合重复读数并做复数平面拟合；"
                         "解 c2=0 得补偿电压与蒙特卡洛不确定度；同法给 5 kHz 零点与补偿点残余 |c1|；"
                         "信号不足时在摘要中标记 signal_ok=false",)),
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
