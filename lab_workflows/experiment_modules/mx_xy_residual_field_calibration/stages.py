"""XY 剩磁标定唯一的模块、参数编辑归属与依赖失效定义。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import DependencyRules, Stage, initial_states
from .run_format import EXPERIMENT_ID

FINISH_ID = "A10"
AXES = ("X_FIELD_START_V", "X_FIELD_STOP_V", "X_FIELD_POINTS",
        "Y_FIELD_START_V", "Y_FIELD_STOP_V", "Y_FIELD_POINTS", "POINT_REPEATS")
SCOPE = ("SCOPE_SAMPLE_RATE", "SCOPE_DURATION", "SCOPE_INITIAL_SCALE", "SCOPE_PD_CHANNEL",
         "SCOPE_TRIGGER_MODE", "SCOPE_OFFSET", "SCOPE_VERTICAL_DIVISIONS", "SCOPE_SCALE_MIN",
         "SCOPE_SCALE_MAX", "SCOPE_AUTO_RANGE_LOW_FRACTION", "SCOPE_AUTO_RANGE_HIGH_FRACTION",
         "SCOPE_AUTO_OFFSET_TOLERANCE_FRACTION", "SCOPE_AUTO_RANGE_MAX_ATTEMPTS", "SCOPE_MINIMUM_POINTS")
ANALYSIS_KEYS = ()
ACQUISITION = (
    Stage("A01", "参数、映射与接线预检", "准备", (),
          quality_gates=("X 至少 3 点、Y 至少 7 点；端点、安全限值及同机通道有效；不连接硬件",)),
    Stage("A02", "设备连接与 GS200 初态回读", "准备", ("A01",),
          quality_gates=("连接复用且 GS200 模式、电流、输出、量程、限流快照完整",)),
    Stage("A03", "参考时钟同步与严格回读", "准备", ("A02",),
          quality_gates=("全部已连接时钟设备回读一致",)),
    Stage("A04", "主场、XYZ 与光功率准备", "初始条件", ("A03",),
          ("SCAN_MAIN_FIELD_MA", "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power")),
    Stage("A05", "TEC 目标温度", "初始条件", ("A04",), ("FIXED_PARAMS.temperature",),
          quality_gates=("仅写目标温度，串口不可用告警跳过；不启停、不判稳",)),
    Stage("A06", "Pump 连续载波与常开门控", "激励", ("A05",),
          ("PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V")),
    Stage("A07", "SDS 固定采样率与 DC 量程准备", "采集", ("A06",), SCOPE,
          display_parameters=("SCOPE_SAMPLE_RATE", "SCOPE_DURATION", "SCOPE_INITIAL_SCALE"),
          quality_gates=("SDS CH1 DC、AUTO/FTRIG、FSRate；offset 固定为零，实际点数和采样率有效",)),
    Stage("A08", "XY 同向网格与逐次量程采集", "采集", ("A07",),
          (*AXES, "RUN_TAG", "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_SETTLE_S"),
          ("raw/xy_residual_scan_axes.npz", "raw/grid_X*_Y*_R*.npz"),
          display_parameters=(*AXES, "RUN_TAG", "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_SETTLE_S", "SCOPE_DURATION"),
          quality_gates=("X 外层、Y 始终正向；每次量程尝试独立关温控、稳定、读入内存、恢复后保存",)),
    Stage("A09", "本轮安全恢复与封存", "结束", ("A08",),
          quality_gates=("恢复 GS200 原态、SDS DC 与温控；XYZ 归零关闭；恢复失败不得封存完成",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "原始网格与重复采集资格", "独立分析", (), artifacts=("xy_residual_inputs.json",),
          quality_gates=("身份与 raw 格式有效，轴、索引、重复次数和波形完整一致；历史采集快照只读",)),
    Stage("B02", "二维 Bloch、时间漂移与色散交叉验证", "独立分析", ("B01",),
          artifacts=("calibration_results.npz", "xy_residual_calibration.csv", "y_dispersive_fits.csv",
                     "xy_residual_fit.json"),
          quality_gates=("连续 Bloch 中心有效才给补偿值；无有效逐 X 色散时保留交叉验证警告，不伪造结果",)),
    Stage("B03", "原科学图与结果导出", "独立分析", ("B02",),
          artifacts=("analysis.json", "analysis.yaml", "pd_mean_voltage_map.png", "pd_drift_corrected_map.png", "pd_mean_surface.png",
                     "bloch_2d_fit.png", "bloch_2d_residual_map.png", "fitted_time_drift.png")),
)


_rules = DependencyRules(finish_id=FINISH_ID)
invalidated_stages = _rules.invalidated_stages
invalidate = _rules.invalidate
parameter_impacts = _rules.parameter_impacts
_validate_selection = _rules.validate_selection


def validate_selection(stages, states, selected):
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("安全结束必须最后执行")
    return _validate_selection(stages, states, selected)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(run_step_id="A08", finish_step_id=FINISH_ID,
        acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
        parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
        diagram_url=f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram")
