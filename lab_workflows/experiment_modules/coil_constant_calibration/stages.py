"""Z 线圈常数标定的唯一执行、编辑归属和失效清单。"""
from dataclasses import asdict

from lab_workflows.workflow_stages import DependencyRules, Stage

from .run_format import EXPERIMENT_ID

FINISH_ID = "A10"
RUN_ID = "A08"
ANALYSIS_KEYS = ("LINEAR_R_SQUARED_MIN", "GYROMAGNETIC_RATIO_HZ_PER_NT", "MIN_ACCEPTED_POINTS",
                 "MIN_BRANCH_POINTS", "BRANCH_SLOPE_TOLERANCE", "NEAR_ZERO_FIT_FLOOR_HZ")

# 链路器件与帧配置：改动后从 A02 起全部失效。
CHAIN = ("AMPLIFIER_GAIN_DB", "AMPLIFIER_INPUT_IMPEDANCE_OHM", "AMPLIFIER_OUTPUT_IMPEDANCE_OHM",
         "AMPLIFIER_WRITE_SETTINGS", "SENSE_RESISTOR_OHM", "SENSE_RESISTOR_TOLERANCE_PERCENT",
         "DAMPING_RESISTOR_OHM", "COIL_RESISTANCE_OHM", "RESISTOR_RATED_W",
         "SETTLE_S", "SCOPE_ACQUIRE_DELAY_S", "SCOPE_CURRENT_CHANNEL", "MAX_MEMORY_DEPTH")
DC_CHECK = ("DC_CHECK_CURRENTS_MA", "ZERO_FRAMES", "ZERO_WINDOW_S")
SCAN = ("CURRENT_START_MA", "CURRENT_STOP_MA", "CURRENT_STEP_MA", "BIDIRECTIONAL_SCAN",
        "CURRENT_SETTLE_TIME_S", "INITIAL_HZ_PER_MA", "PREDICTION_POLARITY",
        "COARSE_HALF_WIDTH_HZ", "COARSE_STEP_HZ",
        "FREQUENCY_HALF_WIDTH_HZ", "FREQUENCY_STEP_HZ", "NEAR_ZERO_SCAN_FLOOR_HZ")
MX_SHARED = ("Y_RF_AMPLITUDE_VPP", "FREQUENCY_DURATION_S", "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S",
             "R_BAD_POINT_STD_THRESHOLD_V", "R_POINT_MAX_ATTEMPTS")
ACQUISITION = (
    Stage("A01", "参数、映射与安全限值预检", "准备", (),
          ("RUN_TAG", "EXPECTED_K_NT_PER_MA", "PULSE_WIDTH_S", "PULSE_REPETITION_HZ", "PULSE_FIELD_MARGIN"),
          display_parameters=("RUN_TAG", "EXPECTED_K_NT_PER_MA", "PULSE_WIDTH_S", "PULSE_REPETITION_HZ",
                              "PULSE_FIELD_MARGIN"),
          quality_gates=("不连接硬件；主磁场电流与参考频率、预测扫频窗为正、限值与锁相能力有效，"
                         "过零几何与 π 设计预览成文",)),
    Stage("A02", "连接设备、写入功放设置并记录 GS200 初态", "准备", ("A01",), CHAIN,
          quality_gates=("连接前过硬件门控；功放输出关闭时写入增益与输入/输出阻抗，信号源负载随输入阻抗配置；"
                         "记录 GS200 运行前源模式/电流/输出/量程/限值",)),
    Stage("A03", "参考时钟同步与严格回读", "准备", ("A02",),
          quality_gates=("全部已连接时钟设备先写入再统一回读并严格校验；失败不开启任何输出",)),
    Stage("A04", "功放零偏与直流 I–V 核对", "基线", ("A03",), (*CHAIN, *DC_CHECK),
          ("raw/zero_*.npz", "raw/dc_check_*.npz"),
          quality_gates=("功放关/开两态、信号源 0 V 测零偏；多点直流核对实测驱动电压→电流关系与线性，"
                         "只记录 k 与零偏、不设判据",)),
    Stage("A05", "初始输出：主磁场、辅助场归零与光功率", "初始条件", ("A04",),
          ("MAIN_FIELD_ENABLED", "MAIN_FIELD_MA", "MAIN_FIELD_CENTER_HZ",
           "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power"),
          quality_gates=("主磁场开启时按 main_magnetic_field 限值校验并写电流（电流与参考拉莫尔频率手填）；"
                         "关闭时只关输出、不写电流；X/Z 辅助场归零关闭，温控开关恢复 5 V ON",)),
    Stage("A06", "TEC 目标温度", "初始条件", ("A05",), ("FIXED_PARAMS.temperature",),
          quality_gates=("只下发界面目标温度，不启停控制器、不判稳；串口失败告警跳过",)),
    Stage("A07", "Pump 载波与 Y RF/R 解调准备", "激励", ("A06",),
          ("PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V",
           "Y_RF_AMPLITUDE_VPP", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V",
           "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER"),
          quality_gates=("Pump 连续 100 MHz 载波与常开门控；Y RF 正弦初始频率取主场参考拉莫尔频率；记录实际解调率",)),
    Stage(RUN_ID, "电流轴 × 逐点粗扫/细扫 Y RF", "测量", ("A07",),
          (*SCAN, *MX_SHARED),
          ("raw/current_scan_index.npz", "raw/current_*/*.npz"),
          quality_gates=("每个电流点先在预测中心 ±COARSE_HALF_WIDTH_HZ 粗扫找峰、再在峰附近细扫；"
                         "主磁场开启时全程不过零，关闭时按 |K(I−I0)| 过零处理；"
                         "预测 |中心| 低于零场下限的点只测电流；R 坏点超门槛重采",)),
    Stage("A09", "本轮安全收尾与封存", "结束", ("A08",),
          quality_gates=("先关 1200B 输出，再把 Z 通道归零关闭、Y RF 关、X 归零、温控恢复 5 V ON、停止 DHO900；"
                         "保持 GS200 本次设定与光路/Pump；失败不得标记安全完成",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (),
          quality_gates=("正常、取消与异常路径均断开 TEC 与其余设备；未连接时不宣称输出已恢复",)),
)
ANALYSIS = (
    Stage("B01", "配置与原始数据资格", "独立分析", (), (),
          quality_gates=("实验身份、原始格式、扫描索引路径与频扫字段有效",)),
    Stage("B02", "逐曲线平方根洛伦兹中心", "独立分析", ("B01",), artifacts=("curve_fits.json",),
          quality_gates=("有效点、γ 与窗口位置基础检查；零场附近曲线标记剔除原因，执行完成与质量通过分开记录",)),
    Stage("B03", "过零几何与加权标定", "独立分析", ("B02",), ANALYSIS_KEYS,
          ("calibration_evaluation.json",),
          quality_gates=("f=|K·(I−I0)| 加权拟合与两支一致性检查（单侧数据退化为直线并给带符号 K）；"
                         "过零在轴内时两支各不少于 MIN_BRANCH_POINTS 点",)),
    Stage("B04", "标定报告与结果图", "独立分析", ("B03",), (),
          ("analysis.yaml", "analysis.json", "calibration_results.npz",
           "frequency_response_fits.png", "coil_constant_calibration.png",
           "coil_voltage_response.png", "coil_constant_residuals.png", "dc_iv_check.png")),
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
