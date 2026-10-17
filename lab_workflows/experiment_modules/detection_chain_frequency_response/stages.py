"""探测链路频率响应标定的流程定义；脚本、后端和界面共用。"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    group: str
    dependencies: tuple[str, ...]
    parameters: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    # 展示归属独立于失效起点；None 表示沿用 parameters。
    display_parameters: tuple[str, ...] | None = None
    quality_gates: tuple[str, ...] = ()


ACQUISITION = (
    Stage("A01", "启动预检", "启动准备", (),
          quality_gates=("参数、映射、锁相能力与输出安全限值通过，解调器索引与 lockin_r 一致。",)),
    Stage("A02", "设备连接", "启动准备", ("A01",)),
    Stage("A03", "参考时钟同步", "启动准备", ("A02",),
          quality_gates=("全部已连接时钟设备按设备库配置写入，参考源回读与目标一致；任一设置或回读失败即停止。",)),
    Stage("A04", "初始输出与温控", "初始条件", ("A03",),
          ("FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power",
           "FIXED_PARAMS.temperature")),
    Stage("A05", "HF2 解调配置", "激励与解调", ("A04",),
          ("HF2_DEMOD_IDX", "HF2_OSC_FREQ_HZ", "HF2_SIGNAL_RANGE_V",
           "HF2_DEMOD_ORDER", "HF2_DAQ_TC_S", "HF2_DAQ_RATE_SA_S"),
          quality_gates=("锁相返回的实际采样率为有限正数。",)),
    Stage("A06", "AOM 载波与外部 AM", "激励与解调", ("A05",),
          ("PROBE_AOM_CARRIER_FREQ_HZ", "PROBE_AOM_CARRIER_AMPLITUDE_VPP")),
    Stage("A07", "频率扫描采集", "扫描与结束", ("A06",),
          ("AM_FREQ_START_HZ", "AM_FREQ_STOP_HZ", "AM_FREQ_POINTS", "SYSTEM_SETTLE_S",
           "PROBE_AOM_AM_AMPLITUDE_VPP", "HF2_DAQ_DURATION_S",
           "TEMP_SWITCH_ON_LAG_S", "PSD_NPERSEG", "RUN_TAG"),
          ("experiment_config.yaml", "raw/waveform_*.npy", "raw/frequency_response.npz",
           "results/results_summary.yaml"),
          quality_gates=("最大基带频率严格小于实际采样率的一半，每点至少取得 8 个 Y 样本。",)),
    Stage("A08", "安全收尾 / 结束会话", "扫描与结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载配置与准备分析", "加载配置与准备分析", (),
          quality_gates=("实验身份及原始格式匹配，频率与谱峰数组非空等长，实际采样率为有限正标量。",)),
    Stage("B02", "归一化响应与斜率对照", "归一化响应与斜率对照", ("B01",),
          (), ("analysis_results.npz",),
          quality_gates=("排除零基带后至少有一个有限正谱峰；归一化仅使用两侧最近有效点。",)),
    Stage("B03", "响应曲线绘图与汇总", "响应曲线绘图与汇总", ("B02",),
          (), ("frequency_response.png", "frequency_response.pdf")),
)


def initial_states(stages):
    return {stage.id: {"status": "pending", "error": ""} for stage in stages}


def invalidated_stages(stages, stage_id):
    """执行与参数影响预览共用同一失效范围。"""
    start = next(i for i, stage in enumerate(stages) if stage.id == stage_id)
    return tuple(stage for stage in stages[start:] if stage.id != "A08")


def invalidate(stages, states, stage_id):
    """前置重跑后，历史记录保留但后续不得作为有效凭据。"""
    for stage in invalidated_stages(stages, stage_id):
        states[stage.id] = {"status": "stale" if states[stage.id]["status"] != "pending" else "pending", "error": ""}


def parameter_impacts(stages):
    """同一参数以最早失效起点为准，与工作流的首次匹配一致。"""
    result = {}
    for stage in stages:
        for name in stage.parameters:
            if name not in result:
                result[name] = [item.id for item in invalidated_stages(stages, stage.id)]
    return result


def validate_selection(stages, states, selected):
    ids = [stage.id for stage in stages]
    if not selected or len(set(selected)) != len(selected) or any(item not in ids for item in selected):
        raise ValueError("请选择有效且不重复的模块")
    ordered = [item for item in ids if item in selected]
    if "A08" in selected and selected[-1] != "A08":
        raise ValueError("安全收尾必须最后执行")
    available = {key for key, value in states.items() if value["status"] == "completed"}
    for item in ordered:
        stage = stages[ids.index(item)]
        missing = [key for key in stage.dependencies if key not in available]
        if missing:
            raise ValueError(f"{item} {stage.title} 缺少前置模块：" + "、".join(
                f"{key} {stages[ids.index(key)].title}" for key in missing))
        if item not in ("A01", "A08"):
            available.difference_update(ids[ids.index(item) + 1:])
        available.add(item)
    return ordered


def definition():
    def public_stage(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}

    return {"acquisition": [public_stage(stage) for stage in ACQUISITION],
            "analysis": [public_stage(stage) for stage in ANALYSIS],
            "parameter_impacts": {"acquisition": parameter_impacts(ACQUISITION),
                                  "analysis": parameter_impacts(ANALYSIS)},
            "diagram_url": "/api/experiments/detection-chain-frequency-response/workflow/diagram"}
