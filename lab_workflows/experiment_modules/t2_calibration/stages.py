"""T2 唯一模块清单；编辑归属与失效范围来自真实业务步骤。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import DependencyRules, Stage, initial_states

RUN_STEP_ID, FINISH_ID = "A06", "A07"
ACQUISITION = (
    Stage("A01", "参数、映射与输出安全预检", "准备", (), quality_gates=("不连接硬件；参数、独立通道与锁相能力有效",)),
    Stage("A02", "按资源复用连接仪器", "准备", ("A01",), ("RUN_TAG",), quality_gates=("部分连接失败释放已连接设备；TEC 失败告警跳过",)),
    Stage("A03", "时钟严格回读、辅助场关闭与温控开启", "准备", ("A02",), quality_gates=("参考时钟回读通过后才进入输出配置",)),
    Stage("A04", "主场、光功率、Pump Burst 与锁相监控", "初始条件", ("A03",),
        ("PUMP_POWER", "MAIN_FIELD_mA", "TEC_TEMPERATURE", "AOM_CARRIER_AMPLITUDE", "RF_GATE_FREQ", "RF_GATE_DUTY",
         "BURST_NCYCLES", "BURST_PERIOD", "DO_POWER_SCAN", "PROBE_POWER", "PROBE_POWER_START", "PROBE_POWER_STOP", "PROBE_POWER_POINTS",
         "HF2_SIGNAL_RANGE", "HF2_DEMOD_TC", "HF2_DEMOD_ORDER", "HF2_DEMOD_RATE"),
        quality_gates=("TEC 只写目标温度；RF 门控保持实验 Burst 策略",)),
    Stage("A05", "示波器触发、记录长度与实际采样率", "采集准备", ("A04",), ("SCOPE_SAMPLE_RATE", "SCOPE_DURATION")),
    Stage(RUN_STEP_ID, "逐 Probe 采集 FID、恢复温控后保存", "采集", ("A05",),
        ("SYSTEM_SETTLE_S", "ACQ_REPEATS", "DO_POWER_SCAN", "PROBE_POWER", "PROBE_POWER_START", "PROBE_POWER_STOP", "PROBE_POWER_POINTS"),
        ("raw/fid_waveforms.npz 或 fid_pNN.npz", "raw/recovery/", "raw/power_scan_waveforms.npz（扫描）"),
        quality_gates=("触发超时不读取旧帧；每次量程尝试恢复后保存；保留完整抢救帧及原始异常",)),
    Stage(FINISH_ID, "安全结束会话（RF 门控 Burst 保持）", "结束", (),
        quality_gates=("无前置；温控恢复 5 V ON；辅助输出归零关闭；只断开 TEC；收尾失败不记成功",)),
)
ANALYSIS = (
    Stage("B01", "核对运行身份与单点／功率扫描原始波形", "独立分析", (), artifacts=("input_manifest.json",),
        quality_gates=("拒绝采集中目录、错误身份及损坏数组；抢救帧不混入正常分析",)),
    Stage("B02", "阻尼振荡拟合、功率外推与结果图", "独立分析", ("B01",),
        quality_gates=("不衰减或低质量拟合保留 NaN 与诊断；步骤完成不代表科学合格",)),
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
        diagram_url="/api/experiments/t2-calibration/workflow/diagram")
