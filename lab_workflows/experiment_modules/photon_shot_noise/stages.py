"""执行顺序、节点编辑归属和参数失效的唯一清单。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import DependencyRules, Stage, initial_states
from .run_format import EXPERIMENT_ID

FINISH_ID = "A08"
ANALYSIS_KEYS = ("PSD_NPERSEG", "FLAT_FREQ_FRACTION", "FIT_START_IDX")
DEMOD = ("HF2_DEMOD_IDX", "HF2_OSC_FREQ", "HF2_SIGNAL_RANGE", "HF2_DEMOD_ORDER",
         "HF2_DEMOD_TC", "HF2_DEMOD_RATE", "ACQ_DURATION")
AXIS = ("PROBE_POWER_START", "PROBE_POWER_STOP", "PROBE_POWER_POINTS")
PLOTS = ("shot_noise_x", "shot_noise_y", "asd_vs_rms", "psd_white_noise_check")
ACQUISITION = (
    Stage("A01", "参数、映射与安全预检", "准备", (),
          quality_gates=("不连接硬件；扫描、安全限值及锁相采集能力有效",)),
    Stage("A02", "连接设备与主输入初始化", "准备", ("A01",),
          quality_gates=("按实际资源复用连接，主电压输入保持高阻和 DC；TEC 连接失败告警跳过",)),
    Stage("A03", "参考时钟同步与严格回读", "准备", ("A02",),
          quality_gates=("按设备库同步所有已连接时钟设备，回读失败停止",)),
    Stage("A04", "关闭 Pump、准备 Probe 与主场", "初始条件", ("A03",),
          ("FIXED_PARAMS.SHOT_NOISE_FIELD_mA", "FIXED_PARAMS.Pump_laser_power",
           "FIXED_PARAMS.Temp_Switch", "PROBE_POWER_START"),
          quality_gates=("主场偏置和光功率先安全校验；Pump 输出 OFF，Probe 输出 ON；温控 5 V DC + ON",)),
    Stage("A05", "下发 TEC 目标温度", "初始条件", ("A04",), ("FIXED_PARAMS.temperature",),
          quality_gates=("只下发 UI 目标值，不启停、不判稳；未知实际温度为 null",)),
    Stage("A06", "锁相解调与采集模块准备", "采集", ("A05",), DEMOD,
          quality_gates=("主电压输入 0；请求率决定 DAQ/Scope，回读模式与设置一致才允许扫描",)),
    Stage("A07", "Probe 扫描、逐次采集与本轮收尾", "采集", ("A06",),
          (*AXIS, "ACQ_REPEATS", "SYSTEM_SETTLE_S", "TEMP_RECOVERY_TIME_S", "RUN_TAG"),
          ("raw/waveforms_P*.npz", "raw/scan_meta.npz"),
          display_parameters=(*AXIS, "ACQ_REPEATS", "SYSTEM_SETTLE_S", "TEMP_RECOVERY_TIME_S",
                              "RUN_TAG", "ACQ_DURATION", "HF2_DEMOD_RATE"),
          quality_gates=("每条重复独立关/开温控，恢复后保存；取消和恢复失败抢救已读数据，收尾失败不封存完成",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载波形、Welch PSD 与 ASD 底噪", "独立分析", (),
          ("PSD_NPERSEG", "FLAT_FREQ_FRACTION"), ("noise_statistics.npz",),
          quality_gates=("身份、格式、波形索引和实际率有效；Welch 网格一致且平坦频段非空；坏输入先拒绝再归档",)),
    Stage("B02", "ASD 平均值平方的加权线性拟合", "独立分析", ("B01",),
          ("FIT_START_IDX",), ("fit_summary.json",),
          quality_gates=("X/Y 各至少三个有限且权重为正的不同功率点；失败保存诊断并阻断出图",)),
    Stage("B03", "ASD、RMS 和白噪声图表导出", "独立分析", ("B02",),
          artifacts=tuple(name + suffix for name in PLOTS for suffix in (".png", ".pdf")) + ("analysis.yaml",)),
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
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(run_step_id="A07", finish_step_id=FINISH_ID,
        acquisition=[public(s) for s in ACQUISITION], analysis=[public(s) for s in ANALYSIS],
        parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
        diagram_url=f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram")
