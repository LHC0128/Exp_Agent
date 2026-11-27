"""标准 Mx 射频相位灵敏度测量的唯一执行清单、分析产物、参数展示和失效规则。"""
from dataclasses import asdict

from lab_workflows.workflow_stages import DependencyRules, Stage

from . import run_format
from .models import MxStandardRfPhaseSensitivityParams

# 采集链：整周相图 → 工作点细扫 → 基带频响 → 无调制噪声。A06 完成才算一整轮结束。
MEASUREMENT_IDS = ("A03", "A04", "A05", "A06")
FINISH_ID = "A07"
RUN_ID = MEASUREMENT_IDS[-1]
ANALYSIS_KEYS: tuple[str, ...] = ()

# 建立本轮共同条件：射频、主场标定、读出与温控。改任一项都要重做整条采集链。
SETUP_KEYS = ("Y_RF_FREQ_HZ", "Y_RF_VPP", "X_BIAS_V", "Y_BIAS_V", "CAL_K_HZ_PER_MA", "CAL_B_HZ")
# A02–A04 的窄带静态读出；A05/A06 改用 SWEEP_KEYS 里的宽带档。
READOUT_KEYS = ("DEMOD_IDX", "DEMOD_SIGNAL_RANGE", "DEMOD_ORDER", "DEMOD_TC", "DEMOD_RATE",
                "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S", "SET_TEC_TARGET", "TARGET_TEMPERATURE_C")
SCAN_KEYS = ("PHASE_GRID_STEP_DEG", "POINT_DURATION_S")
# 宽带档与基带调相扫频只影响 A05/A06；改任一项都要重做这两段。
SWEEP_KEYS = ("DEMOD_DYNAMIC_TC", "DEMOD_DYNAMIC_RATE", "DYNAMIC_TEMP_SWITCH_ON_LAG_S",
              "PM_FREQUENCIES_HZ", "PM_TONE_DURATION_S")
NOISE_KEYS = ("NOISE_SEGMENTS", "NOISE_DURATION_S", "WELCH_SEGMENT_S")
MEASUREMENT_KEYS = tuple(SETUP_KEYS + READOUT_KEYS + SCAN_KEYS + SWEEP_KEYS + NOISE_KEYS)
assert set(MEASUREMENT_KEYS) == set(MxStandardRfPhaseSensitivityParams.external_names()) - {"RUN_TAG"}

ACQUISITION = (
    Stage("A01", "参数、映射与安全预检", "准备", (),
          ("RUN_TAG", *SETUP_KEYS),
          display_parameters=("RUN_TAG", *SETUP_KEYS),
          quality_gates=("不连接硬件；GS200 电流由 (Y_RF_FREQ_HZ - CAL_B_HZ) / CAL_K_HZ_PER_MA 反算并按 "
                         "main_magnetic_field 限值校验，载波包络按 A05 的 2° 峰值边带与偏置合计后按 "
                         "MFLI Signal Output 1 ±10 V 校验。",)),
    Stage("A02", "连接设备、时钟、主场与载波准备", "准备", ("A01",), READOUT_KEYS,
          artifacts=("experiment_config.yaml",),
          quality_gates=("按设备库同步 10 MHz 并严格回读；温控开关 5 V DC + ON；X 偏置回读；共同触发方波与 "
                         "SYNC 回读；Z 双脉冲链（1200B 与 Z 信号源）关闭并回读；Demod 4 / Osc 4 窄带静态读出；"
                         "MF-MOD 只开连续载波（上下边带关闭）并全节点回读；最后按标定式设 GS200 并回读。",)),
    Stage("A03", "整周相位扫描与圆拟合", "采集", ("A02",), SCAN_KEYS,
          artifacts=("raw/mx_std_settle_*.npz", "raw/mx_std_warmup_*.npz", "raw/mx_std_coarse_*.npz",
                     "raw/mx_std_repeat_*.npz"),
          quality_gates=("先等原子响应稳定（相邻两次直流相量差不超过 0.1 mV），采一段丢弃做预热，再按网格逐点采集，"
                         "末尾回采首点量整圈漂移；每帧体检后写盘，圆拟合给出圆心、半径与残差。",)),
    Stage("A04", "工作点选择与细扫", "采集", ("A03",), (),
          artifacts=("raw/mx_std_fine_*.npz",),
          quality_gates=("按圆模型取 |cos phi| 最大的相图点为细扫中心，±5° 步长 1° 细扫；用实测相位角回归 dY/dphi，"
                         "与 R|cos phi| 互校后定最终工作点。",)),
    Stage("A05", "基带频率响应测量", "采集", ("A04",), SWEEP_KEYS,
          artifacts=("raw/mx_std_pm_*.npz",),
          quality_gates=("仅对 A05/A06 把 MFLI Demod 4 切到高带宽 DEMOD_DYNAMIC_TC / DEMOD_DYNAMIC_RATE"
                         "（A03/A04 的窄带滤波不动），开启 MF-MOD 2° 峰值相干边带 PM，按 PM_FREQUENCIES_HZ "
                         "单次顺序切换边带频率并逐点采集 XY 帧；每点恢复温控并按 DYNAMIC_TEMP_SWITCH_ON_LAG_S 等待后写盘。",)),
    Stage("A06", "无调制噪声测量", "采集", ("A05",), NOISE_KEYS,
          artifacts=("raw/mx_std_noise_*.npz",),
          quality_gates=("保持 A05 的高带宽滤波设置，先关闭上下边带保留 5 kHz 连续载波在工作点，按 "
                         "NOISE_SEGMENTS × NOISE_DURATION_S 分段独立进出温控窗口采集无调制噪声 XY 帧；"
                         "每段恢复温控并等待后写盘；宽频帧体检忽略 5 kHz 载波泄漏的已知寄生频点；本轮收尾成功才封存。",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (),
          quality_gates=("关闭 MF-MOD 上下边带并清零 MFLI Out1 载波幅值与偏置，Z/X 通道归零回读，"
                         "温控恢复 5 V DC + ON，恢复 GS200 运行前状态，停 Demod 4 传输并断开设备；共同触发保持。",)),
)
ANALYSIS = (
    Stage("B01", "圆拟合、基带频响与相位灵敏度分析", "独立分析", (), (),
          artifacts=(run_format.SUMMARY, run_format.DIAGRAM_PLOT,
                     run_format.DIAGRAM_PLOT.replace(".png", ".pdf"), run_format.NOISE_PLOT,
                     run_format.NOISE_PLOT.replace(".png", ".pdf"),
                     run_format.PHASE_TRANSFER_PLOT, run_format.PHASE_NOISE_PLOT),
          quality_gates=("目录已停止写入、原始格式受支持；逐帧复检直流幅度与单音；A05 按工频与 5 kHz 载波泄漏模型"
                         "联合拟合音调幅值并以 √Lorentzian H0 归一化，A06 宽带噪声折算成相位灵敏度（µrad/√Hz，"
                         "含全频段 √Lorentzian 回补）；输出 summary.json、相图、三条噪声路径、"
                         "A05/A06 合并双纵轴图与相位灵敏度单面板图，两图与脉冲射频相位灵敏度实验同款样式，"
                         "图中文字用英文。",)),
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
