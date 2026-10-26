"""相位网格的真实步骤、参数编辑归属与失效范围。"""
from dataclasses import asdict
from ..mx_z_optimal_control_rf_sensitivity.stages import TEMP, QUALITY, FINISH_ID, invalidate, invalidated_stages, parameter_impacts, validate_selection
from lab_workflows.workflow_stages import Stage, initial_states
from ..mx_z_optimal_control_xy_rf_sensitivity.stages import SOURCES, AXES

ANALYSIS_KEYS = ("PHASE_FIT_R_SQUARED_MIN", "PHASE_FIT_AMPLITUDE_SIGMA_MIN")
ACQUISITION = (
    Stage("A01", "预检与控制来源冻结", "准备", (), SOURCES,
        quality_gates=("参数、映射、锁相能力及完整电压包络有效；冻结理论或闭环来源内容",)),
    Stage("A02", "本轮目录、连接与初态", "准备", ("A01",), ("RUN_TAG",),
        ("raw/source_manifest.yaml", "raw/applied_control_waveform.npz")),
    Stage("A03", "时钟、TEC 目标与 Mx 工作点", "初始条件", ("A02",),
        ("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power",
         "FIXED_PARAMS.temperature", "PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V",
         "CONTROL_BURST_PHASE_DEG", "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP", "TRIGGER_OFFSET_V",
         "TRIGGER_DUTY_PERCENT", "Y_RF_FREQUENCY_HZ", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V",
         "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER", "PHASE_CAL_RF_AMPLITUDE_VPP", *AXES),
        display_parameters=("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power",
         "FIXED_PARAMS.temperature", "PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V",
         "CONTROL_BURST_PHASE_DEG", "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP", "TRIGGER_OFFSET_V",
         "TRIGGER_DUTY_PERCENT", "Y_RF_FREQUENCY_HZ", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V",
         "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER", "PHASE_CAL_RF_AMPLITUDE_VPP"),
        quality_gates=("参考时钟严格回读；只写 TEC 目标；Z 持续控制、Y RF 等待共同下降沿触发",)),
    Stage("A04", "蛇形 XY 网格与成对 RF 相位采集", "采集", ("A03",),
        ("PHASE_SCAN_START_DEG", "PHASE_SCAN_STOP_DEG", "PHASE_SCAN_STEP_DEG", "RESPONSE_DURATION_S",
         *ANALYSIS_KEYS, "PHASE_OUTLIER_SIGMA_THRESHOLD", "PHASE_OUTLIER_MAX_REACQUIRE_POINTS", *TEMP, *QUALITY),
        ("raw/grid_*_attempt_*.npz", "raw/xy_rf_phase_response_scan.npz"),
        display_parameters=(*AXES, "PHASE_CAL_RF_AMPLITUDE_VPP", "PHASE_SCAN_START_DEG", "PHASE_SCAN_STOP_DEG",
         "PHASE_SCAN_STEP_DEG", "RESPONSE_DURATION_S", *ANALYSIS_KEYS, "PHASE_OUTLIER_SIGMA_THRESHOLD",
         "PHASE_OUTLIER_MAX_REACQUIRE_POINTS", *TEMP, *QUALITY),
        quality_gates=("每次尝试恢复温控后保存；R 标准差重试用尽阻断，拟合拒绝点保留诊断且继续网格",)),
    Stage("A05", "横向场归零与安全封存", "结束", ("A04",),
        quality_gates=("X/Y 归零关闭、温控与 GS200 原态恢复成功；Z 与共同触发保持当前状态",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "运行身份、格式与完整相位网格资格", "独立分析", (),
        quality_gates=("明确 raw1 或已验证 schema v1/v2 历史；坐标、相位配对、完整网格和实际采样率有效",)),
    Stage("B02", "非线性色散拟合与三组科学图", "独立分析", ("B01",), ANALYSIS_KEYS,
        ("evaluation.json", "xy_rf_phase_response_maps.png", "xy_rf_phase_response_maps.pdf",
         "xy_rf_phase_response_curves.png", "xy_rf_phase_response_curves.pdf",
         "xy_rf_phase_response_nonlinear_maps.png", "xy_rf_phase_response_nonlinear_maps.pdf"),
        quality_gates=("沿用非线性拟合及历史正弦对照；质量未通过仍导出参数、掩码与拒绝原因，不选最佳 XY",)),
    Stage("B03", "保存完整相位响应汇总", "独立分析", ("B02",), artifacts=("analysis.yaml", "analysis.json")),
)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(run_step_id="A04", finish_step_id=FINISH_ID,
        acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
        parameter_impacts={"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        diagram_url="/api/experiments/mx-z-optimal-control-xy-rf-phase-response/workflow/diagram")
