"""泄露响应的执行步骤、参数真实使用与失效依赖。"""
from dataclasses import asdict
from ..mx_z_optimal_control_rf_sensitivity.stages import TEMP, QUALITY, FINISH_ID, invalidate, invalidated_stages, parameter_impacts, validate_selection
from lab_workflows.workflow_stages import Stage, initial_states

SOURCES = ("CONTROL_RESULTS_ROOT", "CONTROL_VERSION", "Z_CALIBRATION_SOURCE_RUN", "CONTROL_SCALE",
    "Z_AW_OUTPUT_VPP", "Z_AW_OUTPUT_OFFSET")
AXES = ("X_CONTROL_AMP_START_VPP", "X_CONTROL_AMP_STOP_VPP", "X_CONTROL_AMP_POINTS",
    "Y_CONTROL_AMP_START_VPP", "Y_CONTROL_AMP_STOP_VPP", "Y_CONTROL_AMP_POINTS")
ANALYSIS_KEYS = ()
ACQUISITION = (
    Stage("A01", "预检与理论控制来源冻结", "准备", (), SOURCES,
        quality_gates=("参数、映射、锁相能力及电压包络有效；理论波形、参数与 Z 标定内容冻结",)),
    Stage("A02", "本轮目录、连接与初态", "准备", ("A01",), ("RUN_TAG",),
        ("raw/source_manifest.yaml", "raw/applied_control_waveform.npz")),
    Stage("A03", "时钟、TEC 目标与 Mx 工作点", "初始条件", ("A02",),
        ("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power",
         "FIXED_PARAMS.temperature", "PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V",
         "CONTROL_BURST_PHASE_DEG", "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP", "TRIGGER_OFFSET_V",
         "TRIGGER_DUTY_PERCENT", "DEMOD_FREQUENCY_HZ", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V",
         "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER"),
        quality_gates=("参考时钟严格回读；TEC 只写 UI 目标；XY 初始为零 DC 且关闭，Z 控制启动",)),
    Stage("A04", "蛇形 XY 同形任意波与 R 重试采集", "采集", ("A03",),
        (*AXES, "RESPONSE_DURATION_S", *TEMP, *QUALITY), ("raw/grid_*_attempt_*.npz", "raw/xy_leakage_scan.npz"),
        display_parameters=(*AXES, "RESPONSE_DURATION_S", *TEMP, *QUALITY, "CONTROL_BURST_PHASE_DEG"),
        quality_gates=("每次尝试先恢复温控再写盘；R 标准差通过，重试用尽阻断；读写或恢复错误保留已读数据并停止",)),
    Stage("A05", "施加实测网格最小点", "结束", ("A04",), display_parameters=("CONTROL_BURST_PHASE_DEG",),
        quality_gates=("仅选择实测最小 R，平局取采集最早；共同触发启动后不额外复测 R",)),
    Stage("A06", "保持 Z/XY/触发并安全封存", "结束", ("A05",),
        quality_gates=("温控和 GS200 原态恢复成功；Z、XY 与共同触发保持当前状态",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "运行身份、格式与完整网格资格", "独立分析", (),
        quality_gates=("明确 raw1 或已验证 schema v1/v2 历史；矩阵、坐标、采集序号与实际采样率有效",)),
    Stage("B02", "实测最小点与 R 均值/标准差热图", "独立分析", ("B01",),
        artifacts=("evaluation.json", "xy_leakage_response.png", "xy_leakage_response.pdf"),
        quality_gates=("保留原最小值及最早采集规则；不连续拟合、不外推、不做绝对磁场标定",)),
    Stage("B03", "保存完整泄露响应汇总", "独立分析", ("B02",), artifacts=("analysis.yaml", "analysis.json")),
)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(run_step_id="A04", finish_step_id=FINISH_ID,
        acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
        parameter_impacts={"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        diagram_url="/api/experiments/mx-z-optimal-control-xy-leakage-response/workflow/diagram")
