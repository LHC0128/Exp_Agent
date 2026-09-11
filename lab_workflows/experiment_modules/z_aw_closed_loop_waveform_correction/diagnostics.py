"""闭环幅值初始化、频谱拆分与电压余量的离线数值诊断。"""

from typing import Any

import numpy as np


def spectral_diagnostics(
    time_s: np.ndarray, target: np.ndarray, measured: np.ndarray,
    command: np.ndarray, *, learning_frequencies_hz: list[float] | None,
    output_vpp: float | None,
    full_time_domain: bool = False,
) -> dict[str, Any]:
    """拆分真实幅值误差、形状误差和保持带外分量时的条件残差。"""
    n = len(target)
    target = np.asarray(target, dtype=float)
    measured = np.asarray(measured, dtype=float)
    command = np.asarray(command, dtype=float)
    if n < 2 or any(len(x) != n for x in (time_s, measured, command)):
        raise ValueError("频谱诊断输入长度不足或不一致")
    if not all(np.all(np.isfinite(x)) for x in (time_s, target, measured, command)):
        raise ValueError("频谱诊断包含非有限值")
    frequencies = np.fft.rfftfreq(n, d=float(np.median(np.diff(time_s))))
    spectra = [np.fft.rfft(x) for x in (target, measured, command)]
    factor = np.full(len(frequencies), 2.0 / n)
    factor[0] = 1.0 / n
    if n % 2 == 0:
        factor[-1] = 1.0 / n
    amplitudes = [np.abs(x) * factor for x in spectra]
    norm = max(float(np.std(target)), 1e-15)
    centered = measured - np.mean(measured)
    denominator = float(np.dot(centered, centered))
    fit_scale = float(np.dot(target - np.mean(target), centered) / denominator) if denominator > 1e-30 else 0.0
    shape_error = target - (fit_scale * centered + np.mean(target))
    gain = float(amplitudes[1][1] / amplitudes[2][1]) if amplitudes[2][1] > 1e-15 else None
    required = float(amplitudes[0][1] / gain) if gain is not None and gain > 1e-15 else None
    harmonics = [
        {"harmonic": k, "frequency_hz": float(frequencies[k]),
         "target_amplitude_a": float(amplitudes[0][k]),
         "measured_amplitude_a": float(amplitudes[1][k]),
         "command_amplitude_v": float(amplitudes[2][k])}
        for k in range(1, min(10, len(frequencies)))
    ]
    report = {
        "fundamental_frequency_hz": float(frequencies[1]),
        "fundamental_gain_a_per_v": gain,
        "estimated_required_fundamental_peak_v": required,
        "estimated_required_fundamental_vpp": 2 * required if required is not None else None,
        "configured_vpp": output_vpp,
        "amplitude_fit_scale": fit_scale,
        "shape_only_nrmse_after_amplitude_fit": float(np.sqrt(np.mean(shape_error**2)) / norm),
        "harmonics": harmonics,
        "learning_band_known": full_time_domain or learning_frequencies_hz is not None,
        "learning_mode": "full_time_domain" if full_time_domain else "recorded_band_or_unknown",
        "estimated_voltage_is_linear_extrapolation": True,
    }
    if full_time_domain or learning_frequencies_hz is not None:
        mask = np.full(len(frequencies), full_time_domain, dtype=bool)
        for frequency in learning_frequencies_hz or []:
            mask |= np.isclose(frequencies, frequency, rtol=1e-6, atol=1e-6)
        error_spectrum = spectra[0] - spectra[1]
        in_band = np.fft.irfft(np.where(mask, error_spectrum, 0), n=n)
        outside = np.fft.irfft(np.where(mask, 0, error_spectrum), n=n)
        report.update(
            learning_band_nrmse=float(np.sqrt(np.mean(in_band**2)) / norm),
            outside_learning_band_nrmse=float(np.sqrt(np.mean(outside**2)) / norm),
            conditional_nrmse_if_learning_band_perfect=float(np.sqrt(np.mean(outside**2)) / norm),
        )
    return report
