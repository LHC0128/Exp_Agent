"""复用 DirectAW 预检，并核对所有控制与 Z 合成输出。"""
import numpy as np
from ...devices import signal_generator_max_arb_points
from ..rf_sensitivity_direct_aw import preparation as shared
from .waveform import generate_noise, synthesize_z


def preflight(params, root):
    params.validate_model()
    params, mapping, envelope = shared.preflight(params, root, experiment_id="rf-sensitivity-aw-known-noise")
    noise = generate_noise(params)
    if len(noise["noise_V"]) > signal_generator_max_arb_points(mapping["Z_magnetic_field"]):
        raise ValueError("Z 合成波形超过所选信号源任意波点数上限")
    # 任意相位的最坏电压边界先检查，再逐点检查实际归一化结果。
    from ...common import validate_safety_limit
    peak = float(np.max(np.abs(noise["noise_V"]))) + max(abs(params.rf_amp_start), abs(params.rf_amp_stop), params.z_rf_amplitude_init) / 2
    for level in (-peak, peak):
        validate_safety_limit("Z_magnetic_field", level)
    frequencies = [params.z_rf_freq]
    if params.linewidth_mode == "frequency_sweep":
        frequencies.extend(params.bandwidth_frequencies)
    for frequency in frequencies:
        synthesize_z(noise, params.z_rf_amplitude_init, float(frequency))
    synthesize_z(noise, 0.0, params.z_rf_freq)
    return params, mapping, envelope
