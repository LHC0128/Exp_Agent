"""DG4000 偏置实验的实际依赖、参数编辑归属与质量门槛。"""
from dataclasses import asdict
from ..mx_z_optimal_control_rf_sensitivity.stages import TEMP, QUALITY, FINISH_ID, invalidate, invalidated_stages, parameter_impacts, validate_selection
from lab_workflows.workflow_stages import Stage, initial_states

ANALYSIS_KEYS = ()
AXES = ("X_FIELD_START_V", "X_FIELD_STOP_V", "X_FIELD_POINTS",
    "Y_FIELD_START_V", "Y_FIELD_STOP_V", "Y_FIELD_POINTS",
    "Z_BIAS_START_V", "Z_BIAS_STOP_V", "Z_BIAS_POINTS")
SOURCES = ("CONTROL_WAVEFORM_SOURCE", "CORRECTED_CONTROL_SOURCE_RUN",
    "CONTROL_RESULTS_ROOT", "CONTROL_VERSION", "Z_CALIBRATION_SOURCE_RUN",
    "CONTROL_SCALE", "Z_AW_OUTPUT_VPP", "Z_AW_OUTPUT_OFFSET", "CONFIRM_GS200_DISCONNECTED")
ACQUISITION = (
    Stage("A01", "预检、GS200 断开确认与双来源冻结", "准备", (), SOURCES,
        quality_gates=("参数、映射、锁相能力及叠加后的完整 Z 包络有效；冻结控制波形与独立 Z 标定内容",)),
    Stage("A02", "本轮目录、连接与四通道原态快照", "准备", ("A01",), ("RUN_TAG",),
        ("raw/source_manifest.yaml", "raw/applied_control_waveform.npz"),
        quality_gates=("不连接 GS200；保存 Z、共同触发与 X/Y 通道完整运行前状态",)),
    Stage("A03", "时钟、TEC 目标与 Z 最优控制工作点", "初始条件", ("A02",),
        ("FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power",
         "FIXED_PARAMS.temperature", "PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_GATE_VOLTAGE_V",
         "CONTROL_BURST_PHASE_DEG", "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP", "TRIGGER_OFFSET_V",
         "TRIGGER_DUTY_PERCENT", "DEMOD_FREQUENCY_HZ", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V",
         "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER"),
        quality_gates=("参考时钟严格回读；TEC 只写 UI 目标；X/Y 置零关闭后启动 Z 任意波与共同触发",)),
    Stage("A04", "XYZ 偏置蛇形网格与同步 R/X/Y 重试采集", "采集", ("A03",),
        (*AXES, "RESPONSE_DURATION_S", *TEMP, *QUALITY, "BZ_GAMMA_HZ_PER_NT"),
        ("raw/grid_*_attempt_*.npz", "raw/xyz_balance_scan.npz"),
        display_parameters=(*AXES, "RESPONSE_DURATION_S", *TEMP, *QUALITY),
        quality_gates=("Z 仅 set_offset，使用实际波形 Vpp 校验包络；每次尝试恢复温控后写盘；X/Y 复噪声重试用尽阻断",)),
    Stage("A05", "四通道原态恢复与安全封存", "结束", ("A04",),
        quality_gates=("温控恢复成功；Z、共同触发和 X/Y 均恢复运行前完整状态，失败不得封存为完成",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "运行身份、格式与复数网格输入资格", "独立分析", (),
        quality_gates=("明确 raw1 或已验证 schema v1/v2/v3 历史；完整 R/X/Y、轴、采样率与 Z 标定有效",)),
    Stage("B02", "原稳健 Bloch 复数平面拟合与科学图", "独立分析", ("B01",),
        artifacts=("evaluation.json", "balance_results.npz", "bloch_fit_analysis.npz",
            "bloch_fit_analysis.yaml", "bloch_fit_analysis.json", "xyz_balance_grid.csv",
            "xyz_balance_slices.png", "xyz_balance_slices.pdf",
            "xyz_balance_components.png", "xyz_balance_components.pdf",
            "bloch_fit_summary.png", "bloch_fit_summary.pdf"),
        quality_gates=("各平面至少两个 X/Y 点；保留原 soft-L1 多初值拟合及质量诊断，不把 R 最小点冒充主结果或严格 Floquet 零场",)),
    Stage("B03", "保存三维复数平衡分析汇总", "独立分析", ("B02",),
        artifacts=("analysis.yaml", "analysis.json")),
)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(run_step_id="A04", finish_step_id=FINISH_ID,
        acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
        parameter_impacts={"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        diagram_url="/api/experiments/mx-z-optimal-control-dg4000-bias-xyz-balance/workflow/diagram")
