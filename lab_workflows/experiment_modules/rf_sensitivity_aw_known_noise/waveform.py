"""先以物理电压求和，再统一归一化；噪声幅度自动对齐设计 PSD。"""
import math
import numpy as np
from ...common import validate_safety_limit
from ..noise_spectrum_xy_known_noise.generation import generate_known_noise_waveform


def generate_noise(params):
    source = generate_known_noise_waveform(repeat_freq_hz=params.noise_repeat_freq_hz,
        bands=params.noise_bands, seed=params.noise_seed)
    # 对齐方法与 XY 已知噪声实验一致：幅度 = 2×设计波形峰值，实际注入谱
    # 与设计谱相等，不再单独设置实际噪声幅度。
    amplitude_vpp = 2.0 * source.design_peak_v
    voltage = source.normalized * amplitude_vpp / 2
    return dict(time_s=np.arange(len(voltage)) / source.sample_rate_sa_s,
        noise_V=voltage, sample_rate_Sa_s=source.sample_rate_sa_s,
        repeat_frequency_Hz=params.noise_repeat_freq_hz, seed=params.noise_seed,
        noise_vpp_V=amplitude_vpp,
        frequency_Hz=source.line_frequencies_hz,
        actual_psd_V2_per_Hz=source.design_psd_v2_per_hz.copy(),
        design_psd_V2_per_Hz=source.design_psd_v2_per_hz,
        noise_rms_V=float(np.sqrt(np.mean(voltage ** 2))))


def synthesize_z(noise, rf_vpp, rf_frequency_hz, phase_deg=0.0):
    if not all(math.isfinite(float(v)) for v in (rf_vpp, rf_frequency_hz, phase_deg)) or rf_frequency_hz <= 0:
        raise ValueError("RF 幅度、频率和相位须有限，频率须为正")
    cycles = rf_frequency_hz / noise["repeat_frequency_Hz"]
    if not math.isclose(cycles, round(cycles), abs_tol=1e-6) or cycles < 1:
        raise ValueError("RF 频率须为噪声重复频率的正整数倍，防止周期边界产生额外频谱")
    if rf_frequency_hz >= noise["sample_rate_Sa_s"] / 2:
        raise ValueError("RF 频率超出 Z 任意波 Nyquist 范围")
    rf = rf_vpp / 2 * np.sin(2 * np.pi * rf_frequency_hz * noise["time_s"] + np.deg2rad(phase_deg))
    voltage = noise["noise_V"] + rf
    peak = float(np.max(np.abs(voltage)))
    vpp = 2 * peak
    if not math.isfinite(vpp) or vpp < 0.001:
        raise ValueError("Z 叠加波形满量程低于仪器 0.001 Vpp 下限")
    for level in (float(voltage.min()), float(voltage.max()), -peak, peak):
        validate_safety_limit("Z_magnetic_field", level)
    return dict(time_s=noise["time_s"], noise_V=noise["noise_V"], rf_V=rf,
        voltage_V=voltage, normalized=voltage / peak, vpp_V=vpp, offset_V=0.0,
        rf_vpp_V=rf_vpp, rf_frequency_Hz=rf_frequency_hz, rf_phase_deg=phase_deg,
        repeat_frequency_Hz=noise["repeat_frequency_Hz"])
