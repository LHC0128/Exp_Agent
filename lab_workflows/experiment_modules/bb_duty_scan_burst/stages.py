"""BB 占空比扫描的唯一执行、编辑归属和失效清单。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import DependencyRules, Stage, initial_states
from .run_format import EXPERIMENT_ID

FINISH_ID = "A08"
RUN_ID = "A07"
ANALYSIS_KEYS = ("POST_DARK_TIME_S",)
BURST = ("MODULATION_FREQUENCY_HZ", "BURST_CYCLES", "BURST_BURST_PERIOD_S",
         "PUMP_RF_GATE_HIGH_V", "PUMP_RF_GATE_LOW_V", "AOM_CARRIER_FREQ_HZ",
         "AOM_CARRIER_AMPLITUDE_V", "AOM_CARRIER_OFFSET_V", "DUTY_CYCLE_LIST")
SCOPE = ("SDS_SAMPLE_RATE", "SDS_PD_CHANNEL", "SDS_TRIG_CHANNEL", "SDS_VOLT_SCALE_PD", "SDS_VOLT_SCALE_TRIG",
         "SDS_TRIG_LEVEL", "SDS_TRIG_SLOPE", "SDS_TIMEBASE_DELAY_S", "PRE_BASELINE_TIME_S", "POST_DARK_TIME_S")
PLOTS = ("transient_fit_duty_examples_pub", "rpump_eff_vs_duty_cycle_pub", "gamma_rates_vs_duty_cycle_pub",
         "transient_fit_zoom_pub", "residuals_diagnostics_pub")
ACQUISITION = (
    Stage("A01", "参数、映射与安全预检", "准备", (), quality_gates=("不连接硬件；占空比按比例输入、百分比限值校验，拒绝独立通道冲突",)),
    Stage("A02", "连接设备与可选锁相初始化", "准备", ("A01",), ("USE_HF2_MONITOR",),
        quality_gates=("同一资源只连接一次；锁相仅可选监控、主输入高阻/DC；TEC 串口失败告警跳过",)),
    Stage("A03", "参考时钟同步与严格回读", "准备", ("A02",), quality_gates=("全部已连接时钟设备依设备库同步；失败不启动输出",)),
    Stage("A04", "关闭辅助磁场、主场与光功率、TEC 设温", "初始条件", ("A03",),
        ("Z_FIELD_SETPOINT", "FIXED_PUMP_POWER_VOLTAGE", "PROBE_POWER", "TEC_TEMPERATURE"),
        quality_gates=("仅下发 TEC 目标、不启停不判稳；恢复温控为 5 V DC + ON",)),
    Stage("A05", "Pump 连续载波、有限 Burst 与 SYNC", "激励", ("A04",), BURST,
        quality_gates=("固定 TTL 0/5 V，内部触发有限周期；SYNC 上升沿定义 Burst 起点",)),
    Stage("A06", "SDS 双通道、时基与实际采样回读", "采集", ("A05",), SCOPE,
        quality_gates=("PD 与 SYNC 通道分开；实际采样率覆盖 Larmor，记录完整基线/Burst/暗弛豫",)),
    Stage(RUN_ID, "占空比扫描、逐帧保存与本轮安全封存", "采集", ("A06",),
        ("RUN_TAG", "DUTY_CYCLE_LIST", "ACQ_REPEATS", "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_DELAY_S",
         "TEMP_SWITCH_OFF_V", "TEMP_SWITCH_ON_V", "VERT_DIVS", "SCALE_MIN", "SCALE_MAX"),
        ("raw/shot_d*_r*.npz", "raw/shot_d*_avg.npz", "raw/duty_scan_summary.npz"),
        display_parameters=("RUN_TAG", "DUTY_CYCLE_LIST", "ACQ_REPEATS", "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_DELAY_S",
            "TEMP_SWITCH_OFF_V", "TEMP_SWITCH_ON_V", "VERT_DIVS", "SCALE_MIN", "SCALE_MAX", "POST_DARK_TIME_S"),
        quality_gates=("每帧配置/准备→关温控→稳定→内存采集→恢复温控→写盘；每点最多三次探测，重复帧锁档并逐帧保存",
                       "异常抢救已读帧，不伪造完整平均；本轮收尾成功才封存",)),
    Stage(FINISH_ID, "安全结束会话", "结束", (), quality_gates=("停止 SDS、恢复温控、关闭辅助场；保留主场、光功率、Pump 载波、Time_sequence 输出/Burst/SYNC 和锁相设置，仅断开 TEC",)),
)
ANALYSIS = (
    Stage("B01", "核对实验身份、索引与原始波形", "独立分析", (), artifacts=("inputs.json",),
        quality_gates=("目录已停止写入；文件引用、占空比、时间轴与必需波形字段有效；兼容旧分节配置",)),
    Stage("B02", "Hilbert 包络、建立/振荡衰减拟合与有效泵浦率", "独立分析", ("B01",), ANALYSIS_KEYS,
        ("fits.npz", "analysis_pub.yaml", "analysis_pub.json"),
        quality_gates=("保留原 tau 误差及速率误差限幅、可疑点规则与加权线性拟合；质量失败保存诊断但阻止后继出图",)),
    Stage("B03", "瞬态、泵浦率、速率分量及残差图导出", "独立分析", ("B02",),
        artifacts=tuple(name + suffix for name in PLOTS for suffix in (".png", ".pdf"))),
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
    return dict(run_step_id="A07", finish_step_id=FINISH_ID,
        acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
        parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
        diagram_url=f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram")
