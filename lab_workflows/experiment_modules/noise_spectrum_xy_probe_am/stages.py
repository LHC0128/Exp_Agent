"""Probe AM 噪声谱的操作定义与真实依赖传播。"""
from dataclasses import asdict, replace
from ..noise_spectrum_xy.stages import Stage, ACQUISITION as SINE_STAGES, initial_states

EXPERIMENT_ID = "noise-spectrum-xy-uncontrolled-probe-am"
FINISH_ID = "A16"
CARRIER = ("PROBE_AOM_CARRIER_FREQ_HZ", "PROBE_AOM_CARRIER_VPP", "PROBE_AOM_AM_DEPTH_PERCENT")
AM_NOISE = ("NOISE_BAND_STARTS_HZ", "NOISE_BAND_STOPS_HZ", "NOISE_BAND_PSDS_V2_PER_HZ",
            "NOISE_REPEAT_FREQ_HZ", "NOISE_AMPLITUDE_VPP", "NOISE_SEED")
Z_NOISE = ("INJECT_CONTROLLED_NOISE", "Z_NOISE_BAND_STARTS_HZ", "Z_NOISE_BAND_STOPS_HZ",
           "Z_NOISE_BAND_PSDS_V2_PER_HZ", "Z_NOISE_REPEAT_FREQ_HZ", "Z_NOISE_AMPLITUDE_VPP", "Z_NOISE_SEED")
ACQUISITION = (
    *SINE_STAGES[:7],
    # 载波在 Demod0 校相前开启：Probe 光是校相与采集的信号来源。
    Stage("A08", "Probe AOM 载波与外部 AM 配置", "激励与触发", ("A07",), CARRIER),
    replace(SINE_STAGES[7], id="A09", dependencies=("A08",)),
    replace(SINE_STAGES[8], id="A10", dependencies=("A09",)),
    replace(SINE_STAGES[9], id="A11", dependencies=("A10",)),
    Stage("A12", "生成与上传 AM 噪声任意波", "噪声激励", ("A11",), (*AM_NOISE, *Z_NOISE)),
    replace(SINE_STAGES[10], id="A13", dependencies=("A12",)),
    replace(SINE_STAGES[11], id="A14", title="AM 噪声 ON/OFF 配对扫描", dependencies=("A13",),
            artifacts=("experiment_config.yaml", "raw/control_scan_axes.npz", "raw/probe_am_noise_waveform.npy",
                       "raw/controlled_z_noise_waveform.npy", "raw/pair_timing.jsonl", "raw/waveform_C*_{off,on}.npy"),
            display_parameters=SINE_STAGES[11].display_parameters),
    replace(SINE_STAGES[12], id="A15", dependencies=("A14",)),
    Stage(FINISH_ID, "安全收尾 / 结束会话", "扫描与结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载配置与原始数据", "加载", (),
          quality_gates=("实验身份、OFF/ON 配对、实际采样率和波形长度有效，采集已封存",)),
    Stage("B02", "ON/OFF Welch PSD", "两态谱", ("B01",),
          ("ANALYSIS_BIN_WIDTH_HZ", "ANALYSIS_FREQUENCY_MIN_HZ", "ANALYSIS_FREQUENCY_MAX_HZ"),
          ("psd_matrices.npz", "known_noise_preview.npz", "known_noise_preview.png", "known_noise_preview.pdf")),
    Stage("B03", "OFF 移动脊线重标定", "控制轴", ("B02",), (),
          ("calibration.npz", "noise_spectrum_2d.png", "noise_spectrum_2d.pdf"),
          display_parameters=("ANALYSIS_FREQUENCY_MIN_HZ", "ANALYSIS_FREQUENCY_MAX_HZ")),
    Stage("B04", "全控制轴两态拟合、谱分离与有符号差谱", "谱分离", ("B03",),
          ("ANALYSIS_FIT_HALF_WIDTH_HZ",),
          ("popt_fit.npz", "noise_spectra.npz", "noise_spectra.csv",
           "lorentzian_line_shapes.png", "lorentzian_line_shapes.pdf",
           "controlled_uncontrolled_comparison.png", "controlled_uncontrolled_comparison.pdf",
           "signed_spectral_difference.png", "signed_spectral_difference.pdf")),
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
    return {name: list(dict.fromkeys(item.id for stage in stages if name in stage.parameters
                                   for item in invalidated_stages(stages, stage.id)))
            for name in dict.fromkeys(name for stage in stages for name in stage.parameters)}


def validate_selection(stages, states, selected):
    ids = [stage.id for stage in stages]
    if not selected or len(set(selected)) != len(selected) or any(item not in ids for item in selected):
        raise ValueError("请选择有效且不重复的模块")
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("安全收尾必须最后执行")
    available = {key for key, state in states.items() if state["status"] == "completed"}
    ordered = [item for item in ids if item in selected]
    for item in ordered:
        stage = stages[ids.index(item)]
        missing = set(stage.dependencies) - available
        if missing:
            raise ValueError(f"{item} {stage.title} 缺少前置模块：" + "、".join(
                f"{key} {stages[ids.index(key)].title}" for key in sorted(missing)))
        if item not in ("A01", FINISH_ID):
            available.difference_update(s.id for s in invalidated_stages(stages, item))
        available.add(item)
    return ordered


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return {"acquisition": [public(stage) for stage in ACQUISITION],
            "analysis": [public(stage) for stage in ANALYSIS],
            "parameter_impacts": {"acquisition": parameter_impacts(ACQUISITION), "analysis": parameter_impacts(ANALYSIS)},
            "diagram_url": f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram"}
