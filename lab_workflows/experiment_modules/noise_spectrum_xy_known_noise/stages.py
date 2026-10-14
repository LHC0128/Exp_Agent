"""已知噪声谱的操作定义与真实依赖传播。"""
from dataclasses import asdict, replace
from ..noise_spectrum_xy.stages import Stage, ACQUISITION as SINE_STAGES, initial_states

EXPERIMENT_ID = "noise-spectrum-xy-known-noise"
FINISH_ID = "A15"
ACQUISITION = (
    *SINE_STAGES[:10],
    Stage("A11", "生成与上传 Z 噪声任意波", "噪声激励", ("A10",),
          ("NOISE_BAND_STARTS_HZ", "NOISE_BAND_STOPS_HZ", "NOISE_BAND_PSDS_V2_PER_HZ",
           "NOISE_REPEAT_FREQ_HZ", "NOISE_AMPLITUDE_VPP", "NOISE_SEED", "Z_CALIBRATION_SOURCE_RUN")),
    replace(SINE_STAGES[10], id="A12", dependencies=("A11",)),
    replace(SINE_STAGES[11], id="A13", title="ON/OFF 配对扫描", dependencies=("A12",),
            parameters=(*SINE_STAGES[11].parameters, "Z_INJECTION_SETTLE_S"),
            artifacts=("experiment_config.yaml", "raw/control_scan_axes.npz", "raw/known_noise_waveform.npy", "raw/pair_timing.jsonl", "raw/waveform_C*_{on,off}.npy"),
            display_parameters=(*SINE_STAGES[11].display_parameters, "Z_INJECTION_SETTLE_S")),
    Stage("A14", "解调器恢复", "扫描与结束", ("A13",)),
    Stage(FINISH_ID, "安全收尾 / 结束会话", "扫描与结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载本轮配置与原始数据", "加载", ()),
    Stage("B02", "ON/OFF Welch PSD", "两态谱", ("B01",),
          ("ANALYSIS_BIN_WIDTH_HZ", "ANALYSIS_FREQUENCY_MIN_HZ", "ANALYSIS_FREQUENCY_MAX_HZ"),
          ("psd_matrices.npz", "known_noise_preview.png", "known_noise_preview.pdf")),
    Stage("B03", "OFF 移动脊线重标定", "控制轴", ("B02",), (),
          ("calibration.npz", "noise_spectrum_2d.png", "noise_spectrum_2d.pdf", "differential_psd.png", "differential_psd.pdf"),
          display_parameters=("ANALYSIS_FREQUENCY_MIN_HZ", "ANALYSIS_FREQUENCY_MAX_HZ")),
    Stage("B04", "全控制轴两态拟合、谱分离与背景验收", "谱分离", ("B03",),
          ("ANALYSIS_FIT_HALF_WIDTH_HZ",),
          ("popt_fit.npz", "separated_spectra.npz", "fit_diagnostics.npz", "lorentzian_line_shapes.png", "lorentzian_line_shapes.pdf", "noise_spectra_extracted.png", "noise_spectra_extracted.pdf")),
    Stage("B05", "选择静磁场曲线并拟合检测增益", "检测增益", ("B01",),
          ("ANALYSIS_DISPERSION_SOURCE_RUN",),
          ("dispersion_source.npz", "dispersion_source.json", "dispersion_scan.png", "dispersion_scan.pdf")),
    Stage("B06", "增益换算、注入真值比较与汇总", "定量比较", ("B04", "B05"),
          ("RATIO_PASS_LOW", "RATIO_PASS_HIGH"),
          ("noise_spectra.npz", "noise_spectra.csv", "controlled_uncontrolled_comparison.png", "controlled_uncontrolled_comparison.pdf", "measured_vs_truth.png", "measured_vs_truth.pdf")),
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
