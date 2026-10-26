"""静磁场采集与离线分析的唯一阶段清单。"""

from dataclasses import asdict
from lab_workflows.workflow_stages import Stage, initial_states


EXPERIMENT_ID = "static-sensitivity"
FINISH_ID = "A13"
TEMP = ("SYSTEM_SETTLE_S", "dispersion_temp_switch_on_settle_s")
ACQUISITION = (
    Stage("A01", "启动预检", "启动准备", (), quality_gates=("参数、映射、锁相能力及输出安全限值全部通过。",)),
    Stage("A02", "设备连接与输入初始化", "启动准备", ("A01",)),
    Stage("A03", "参考时钟同步", "启动准备", ("A02",), quality_gates=("所有已连接设备的参考时钟严格回读一致。",)),
    Stage("A04", "初始磁场、光功率与温控", "初始条件", ("A03",),
          ("pump_laser_power", "probe_laser_power", "main_magnetic_field",
           "x_magnetic_field", "y_magnetic_field", "temp_switch", "temperature")),
    Stage("A06", "Pump 载波与脉冲门控", "激励与校相", ("A04",),
          ("pump_mod_freq", "pump_mod_amplitude", "pump_mod_duty",
           "rf_gate_amplitude", "rf_gate_offset", "rf_gate_delay")),
    Stage("A07", "锁相色散配置", "激励与校相", ("A06",),
          ("hf2_demod_idx", "hf2_demod_order", "hf2_signal_range", "hf2_demod_rate", "hf2_demod_tc"),
          quality_gates=("实际色散采样率有限且为正，每点至少 2 个样本。",)),
    Stage("A08", "Bell-Bloom 校相", "激励与校相", ("A07",), TEMP,
          quality_gates=("校相读数与回读相位有限，失败不执行色散采集。",)),
    Stage("A09", "Z 直流逐点色散扫描", "采集与结束", ("A08",),
          ("run_tag", "ramp_low", "ramp_high", "dispersion_steps", "SYSTEM_SETTLE_S",
           "dispersion_duration_s", "dispersion_temp_switch_interval_points"),
          ("raw/dispersion_scan.npz", "raw/dispersion_steps/point_*.npz"),
          display_parameters=("run_tag", "ramp_low", "ramp_high", "dispersion_steps",
                              "hf2_demod_rate", "dispersion_duration_s",
                              "dispersion_temp_switch_interval_points", *TEMP),
          quality_gates=("锁相实际配置与有效前置一致；X/Y/R 样本数、有限性和时间轴通过校验。",)),
    Stage("A10", "噪声模式准备", "采集与结束", ("A09",),
          ("hf2_noise_rate", "hf2_noise_tc", "noise_duration"),
          quality_gates=("实际噪声采样率有限且为正，每段至少 2 个样本。",)),
    Stage("A11", "GS200 多量程 Y 噪声采集", "采集与结束", ("A10",),
          ("noise_n_avg", "gs200_current_ranges", "gs200_range_headroom", "gs200_range_settle_time"),
          ("raw/noise_raw/gs200_range_*/noise_*.npy",),
          display_parameters=("noise_n_avg", "noise_duration", "gs200_current_ranges",
                              "gs200_range_headroom", "gs200_range_settle_time", "SYSTEM_SETTLE_S"),
          quality_gates=("GS200 实际量程与设定一致且电流有限；Y 波形为有限一维数组且至少 2 个样本。",)),
    Stage("A12", "恢复色散配置并结束本轮", "采集与结束", ("A11",),
          quality_gates=("Z、温控和最后实际色散配置的恢复全部成功，才标记本轮完成。",)),
    Stage(FINISH_ID, "安全结束会话", "采集与结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载与校验原始数据", "独立离线分析", (),
          quality_gates=("身份、原始格式、色散轴和噪声波形有效；段数与实际采集记录一致。",)),
    Stage("B02", "色散拟合、增益与线宽", "独立离线分析", ("B01",),
          ("z_v_to_nt",), ("dispersion_fit.npz",),
          quality_gates=("至少 7 个有效色散点；R²≥0.85，γ 相对不确定度≤0.5；拟合参数、增益与线宽有效。",)),
    Stage("B03", "各量程 Welch PSD 与平均", "独立离线分析", ("B01",),
          ("noise_duration",), ("noise_psd.npz",),
          quality_gates=("逐段实际采样率有效，同一及不同量程的 PSD 频率轴完全一致。",)),
    Stage("B04", "灵敏度、量程比较与结果图", "独立离线分析", ("B02", "B03"), artifacts=(
        "gs200_current_range_sensitivity.npz", "gs200_current_range_summary.yaml",
        "full_analysiswithoutpump.png", "full_analysiswithoutpump.pdf",
        "analysis.yaml", "analysis.json", "run_summary.csv"),
          quality_gates=("色散拟合与 PSD 前置有效，各量程平坦段灵敏度均为有限值。",)),
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
    return {"run_step_id": "A12", "finish_step_id": FINISH_ID,
            "acquisition": [public(stage) for stage in ACQUISITION],
            "analysis": [public(stage) for stage in ANALYSIS],
            "parameter_impacts": {"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
            "diagram_url": f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram"}
