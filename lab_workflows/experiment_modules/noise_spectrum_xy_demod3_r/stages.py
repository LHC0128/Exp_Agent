"""唯一的执行顺序、节点参数归属与依赖失效定义。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import DependencyRules, Stage, initial_states
from .run_format import EXPERIMENT_ID

FINISH_ID = "A12"
ANALYSIS_KEYS = ()
SETTLE = ("SYSTEM_SETTLE_S",)
WAVE = ("XY_CTRL_FREQ", "XY_CTRL_QUAD", "XY_AW_REPEAT_FREQ_HZ", "XY_AW_POINTS")
AXES = ("CONTROL_FREQ_START_HZ", "CONTROL_FREQ_STOP_HZ", "CONTROL_FREQ_POINTS",
        "DEMOD3_FREQ_START_HZ", "DEMOD3_FREQ_STOP_HZ", "DEMOD3_FREQ_POINTS",
        "XY_CTRL_K_HZ_PER_V", "XY_CTRL_B_HZ", "XY_CALIBRATION_MIN_ENVELOPE_V", "XY_CALIBRATION_MAX_ENVELOPE_V")
ACQUISITION = (
    Stage("A01", "参数、映射与安全预检", "准备", (),
          quality_gates=("参数、安全范围、实际映射和 HF2 双主电压输入能力有效；不连接仪器",)),
    Stage("A02", "设备连接与输入初始化", "准备", ("A01",),
          quality_gates=("按物理资源复用连接，主电压输入统一 DC 与高阻；失败清理已有连接",)),
    Stage("A03", "参考时钟同步与严格回读", "准备", ("A02",),
          quality_gates=("所有已连接时钟设备按设备库同步，回读不一致停止",)),
    Stage("A04", "主场、光功率与辅助场准备", "初始条件", ("A03",),
          ("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Pump_laser_power",
           "FIXED_PARAMS.Probe_laser_power", "FIXED_PARAMS.Temp_Switch")),
    Stage("A05", "TEC 目标温度", "初始条件", ("A04",), ("FIXED_PARAMS.temperature",),
          quality_gates=("仅写 UI 目标温度；串口不可用告警跳过，不启停或判稳",)),
    Stage("A06", "Pump 门控与双路连续外触发", "激励", ("A05",),
          ("PUMP_MOD_FREQ", "PUMP_MOD_AMPLITUDE", "PUMP_MOD_DUTY", "RF_GATE_AMPLITUDE", "RF_GATE_OFFSET",
           "XY_TRIGGER_FREQ", "XY_TRIGGER_AMPLITUDE", "XY_TRIGGER_OFFSET", "XY_TRIGGER_DUTY", "XY_TRIGGER_PHASE")),
    Stage("A07", "Demod0 配置与自动校相", "校相", ("A06",),
          ("DEMOD0_IDX", "DEMOD0_OSC_IDX", "DEMOD0_SIGNAL_RANGE_V", "DEMOD0_ORDER",
           "DEMOD0_TC_CALIB_S", "DEMOD0_RATE_SA_S", *SETTLE),
          quality_gates=("Demod0 有限且幅度足够，自动校相收敛才允许后继",)),
    Stage("A08", "AuxOut 与物理回环配置", "校相", ("A07",),
          ("AUXOUT_INDEX", "AUXOUT_SOURCE_DEMOD_IDX", "AUXOUT_SOURCE_SELECT", "AUXOUT_SCALE",
           "AUXOUT_OFFSET_V", "SIGIN2_INDEX", "SIGIN2_RANGE_V"),
          quality_gates=("辅助输出具有明确安全上下限，预计总电压不越界；物理线缆需人工确认",)),
    Stage("A09", "DirectAW 整体相位校准", "校相", ("A08",),
          (*WAVE, "XY_CTRL_PHASE", "XY_CALIB_ENVELOPE_V", "XY_PHASE_CAL_TOL_DEG",
           "XY_PHASE_CAL_MAX_ITER", "XY_PHASE_CAL_MIN_R_V", "XY_PHASE_CAL_MIN_R_RATIO", *SETTLE),
          quality_gates=("DirectAW 校相收敛且信号未低于幅度门槛，零包络保持输出 OFF",)),
    Stage("A10", "Demod3 连续 R 采集准备", "扫描", ("A09",),
          ("DEMOD3_IDX", "DEMOD3_OSC_IDX", "DEMOD3_ADC_SELECT", "DEMOD3_ORDER", "DEMOD3_RATE_SA_S",
           "DEMOD3_TC_MIN_S", "DEMOD3_TC_PERIOD_FRACTION", "DEMOD3_ACQUISITION_DURATION_S"),
          quality_gates=("实际采样率为有限正数；解调器、振荡器与 Demod0 独立",)),
    Stage("A11", "二维 R 均值批次扫描与本轮收尾", "扫描", ("A10",),
          (*AXES, "RUN_TAG", "TEMP_RECOVERY_BATCH_POINTS", "TEMP_RECOVERY_TIME_S", *SETTLE),
          ("raw/demod3_r_mean_matrix.npz",),
          display_parameters=(*AXES, "RUN_TAG", "TEMP_RECOVERY_BATCH_POINTS", "TEMP_RECOVERY_TIME_S",
                              *SETTLE, *WAVE, "DEMOD3_ACQUISITION_DURATION_S", "DEMOD3_RATE_SA_S",
                              "DEMOD3_TC_MIN_S", "DEMOD3_TC_PERIOD_FRACTION"),
          quality_gates=("控制频率外层、Demod3 正向内层；每批含末组在恢复温控后保存；安全收尾失败不封存完成",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载配置与均值矩阵资格", "独立分析", (), artifacts=("demod3_r_matrix.npz",),
          quality_gates=("身份、raw 格式、四键矩阵及有限递增轴匹配；历史采集快照只读",)),
    Stage("B02", "脊线稳健标定与逐列洛伦兹拟合", "独立分析", ("B01",),
          artifacts=("calibration.npz", "r_fit_params.npz", "r_spectra.npz"),
          quality_gates=("至少六个脊线标定内点；逐列失败保留 NaN 和掩码，R 不作为 PSD",)),
    Stage("B03", "矩阵、标定与 R 谱绘图导出", "独立分析", ("B02",),
          artifacts=("demod3_r_matrix.png", "calibration.png", "r_spectra.png", "analysis.json", "analysis.yaml")),
)


_rules = DependencyRules(finish_id=FINISH_ID)
invalidated_stages = _rules.invalidated_stages
invalidate = _rules.invalidate
parameter_impacts = _rules.parameter_impacts
validate_selection = _rules.validate_selection


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(run_step_id="A11", finish_step_id=FINISH_ID,
        acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
        parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
        diagram_url=f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram")
