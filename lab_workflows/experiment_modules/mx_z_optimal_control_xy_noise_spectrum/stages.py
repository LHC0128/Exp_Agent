"""噪声网格的真实步骤、参数归属与失效依赖。"""
from dataclasses import asdict
from ..mx_z_optimal_control_rf_sensitivity.stages import TEMP, FINISH_ID, invalidate, invalidated_stages, parameter_impacts, validate_selection
from lab_workflows.workflow_stages import Stage, initial_states
from ..mx_z_optimal_control_xy_rf_sensitivity.stages import SOURCES, AXES

ANALYSIS_KEYS = ("LOW_FREQ_SKIP_HZ", "NOISE_BAND_MAX_HZ")
ACQUISITION = (
    Stage("A01", "预检与控制来源冻结", "准备", (), SOURCES,
        quality_gates=("参数、映射、锁相能力与完整电压包络有效；冻结双来源内容",)),
    Stage("A02", "本轮目录、连接与初态", "准备", ("A01",), ("RUN_TAG",),
        ("raw/source_manifest.yaml", "raw/applied_control_waveform.npz")),
    Stage("A03", "时钟、TEC 目标与 Mx 工作点", "初始条件", ("A02",),
        ("FIXED_PARAMS.main_magnetic_field", "FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power",
         "FIXED_PARAMS.temperature", "PUMP_CARRIER_FREQUENCY_HZ", "PUMP_CARRIER_AMPLITUDE_VPP",
         "PUMP_GATE_VOLTAGE_V", "CONTROL_BURST_PHASE_DEG", "TRIGGER_FREQUENCY_HZ", "TRIGGER_AMPLITUDE_VPP",
         "TRIGGER_OFFSET_V", "TRIGGER_DUTY_PERCENT", "DEMOD_IDX", "DEMOD_OSC_IDX", "HF2_SIGNAL_RANGE_V",
         "RESPONSE_RATE_SA_S", "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER"),
        quality_gates=("参考时钟严格回读；只写 TEC 目标；Y 为纯 DC，不执行 RF 校相",)),
    Stage("A04", "初始化蛇形网格与点清单", "采集", ("A03",), AXES,
        ("raw/point_manifest.yaml", "raw/xy_bias_grid.npz")),
    Stage("A05", "逐点重复 R 噪声采集", "采集", ("A04",),
        ("NOISE_N_AVG", "NOISE_DURATION_S", "NOISE_RATE_SA_S", "NOISE_TIME_CONSTANT_S", "NOISE_DEMOD_ORDER", *TEMP),
        ("raw/point_manifest.yaml", "raw/points/*/noise_*.npz"),
        display_parameters=("NOISE_N_AVG", "NOISE_DURATION_S", "NOISE_RATE_SA_S", "NOISE_TIME_CONSTANT_S",
            "NOISE_DEMOD_ORDER", *TEMP, "DEMOD_IDX", "DEMOD_OSC_IDX"),
        quality_gates=("每条采集先恢复温控再保存；恢复或读写故障停止并抢救已读数据",)),
    Stage("A06", "恢复响应模式与安全封存", "结束", ("A05",),
        quality_gates=("X/Y 关闭归零、温控与 GS200 原态恢复成功；Z 和共同触发保持",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "运行身份、格式与原始噪声资格", "独立分析", (),
        quality_gates=("明确 raw1 或已验证的 schema v1/v2 历史；坐标、记录和实际采样率有效",)),
    Stage("B02", "Welch、频段中位数与二维排名", "独立分析", ("B01",), ANALYSIS_KEYS,
        ("noise_metrics.json", "noise_median_matrix.npz", "noise_median_heatmap.png", "noise_median_heatmap.pdf",
         "noise_spectrum_best.png", "noise_spectrum_best.pdf"),
        quality_gates=("平均 PSD 后开方；统计频段低于实际 Nyquist 且非空；窄带谱线参与中位数",)),
    Stage("B03", "保存完整噪声分析汇总", "独立分析", ("B02",), artifacts=("analysis.yaml", "analysis.json")),
)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(run_step_id="A05", finish_step_id=FINISH_ID,
        acquisition=[public(stage) for stage in ACQUISITION], analysis=[public(stage) for stage in ANALYSIS],
        parameter_impacts={"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        diagram_url="/api/experiments/mx-z-optimal-control-xy-noise-spectrum/workflow/diagram")
