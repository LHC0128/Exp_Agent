"""功率扫描唯一执行清单；编辑归属与参数失效分别声明。"""
from dataclasses import asdict
from ..mx_y_rf_sensitivity.stages import Stage, initial_states, invalidate, invalidated_stages, parameter_impacts, validate_selection as _validate

FINISH_ID = "A08"
RUN_STEP_ID = "A07"
ANALYSIS_KEYS = ("POST_DARK_TIME_S",)
BURST = ("MODULATION_FREQUENCY_HZ", "FIXED_DUTY_CYCLE", "BURST_CYCLES", "PRE_BASELINE_TIME_S",
         "POST_DARK_TIME_S", "BURST_BURST_PERIOD_S", "PUMP_RF_GATE_HIGH_V", "PUMP_RF_GATE_LOW_V",
         "AOM_CARRIER_FREQ_HZ", "AOM_CARRIER_AMPLITUDE_V", "AOM_CARRIER_OFFSET_V")
SCOPE = ("SDS_SAMPLE_RATE", "SDS_PD_CHANNEL", "SDS_TRIG_CHANNEL", "SDS_VOLT_SCALE_PD",
         "SDS_VOLT_SCALE_TRIG", "SDS_TRIG_LEVEL", "SDS_TRIG_SLOPE", "SDS_TIMEBASE_DELAY_S")
SCAN = ("RUN_TAG", "PUMP_POWER", "ACQ_REPEATS", "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_DELAY_S", "VERT_DIVS", "SCALE_MIN", "SCALE_MAX")
PLOT_NAMES = ("transient_fit_examples_pub", "rpump_eff_vs_pump_power_pub", "gamma_rates_vs_pump_power_pub",
              "transient_fit_zoom_pub", "residuals_diagnostics_pub")
ACQUISITION = (
    Stage("A01", "参数、映射与安全预检", "准备", (), quality_gates=("无硬件预检；扫描轴、输出限值、独立物理通道及触发窗口有效",)),
    Stage("A02", "连接设备与可选锁相初始化", "准备", ("A01",), ("USE_HF2_MONITOR",),
          quality_gates=("同资源复用连接；TEC 失败告警跳过；可选锁相仅初始化高阻/DC 输入，不采集解调信号",)),
    Stage("A03", "参考时钟同步与严格回读", "准备", ("A02",), quality_gates=("按当前设备库同步所有已连接时钟设备，回读失败停止",)),
    Stage("A04", "主场、光功率、温控及 TEC 目标", "初始条件", ("A03",),
          ("Z_FIELD_SETPOINT", "PROBE_POWER", "TEC_TEMPERATURE", "TEMP_SWITCH_OFF_V", "TEMP_SWITCH_ON_V", "PUMP_POWER"),
          display_parameters=("Z_FIELD_SETPOINT", "PROBE_POWER", "TEC_TEMPERATURE", "TEMP_SWITCH_OFF_V", "TEMP_SWITCH_ON_V"),
          quality_gates=("其他磁场输出关闭；温控 5 V DC + ON；TEC 只写 UI 目标、不启停、不判稳",)),
    Stage("A05", "AOM 载波与内部触发 Burst 门控", "激励", ("A04",), BURST,
          quality_gates=("扫描量是 Pump DC 电压，不是门控 TTL；门控 TRIGgered/INTernal，SYNC 正极性",)),
    Stage("A06", "示波器配置与采样审计", "采集", ("A05",), SCOPE,
          quality_gates=("PD 高阻/DC；触发通道只作触发；保存真实点数、采样率及时基",)),
    Stage(RUN_STEP_ID, "逐功率试探、重复平均与保存", "采集", ("A06",), SCAN,
          ("raw/shot_p*_r*.npz", "raw/shot_p*_avg.npz", "raw/power_scan_summary.npz"),
          quality_gates=("每帧配置→关温控→系统稳定→读到内存→恢复温控→保存；故障抢救已读帧；重复平均时间轴一致",)),
    Stage(FINISH_ID, "结束会话并保留门控与末点 Pump", "结束", (),
          quality_gates=("无前置；恢复温控、关闭其他磁场、只断开 TEC；保留主场、Pump/Probe、AOM、Time_sequence 输出/Burst/SYNC 及锁相设置",)),
)
ANALYSIS = (
    Stage("B01", "加载配置与平均波形资格", "独立分析", (), artifacts=("input_manifest.json",),
          quality_gates=("身份、格式和实际时间轴有效；只读取平均帧；采集中目录拒绝分析",)),
    Stage("B02", "Hilbert 包络、建立与暗弛豫拟合", "独立分析", ("B01",), ANALYSIS_KEYS,
          ("fit_points.npz", "analysis_pub.yaml", "analysis_pub.json", "analysis.yaml", "analysis.json"),
          quality_gates=("保留原 3 参数包络建立及 5 参数全振荡暗弛豫拟合；失败/低质量仍导出 NaN 和诊断，不伪造质量通过",)),
    Stage("B03", "有效泵浦率、速率与残差图", "独立分析", ("B02",),
          artifacts=tuple(name+suffix for name in PLOT_NAMES for suffix in (".png", ".pdf"))+("plot_manifest.json",)),
)


def validate_selection(stages, states, selected):
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("结束会话必须最后执行")
    return _validate(stages, states, selected)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
        parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
        diagram_url="/api/experiments/bb-power-scan-burst/workflow/diagram")
