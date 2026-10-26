"""Bell Bloom Z 标定的步骤、参数编辑归属和后端失效规则。"""
from dataclasses import asdict
from lab_workflows.workflow_stages import Stage, initial_states


FINISH_ID = "A10"
ANALYSIS_KEYS = ("LINEAR_R_SQUARED_MIN",)
PREDICTION_KEYS = ("ZERO_BIAS_CENTER_FREQUENCY_HZ", "Z_INITIAL_HZ_PER_V", "Z_BIAS_START_V")
ACQUISITION = (
    Stage("A01", "启动预检", "准备", (), quality_gates=("参数、映射占用、DG4000 门控方案与锁相扫频能力全部通过",)),
    Stage("A02", "设备连接与输入初态", "准备", ("A01",), ("RUN_TAG",)),
    Stage("A03", "参考时钟同步与严格回读", "准备", ("A02",), quality_gates=("全部已连接时钟设备设置后严格回读一致",)),
    Stage("A04", "辅助场归零、固定主场与光功率", "初始条件", ("A03",),
          ("FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power", "FIXED_PARAMS.main_magnetic_field")),
    Stage("A05", "TEC 目标温度", "初始条件", ("A04",), ("FIXED_PARAMS.temperature",)),
    Stage("A06", "100 MHz 外部门控载波与 Pump 脉冲", "激励", ("A05",),
          (*PREDICTION_KEYS, "PUMP_CARRIER_AMPLITUDE_VPP", "PUMP_MOD_DUTY"), quality_gates=("外部门控脉冲频率和脉宽回读匹配，保持占空比",)),
    Stage("A07", "Demod0 R 解调与实际采样率", "激励", ("A06",),
          (*PREDICTION_KEYS, "HF2_SIGNAL_RANGE_V", "RESPONSE_RATE_SA_S", "FREQUENCY_DURATION_S",
           "RESPONSE_TIME_CONSTANT_S", "RESPONSE_DEMOD_ORDER"), quality_gates=("Demod0 R 实际采样率每频点至少八个样本，解调频率回读一致",)),
    Stage("A08", "Z DC × Pump 调制与解调扫频", "采集", ("A07",),
          (*PREDICTION_KEYS, "Z_BIAS_STOP_V", "Z_BIAS_STEP_V", "FREQUENCY_HALF_WIDTH_HZ", "FREQUENCY_STEP_HZ",
           "PUMP_MOD_DUTY", "FREQUENCY_DURATION_S", "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_LAG_S",
           "R_BAD_POINT_STD_THRESHOLD_V", "R_POINT_MAX_ATTEMPTS"),
          ("raw/z_scan_index.npz", "raw/z_*/*.npz"), quality_gates=("Z 为 DC 且输出开启；门控与解调同步改频并回读", "每次 R 标准差不超过阈值；重采仍失败立即阻断后继并保留全部尝试", "温控先恢复，随后保存频点；索引只纳入完整 Z 频扫")),
    Stage("A09", "零偏参考恢复与本轮封存", "结束", ("A08",), quality_gates=("逐项安全恢复成功后才能标记本轮完成",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "配置与原始频扫资格", "独立分析", (), quality_gates=("本实验身份与兼容格式；已结束写入；索引、路径与频扫数组有效",)),
    Stage("B02", "逐 Z 电压 Lorentzian 共振中心", "独立分析", ("B01",), artifacts=("curve_fits.json",), quality_gates=("中心须收敛、线宽有效且距边缘至少一个 HWHM；失败曲线保留诊断并排除",)),
    Stage("B03", "加权线性 Z 电压标定", "独立分析", ("B02",), ANALYSIS_KEYS,
          ("calibration_evaluation.json",), quality_gates=("至少两个不同电压的有效中心，加权线性 R² 达到保存门槛；不通过仍出具诊断",)),
    Stage("B04", "标定报告与结果图", "独立分析", ("B03",), artifacts=(
        "analysis.yaml", "analysis.json", "calibration_results.npz", "frequency_response_fits.png",
        "z_frequency_calibration.png", "z_frequency_residuals.png")),
)


def invalidated_stages(stages, stage_id):
    affected = {stage_id}
    for stage in stages:
        if affected.intersection(stage.dependencies):
            affected.add(stage.id)
    return tuple(stage for stage in stages if stage.id in affected and stage.id != FINISH_ID)


def invalidate(stages, states, stage_id):
    for stage in invalidated_stages(stages, stage_id):
        states[stage.id] = {"status": "pending" if states[stage.id]["status"] == "pending" else "stale", "error": ""}


def parameter_impacts(stages):
    result = {}
    for stage in stages:
        for key in stage.parameters:
            result.setdefault(key, [])
            result[key] = list(dict.fromkeys([*result[key], *(item.id for item in invalidated_stages(stages, stage.id))]))
    return result


def validate_selection(stages, states, selected):
    ids = [stage.id for stage in stages]
    if not selected or len(set(selected)) != len(selected) or any(item not in ids for item in selected):
        raise ValueError("请选择有效且不重复的模块")
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("安全结束必须最后执行")
    available = {key for key, value in states.items() if value["status"] == "completed"}
    ordered = [item for item in ids if item in selected]
    for item in ordered:
        stage = stages[ids.index(item)]
        missing = set(stage.dependencies) - available
        if missing:
            raise ValueError(f"{item} {stage.title} 缺少前置模块：" + "、".join(
                f"{key} {stages[ids.index(key)].title}" for key in ids if key in missing))
        if item != "A01":
            available.difference_update(stage.id for stage in invalidated_stages(stages, item))
        available.add(item)
    return ordered


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return {"run_step_id": "A08", "finish_step_id": FINISH_ID,
            "acquisition": [public(stage) for stage in ACQUISITION],
        "analysis": [public(stage) for stage in ANALYSIS],
        "parameter_impacts": {"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
        "diagram_url": "/api/experiments/bell-bloom-z-field-calibration/workflow/diagram"}
