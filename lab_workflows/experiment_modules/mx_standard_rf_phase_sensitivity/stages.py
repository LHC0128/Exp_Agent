"""标准 Mx 相图与噪声路径的唯一执行、编辑归属和失效清单。"""
from dataclasses import asdict

from lab_workflows.workflow_stages import DependencyRules, Stage

from . import run_format

# A03 整周扫描 → A04 工作点细扫 → A05 噪声分段，A05 完成才算一整轮结束；A06 只做安全收尾。
FINISH_ID = "A06"
RUN_ID = "A05"
ANALYSIS_KEYS: tuple[str, ...] = ()

# 建立本轮共同条件：射频、主场标定、读出与温控。改任一项都要重做整条采集链。
SETUP_KEYS = ("Y_RF_FREQ_HZ", "Y_RF_VPP", "X_BIAS_V", "Y_BIAS_V", "CAL_K_HZ_PER_MA", "CAL_B_HZ")
READOUT_KEYS = ("DEMOD_IDX", "DEMOD_SIGNAL_RANGE", "DEMOD_ORDER", "DEMOD_TC", "DEMOD_RATE",
                "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S", "SET_TEC_TARGET", "TARGET_TEMPERATURE_C")
SCAN_KEYS = ("PHASE_GRID_STEP_DEG", "POINT_DURATION_S")
NOISE_KEYS = ("NOISE_SEGMENTS", "NOISE_DURATION_S", "WELCH_SEGMENT_S")

ACQUISITION = (
    Stage("A01", "参数、映射与安全预检", "准备", (),
          ("RUN_TAG", *SETUP_KEYS),
          display_parameters=("RUN_TAG", *SETUP_KEYS),
          quality_gates=("不连接硬件；GS200 电流由 (Y_RF_FREQ_HZ - CAL_B_HZ) / CAL_K_HZ_PER_MA 反算并按 "
                         "main_magnetic_field 限值校验，载波包络按 MFLI Signal Output 1 ±10 V 校验。",)),
    Stage("A02", "连接设备、时钟、主场与载波准备", "准备", ("A01",), READOUT_KEYS,
          artifacts=("experiment_config.yaml",),
          quality_gates=("按设备库同步 10 MHz 并严格回读；温控开关 5 V DC + ON；X 偏置回读；共同触发方波与 "
                         "SYNC 回读；Z 双脉冲链（1200B 与 Z 信号源）关闭并回读；Demod 4 / Osc 4 静态读出；"
                         "最后按标定式设 GS200 并回读。",)),
    Stage("A03", "整周相位扫描与圆拟合", "采集", ("A02",), SCAN_KEYS,
          artifacts=("raw/mx_std_settle_*.npz", "raw/mx_std_warmup_*.npz", "raw/mx_std_coarse_*.npz",
                     "raw/mx_std_repeat_*.npz"),
          quality_gates=("先等原子响应稳定（相邻两次直流相量差不超过 0.1 mV），采一段丢弃做预热，再按网格逐点采集，"
                         "末尾回采首点量整圈漂移；每帧体检后写盘，圆拟合给出圆心、半径与残差。",)),
    Stage("A04", "工作点选择与细扫", "采集", ("A03",), (),
          artifacts=("raw/mx_std_fine_*.npz",),
          quality_gates=("按圆模型取 |cos phi| 最大的相图点为细扫中心，±5° 步长 1° 细扫；用实测相位角回归 dY/dphi，"
                         "与 R|cos phi| 互校后定最终工作点。",)),
    Stage(RUN_ID, "噪声分段与安全封存", "采集", ("A04",), NOISE_KEYS,
          artifacts=("raw/mx_std_noise_*.npz",),
          display_parameters=("NOISE_SEGMENTS", "NOISE_DURATION_S", "WELCH_SEGMENT_S"),
          quality_gates=("沿用相图的静态解调配置（时间常数 DEMOD_TC、传输率 DEMOD_RATE，不切高带宽）；"
                         "每段独立进出温控窗口，恢复温控并等待后才写盘；每帧体检，含 100 Hz 以上单音超过直流 20% 的帧作废；"
                         "本轮收尾成功才封存。",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (),
          quality_gates=("关闭并清零 MFLI Out1 与 MF-MOD，Z/X 通道归零回读，恢复温控为 5 V DC + ON，"
                         "恢复 GS200 运行前状态，停解调传输并断开设备；共同触发保持。",)),
)
ANALYSIS = (
    Stage("B01", "圆拟合、工作点斜率与噪声路径分析", "独立分析", (), (),
          artifacts=(run_format.SUMMARY, run_format.DIAGRAM_PLOT, run_format.DIAGRAM_PLOT.replace(".png", ".pdf"),
                     run_format.NOISE_PLOT, run_format.NOISE_PLOT.replace(".png", ".pdf")),
          quality_gates=("目录已停止写入、原始格式受支持；逐帧复检直流幅度与单音；输出 summary.json、"
                         "相图（圆拟合与逐段切线斜率）与三条相位噪声路径对比图，图中文字用英文。",)),
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
