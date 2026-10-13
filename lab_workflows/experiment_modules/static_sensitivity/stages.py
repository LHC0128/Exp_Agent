"""静磁场采集与离线分析的唯一阶段清单。"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    group: str
    dependencies: tuple[str, ...]
    parameters: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    display_parameters: tuple[str, ...] | None = None


EXPERIMENT_ID = "static-sensitivity"
FINISH_ID = "A13"
TEMP = ("dispersion_temp_switch_off_settle_s", "dispersion_temp_switch_on_settle_s")
ACQUISITION = (
    Stage("A01", "启动预检", "启动准备", ()),
    Stage("A02", "设备连接与输入初始化", "启动准备", ("A01",)),
    Stage("A03", "参考时钟同步", "启动准备", ("A02",)),
    Stage("A04", "初始磁场、光功率与温控", "初始条件", ("A03",),
          ("pump_laser_power", "probe_laser_power", "main_magnetic_field",
           "x_magnetic_field", "y_magnetic_field", "temp_switch", "temperature")),
    Stage("A05", "温度稳定", "初始条件", ("A04",),
          ("temp_tolerance_c", "temp_stable_reads", "temp_poll_interval_s", "max_temp_wait_s")),
    Stage("A06", "Pump 载波与脉冲门控", "激励与校相", ("A05",),
          ("pump_mod_freq", "pump_mod_amplitude", "pump_mod_duty",
           "rf_gate_amplitude", "rf_gate_offset", "rf_gate_delay")),
    Stage("A07", "锁相色散配置", "激励与校相", ("A06",),
          ("hf2_demod_idx", "hf2_demod_order", "hf2_signal_range", "hf2_demod_rate", "hf2_demod_tc")),
    Stage("A08", "Bell-Bloom 校相", "激励与校相", ("A07",), TEMP),
    Stage("A09", "Z 直流逐点色散扫描", "采集与结束", ("A08",),
          ("run_tag", "ramp_low", "ramp_high", "dispersion_steps", "dispersion_step_settle_s",
           "dispersion_duration_s", "dispersion_temp_switch_interval_points"),
          ("raw/dispersion_scan.npz", "raw/dispersion_steps/point_*.npz"),
          display_parameters=("run_tag", "ramp_low", "ramp_high", "dispersion_steps",
                              "hf2_demod_rate", "dispersion_duration_s", "dispersion_step_settle_s",
                              "dispersion_temp_switch_interval_points", *TEMP)),
    Stage("A10", "噪声模式准备", "采集与结束", ("A09",),
          ("hf2_noise_rate", "hf2_noise_tc", "noise_duration")),
    Stage("A11", "GS200 多量程 Y 噪声采集", "采集与结束", ("A10",),
          ("noise_n_avg", "gs200_current_ranges", "gs200_range_headroom", "gs200_range_settle_time"),
          ("raw/noise_raw/gs200_range_*/noise_*.npy",),
          display_parameters=("noise_n_avg", "noise_duration", "gs200_current_ranges",
                              "gs200_range_headroom", "gs200_range_settle_time")),
    Stage("A12", "恢复色散配置并结束本轮", "采集与结束", ("A11",)),
    Stage(FINISH_ID, "安全结束会话", "采集与结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载与校验原始数据", "独立离线分析", ()),
    Stage("B02", "色散拟合、增益与线宽", "独立离线分析", ("B01",),
          ("z_v_to_nt",), ("dispersion_fit.npz",)),
    Stage("B03", "各量程 Welch PSD 与平均", "独立离线分析", ("B01",),
          ("noise_duration",), ("noise_psd.npz",)),
    Stage("B04", "灵敏度、量程比较与结果图", "独立离线分析", ("B02", "B03"), artifacts=(
        "gs200_current_range_sensitivity.npz", "gs200_current_range_summary.yaml",
        "full_analysiswithoutpump.png", "full_analysiswithoutpump.pdf",
        "analysis.yaml", "analysis.json", "run_summary.csv")),
)


def initial_states(stages):
    return {stage.id: {"status": "pending", "error": ""} for stage in stages}


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
        for name in stage.parameters:
            affected = result.setdefault(name, [])
            for item in invalidated_stages(stages, stage.id):
                if item.id not in affected:
                    affected.append(item.id)
    return result


def validate_selection(stages, states, selected):
    ids = [stage.id for stage in stages]
    if not selected or len(set(selected)) != len(selected) or any(item not in ids for item in selected):
        raise ValueError("请选择有效且不重复的模块")
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("安全结束必须最后执行")
    available = {key for key, value in states.items() if value["status"] == "completed"}
    ordered = [stage for stage in stages if stage.id in selected]
    for stage in ordered:
        missing = [key for key in stage.dependencies if key not in available]
        if missing:
            raise ValueError(f"{stage.id} {stage.title} 缺少前置模块：" + "、".join(
                f"{key} {next(item.title for item in stages if item.id == key)}" for key in missing))
        if stage.id != "A01":
            available.difference_update(item.id for item in invalidated_stages(stages, stage.id))
        available.add(stage.id)
    return [stage.id for stage in ordered]


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return {"acquisition": [public(stage) for stage in ACQUISITION],
            "analysis": [public(stage) for stage in ANALYSIS],
            "parameter_impacts": {"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
            "diagram_url": f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram"}
