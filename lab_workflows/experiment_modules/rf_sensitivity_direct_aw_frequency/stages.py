"""本实验唯一执行清单；展示归属独立于后端失效范围。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import DependencyRules, Stage, initial_states
RUN_STEP_ID, FINISH_ID = "A06", "A07"
ACQUISITION = (
    Stage("A01", '参数、映射、波形与输出安全预检', '准备', (), (), display_parameters=(), quality_gates=('不连接仪器；参数、通道、波形和安全限值有效',)),
    Stage("A02", '按资源复用连接并严格回读时钟', '准备', ('A01',), ('RUN_TAG',), display_parameters=('RUN_TAG',), quality_gates=('全部参考时钟严格回读；失败不进入后继',)),
    Stage("A03", '主场、光功率、TEC 与 Pump 初始条件', '初始条件', ('A02',), ('PUMP_MOD_FREQ', 'PUMP_MOD_DUTY', 'PUMP_MOD_AMPLITUDE', 'FIXED_PARAMS.Pump_laser_power', 'FIXED_PARAMS.Probe_laser_power', 'FIXED_PARAMS.main_magnetic_field', 'FIXED_PARAMS.temperature', 'FIXED_PARAMS.Temp_Switch'), display_parameters=('PUMP_MOD_FREQ', 'PUMP_MOD_DUTY', 'PUMP_MOD_AMPLITUDE', 'FIXED_PARAMS.Pump_laser_power', 'FIXED_PARAMS.Probe_laser_power', 'FIXED_PARAMS.main_magnetic_field', 'FIXED_PARAMS.temperature', 'FIXED_PARAMS.Temp_Switch'), quality_gates=()),
    Stage("A04", '主解调校相与物理回环准备', '校相', ('A03',), ('SYSTEM_SETTLE_S', 'DEMOD0_IDX', 'DEMOD0_OSC_IDX', 'DEMOD0_SIGNAL_RANGE', 'DEMOD0_ORDER', 'DEMOD0_TC_CALIB', 'DEMOD0_RATE', 'DEMOD3_IDX', 'DEMOD3_OSC_IDX', 'DEMOD3_TC_PERIOD_FRACTION', 'DEMOD3_ORDER', 'DEMOD3_RATE', 'CONFIGURE_AUXOUT2_TO_DEMOD0_Y', 'AUXOUT_INDEX', 'AUXOUT_SOURCE_DEMOD_IDX', 'AUXOUT_SOURCE_SELECT', 'AUXOUT_SCALE', 'AUXOUT_OFFSET', 'SIGIN2_INDEX', 'SIGIN2_RANGE', 'SIGIN2_AC_COUPLING', 'SIGIN2_IMPEDANCE'), display_parameters=('SYSTEM_SETTLE_S', 'DEMOD0_IDX', 'DEMOD0_OSC_IDX', 'DEMOD0_SIGNAL_RANGE', 'DEMOD0_ORDER', 'DEMOD0_TC_CALIB', 'DEMOD0_RATE', 'DEMOD3_IDX', 'DEMOD3_OSC_IDX', 'DEMOD3_TC_PERIOD_FRACTION', 'DEMOD3_ORDER', 'DEMOD3_RATE', 'CONFIGURE_AUXOUT2_TO_DEMOD0_Y', 'AUXOUT_INDEX', 'AUXOUT_SOURCE_DEMOD_IDX', 'AUXOUT_SOURCE_SELECT', 'AUXOUT_SCALE', 'AUXOUT_OFFSET', 'SIGIN2_INDEX', 'SIGIN2_RANGE'), quality_gates=()),
    Stage("A05", 'XY DirectAW 校相与测量解调准备', '校相', ('A04',), ('SYSTEM_SETTLE_S', 'ARB_WAVEFORM_SOURCE', 'ARB_WAVEFORM_FILE', 'CORRECTED_CONTROL_SOURCE_RUN', 'A_ENV_FREQ', 'XY_CTRL_K_HZ_PER_V', 'XY_CTRL_B_HZ', 'XY_CTRL_PHASE', 'XY_CTRL_QUAD', 'ENABLE_XY_CTRL_PHASE_CAL', 'XY_CTRL_PHASE_TOL_DEG', 'XY_CTRL_PHASE_MAX_ITER', 'XY_CTRL_PHASE_MIN_R_V', 'XY_CTRL_PHASE_MIN_R_RATIO', 'XY_TRIGGER_FREQ', 'XY_TRIGGER_AMPLITUDE', 'XY_TRIGGER_OFFSET', 'XY_TRIGGER_DUTY', 'XY_TRIGGER_PHASE', 'PUMP_MOD_FREQ', 'DEMOD0_IDX', 'DEMOD0_OSC_IDX', 'DEMOD0_ORDER', 'DEMOD0_TC_MEAS', 'DEMOD0_RATE'), display_parameters=('SYSTEM_SETTLE_S', 'ARB_WAVEFORM_SOURCE', 'ARB_WAVEFORM_FILE', 'CORRECTED_CONTROL_SOURCE_RUN', 'A_ENV_FREQ', 'XY_CTRL_K_HZ_PER_V', 'XY_CTRL_B_HZ', 'XY_CTRL_PHASE', 'XY_CTRL_QUAD', 'ENABLE_XY_CTRL_PHASE_CAL', 'XY_CTRL_PHASE_TOL_DEG', 'XY_CTRL_PHASE_MAX_ITER', 'XY_CTRL_PHASE_MIN_R_V', 'XY_CTRL_PHASE_MIN_R_RATIO', 'XY_TRIGGER_FREQ', 'XY_TRIGGER_AMPLITUDE', 'XY_TRIGGER_OFFSET', 'XY_TRIGGER_DUTY', 'XY_TRIGGER_PHASE', 'PUMP_MOD_FREQ', 'DEMOD0_IDX', 'DEMOD0_OSC_IDX', 'DEMOD0_ORDER', 'DEMOD0_TC_MEAS', 'DEMOD0_RATE'), quality_gates=('保留校相诊断；是否收敛与步骤完成分别记录',)),
    Stage("A06", '频率 × 相位扫描、恢复温控后保存', '采集', ('A05',), ('SYSTEM_SETTLE_S', 'ACQUISITION_MODE', 'Z_RF_FREQ_START', 'Z_RF_FREQ_STOP', 'Z_RF_FREQ_POINTS', 'Z_RF_FREQ_LOG_SPACED', 'Z_RF_FREQ_OFFSET_HZ', 'Z_RF_AMPLITUDE', 'Z_RF_DRIVE_MODE', 'PHASE_START', 'PHASE_STOP', 'PHASE_POINTS', 'DEMOD0_IDX', 'DEMOD0_RATE', 'DEMOD3_IDX', 'DEMOD3_OSC_IDX', 'DEMOD3_TC_PERIOD_FRACTION', 'DEMOD3_ORDER', 'DEMOD3_RATE', 'DEMOD3_N_AVG', 'TEMP_SWITCH_ON_LAG'), display_parameters=('SYSTEM_SETTLE_S', 'ACQUISITION_MODE', 'Z_RF_FREQ_START', 'Z_RF_FREQ_STOP', 'Z_RF_FREQ_POINTS', 'Z_RF_FREQ_LOG_SPACED', 'Z_RF_FREQ_OFFSET_HZ', 'Z_RF_AMPLITUDE', 'Z_RF_DRIVE_MODE', 'PHASE_START', 'PHASE_STOP', 'PHASE_POINTS', 'DEMOD0_IDX', 'DEMOD0_RATE', 'DEMOD3_IDX', 'DEMOD3_OSC_IDX', 'DEMOD3_TC_PERIOD_FRACTION', 'DEMOD3_ORDER', 'DEMOD3_RATE', 'DEMOD3_N_AVG', 'TEMP_SWITCH_ON_LAG'), quality_gates=('配置完成后关温控、稳定、读入内存、恢复再保存；失败保留已读样本及原始异常',)),
    Stage(FINISH_ID, "安全结束会话", "结束", (), quality_gates=("无前置；温控恢复 5 V ON，X/Y/Z 与外触发归零关闭；只断开 TEC，保留主场、光功率及锁相设置",)),
)
ANALYSIS = (
    Stage("B01", "核对身份、扫描索引与原始数据资格", "独立分析", (), artifacts=("input_manifest.json",), quality_gates=("拒绝采集中目录、错误身份、路径越界及损坏数据；不混入抢救帧",)),
    Stage("B02", '稳健幅频、相位诊断与科研绘图', "独立分析", ("B01",), quality_gates=("科学质量单独记录；失败模型保留诊断，不伪造合格结果",)),
)

_rules = DependencyRules(finish_id=FINISH_ID)
invalidated_stages = _rules.invalidated_stages
invalidate = _rules.invalidate
parameter_impacts = _rules.parameter_impacts
_validate = _rules.validate_selection


def validate_selection(stages, states, selected):
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("安全结束必须最后执行")
    return _validate(stages, states, selected)

def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(run_step_id=RUN_STEP_ID, finish_step_id=FINISH_ID,
        acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
        parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
        diagram_url="/api/experiments/rf-sensitivity-direct-aw-frequency/workflow/diagram")
