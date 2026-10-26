"""T1 的唯一执行清单、节点编辑归属与参数失效规则。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import DependencyRules, Stage, initial_states

FINISH_ID, RUN_STEP_ID = "A08", "A07"
ANALYSIS_KEYS = ("T1_MIN_S", "T1_MAX_S")
ACQUISITION = (
    Stage("A01", "参数、映射与安全预检", "准备", (), quality_gates=("不连接硬件；半周期、探测电压、独立通道与安全限值有效",)),
    Stage("A02", "按资源复用设备连接", "准备", ("A01",), quality_gates=("不连接 GS200 或锁相；TEC 串口失败告警跳过",)),
    Stage("A03", "参考时钟同步与严格回读", "准备", ("A02",), quality_gates=("全部已连接信号源严格回读，失败不启动输出",)),
    Stage("A04", "X 场、Probe 与 TEC 初始条件", "初始条件", ("A03",),
        ("X_FIELD_AMPLITUDE", "PROBE_POWER", "TEC_TEMPERATURE"),
        quality_gates=("X 场 DC；Y/Z 关闭；温控 5 V + ON；TEC 只写目标温度",)),
    Stage("A05", "Pump 载波与低频开关门控", "激励", ("A04",), ("PUMP_SWITCH_FREQ", "PUMP_MOD_AMPLITUDE"),
        quality_gates=("100 MHz 载波；0–5 V、50%、180° 方波；关闭 Burst/调制，开启同步输出",)),
    Stage("A06", "CH1 差分与 CH4 下降沿触发", "采集准备", ("A05",),
        ("SCOPE_SAMPLING_RATE", "ACQ_DURATION", "TRIGGER_SOURCE", "TRIGGER_LEVEL", "SCOPE_VOLT_SCALE"),
        quality_gates=("STOP→配置→RUN；CH1/CH4 高阻 DC；触发超时拒绝旧波形",)),
    Stage(RUN_STEP_ID, "逐 Probe 重复采集、拟合与恢复后保存", "采集", ("A06",),
        ("RUN_TAG", "DO_POWER_SCAN", "PROBE_POWER_LIST", "PROBE_POWER", "ACQ_REPEATS", "PUMP_PREP_TIME",
         "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S", "VERT_DIVS", "SCALE_MIN", "SCALE_MAX", *ANALYSIS_KEYS),
        ("raw/frame_*.npz", "raw/point_*.npz", "raw/t1_measurement.npz 或 power_scan_results.npz"),
        quality_gates=("每帧恢复温控后保存；档位最多三次尝试；只平均相同时间轴；失败保留完整点与逐帧，不继续危险采集",)),
    Stage(FINISH_ID, "安全结束：X／Y／Z 归零关闭", "结束", (),
        quality_gates=("无前置；恢复温控 5 V + ON；保留 Probe、Pump 载波与门控，只断开 TEC",)),
)
ANALYSIS = (
    Stage("B01", "加载配置与原始衰减资格", "独立分析", (), artifacts=("input_manifest.json",),
        quality_gates=("只读历史单点/扫描；采集中目录、错误身份或损坏数组拒绝分析",)),
    Stage("B02", "指数衰减与倒数弛豫率拟合", "独立分析", ("B01",), ANALYSIS_KEYS,
        ("fit_points.npz", "analysis.yaml", "analysis.json"),
        quality_gates=("失败拟合保留 NaN、掩码与原因；旧扫描无波形时保留原 T1 序列，不伪造重拟合",)),
    Stage("B03", "衰减、残差与 Probe 依赖图", "独立分析", ("B02",), artifacts=("plot_manifest.json",)),
)


_rules = DependencyRules(finish_id=FINISH_ID, finish_error="结束会话必须最后执行")
invalidated_stages = _rules.invalidated_stages
invalidate = _rules.invalidate
parameter_impacts = _rules.parameter_impacts
_validate = _rules.validate_selection


def validate_selection(stages, states, selected):
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("结束会话必须最后执行")
    return _validate(stages, states, selected)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(run_step_id=RUN_STEP_ID, finish_step_id=FINISH_ID,
        acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
        parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
        diagram_url="/api/experiments/t1-calibration/workflow/diagram")
