"""复用 DirectAW 阶段，每个测量阶段依次执行全部所选控制。"""
from dataclasses import replace
from ..rf_sensitivity_direct_aw import stages as shared
from ..rf_sensitivity_direct_aw.stages import (initial_states, invalidate, invalidated_stages,
    validate_selection, parameter_impacts, FINISH_ID)

NOISE = ("NOISE_BAND_STARTS_HZ", "NOISE_BAND_STOPS_HZ", "NOISE_BAND_PSDS_V2_PER_HZ", "NOISE_REPEAT_FREQ_HZ", "NOISE_AMPLITUDE_VPP", "NOISE_SEED")
ACQUISITION = tuple(replace(stage,
    title={"A09": "所选控制逐版本 XY 校相与噪声准备", "A11": "RF 正弦分量相位扫描（保持噪声相位）"}.get(stage.id, stage.title),
    parameters=tuple("CONTROL_WAVEFORM_FILES" if key == "ARB_WAVEFORM_FILE" else key for key in stage.parameters) + (NOISE if stage.id == "A09" else ()),
    display_parameters=tuple("CONTROL_WAVEFORM_FILES" if key == "ARB_WAVEFORM_FILE" else key for key in (stage.display_parameters if stage.display_parameters is not None else stage.parameters)) + (NOISE if stage.id == "A09" else ()),
    artifacts=tuple("controls/*/" + path if path.startswith(("raw/", "results/")) else path for path in stage.artifacts),
) for stage in shared.ACQUISITION)
ANALYSIS = tuple(replace(stage, title="各控制：" + stage.title,
    artifacts={"B01": (), "B02": ("response_fits.json",), "B03": ("psd_index.json",),
        "B04": ("sensitivity.npz", "analysis.yaml", "analysis.json", "full_analysis.png", "full_analysis.pdf")}[stage.id]) for stage in shared.ANALYSIS)


def definition():
    return shared.definition(acquisition=ACQUISITION, analysis=ANALYSIS, experiment_id="rf-sensitivity-aw-known-noise")
