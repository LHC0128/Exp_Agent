"""Probe AOM AM 噪声谱参数与纯计算预览。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np

from ...common import find_project_root, load_mapping, validate_safety_limit
from ...experiment_params import parameter
from ..noise_spectrum_xy.models import NoiseSpectrumXYParams, welch_settings
from ..noise_spectrum_xy_known_noise.generation import (
    calculate_noise_preview,
    generate_known_noise_waveform,
)
from ..noise_spectrum_xy_known_noise.models import noise_waveform_settings


@dataclass(slots=True)
class NoiseSpectrumXYProbeAMParams(NoiseSpectrumXYParams):
    schema_version: ClassVar[int] = 2

    run_tag: str = parameter(default="probe_am", external_name="RUN_TAG", label="运行标签", group="basic")
    noise_band_starts_hz: list[float] = parameter(default_factory=lambda: [10000.0], external_name="NOISE_BAND_STARTS_HZ", label="设计噪声谱段起点", unit="Hz", group="basic")
    noise_band_stops_hz: list[float] = parameter(default_factory=lambda: [20000.0], external_name="NOISE_BAND_STOPS_HZ", label="设计噪声谱段终点", unit="Hz", group="basic")
    noise_band_psds_v2_per_hz: list[float] = parameter(default_factory=lambda: [1.0], external_name="NOISE_BAND_PSDS_V2_PER_HZ", label="设计噪声谱密度", unit="V²/Hz", group="basic")
    noise_repeat_freq_hz: float = parameter(default=10.0, external_name="NOISE_REPEAT_FREQ_HZ", label="噪声波形重复频率", unit="Hz", group="basic", minimum=0.001)
    noise_amplitude_vpp: float = parameter(default=2.0, external_name="NOISE_AMPLITUDE_VPP", label="AM 噪声输出幅度", unit="Vpp", group="basic", minimum=0.0, safety_key="Probe_AOM_AM")
    noise_seed: int = parameter(default=20260922, external_name="NOISE_SEED", label="固定随机种子", group="advanced", minimum=0)
    injection_settle_s: float = parameter(default=0.2, external_name="INJECTION_SETTLE_S", label="AM 噪声切换等待", unit="s", group="advanced", minimum=0)
    carrier_frequency_hz: float = parameter(default=100e6, external_name="PROBE_AOM_CARRIER_FREQ_HZ", label="Probe AOM 载波频率", unit="Hz", group="basic", minimum=1)
    carrier_amplitude_vpp: float = parameter(default=0.05, external_name="PROBE_AOM_CARRIER_VPP", label="Probe AOM 载波幅度", unit="Vpp", group="basic", minimum=0.0, maximum=0.05, safety_key="Probe_AOM_Carrier")
    am_depth_percent: float = parameter(default=100.0, external_name="PROBE_AOM_AM_DEPTH_PERCENT", label="外部 AM 深度", unit="%", group="basic", minimum=0, maximum=120)
    inject_controlled_noise: bool = parameter(default=False, external_name="INJECT_CONTROLLED_NOISE", label="注入 Z 已知可控噪声", group="basic")
    z_noise_band_starts_hz: list[float] = parameter(default_factory=lambda: [10000.0], external_name="Z_NOISE_BAND_STARTS_HZ", label="Z 噪声谱段起点", unit="Hz", group="basic")
    z_noise_band_stops_hz: list[float] = parameter(default_factory=lambda: [30000.0], external_name="Z_NOISE_BAND_STOPS_HZ", label="Z 噪声谱段终点", unit="Hz", group="basic")
    z_noise_band_psds_v2_per_hz: list[float] = parameter(default_factory=lambda: [1e-11], external_name="Z_NOISE_BAND_PSDS_V2_PER_HZ", label="Z 噪声设计谱密度", unit="V²/Hz", group="basic")
    z_noise_repeat_freq_hz: float = parameter(default=10.0, external_name="Z_NOISE_REPEAT_FREQ_HZ", label="Z 噪声重复频率", unit="Hz", group="basic", minimum=0.001)
    z_noise_amplitude_vpp: float = parameter(default=0.003403262733487621, external_name="Z_NOISE_AMPLITUDE_VPP", label="Z 噪声输出幅度", unit="Vpp", group="basic", minimum=0.0, safety_key="Z_magnetic_field")
    z_noise_seed: int = parameter(default=20260922, external_name="Z_NOISE_SEED", label="Z 噪声随机种子", group="advanced", minimum=0)
    z_noise_settle_s: float = parameter(default=0.2, external_name="Z_NOISE_SETTLE_S", label="Z 噪声开启等待", unit="s", group="advanced", minimum=0)

    def noise_bands(self) -> list[tuple[float, float, float]]:
        return list(zip(self.noise_band_starts_hz, self.noise_band_stops_hz,
                        self.noise_band_psds_v2_per_hz, strict=True))

    def z_noise_bands(self) -> list[tuple[float, float, float]]:
        return list(zip(self.z_noise_band_starts_hz, self.z_noise_band_stops_hz,
                        self.z_noise_band_psds_v2_per_hz, strict=True))

    @classmethod
    def derive_external(cls, values: dict[str, Any]) -> dict[str, Any]:
        try:
            params = cls.from_external(values)
            rate = noise_waveform_settings(params.noise_repeat_freq_hz)
            welch = welch_settings(params.hf2_daq_rate, params.hf2_daq_duration,
                                   params.analysis_bin_width_hz)
            waveform = generate_known_noise_waveform(
                repeat_freq_hz=params.noise_repeat_freq_hz,
                bands=params.noise_bands(), seed=params.noise_seed)
            preview = calculate_noise_preview(
                waveform, amplitude_vpp=params.noise_amplitude_vpp,
                bands=params.noise_bands(), bin_width_hz=params.analysis_bin_width_hz)
            z_waveform = generate_known_noise_waveform(
                repeat_freq_hz=params.z_noise_repeat_freq_hz,
                bands=params.z_noise_bands(), seed=params.z_noise_seed)
            aligned_amplitude = 2.0 * waveform.design_peak_v
            z_aligned_amplitude = 2.0 * z_waveform.design_peak_v
            try:
                validate_safety_limit("Probe_AOM_AM", aligned_amplitude)
                validate_safety_limit("Probe_AOM_AM", -aligned_amplitude / 2)
                validate_safety_limit("Probe_AOM_AM", aligned_amplitude / 2)
                aligned_amplitude_safe = True
            except ValueError:
                aligned_amplitude_safe = False
            try:
                validate_safety_limit("Z_magnetic_field", z_aligned_amplitude)
                validate_safety_limit("Z_magnetic_field", -z_aligned_amplitude / 2)
                validate_safety_limit("Z_magnetic_field", z_aligned_amplitude / 2)
                z_aligned_amplitude_safe = True
            except ValueError:
                z_aligned_amplitude_safe = False
            return {
                **rate, **welch,
                "scan_seconds": params.target_noise_freq_points * (
                    2 * params.hf2_daq_duration + params.xy_settle_time
                    + params.temp_switch_on_settle_s + 2 * params.injection_settle_s + 0.3),
                "design_peak_v": waveform.design_peak_v,
                "aligned_amplitude_vpp": aligned_amplitude,
                "aligned_amplitude_safe": aligned_amplitude_safe,
                "realized_psd_scale": (params.noise_amplitude_vpp / (2.0 * waveform.design_peak_v)) ** 2,
                "z_aligned_amplitude_vpp": z_aligned_amplitude,
                "z_aligned_amplitude_safe": z_aligned_amplitude_safe,
                "z_realized_psd_scale": (params.z_noise_amplitude_vpp / z_aligned_amplitude) ** 2,
                "waveform_rms_v": float(np.sqrt(np.mean(preview["voltage_v"] ** 2))),
                "actual_waveform_peak_v": float(np.max(np.abs(preview["voltage_v"]))),
                **{key: np.asarray(preview[key]).tolist() for key in (
                    "time_s", "voltage_v", "frequency_hz", "target_psd_v2_per_hz",
                    "calculated_psd_v2_per_hz")},
            }
        except (TypeError, ValueError, OverflowError) as exc:
            return {"error": str(exc)}

    def validate_model(self) -> list[str]:
        errors = NoiseSpectrumXYParams.validate_model(self)
        lengths = {len(self.noise_band_starts_hz), len(self.noise_band_stops_hz),
                   len(self.noise_band_psds_v2_per_hz)}
        if len(lengths) != 1:
            return [*errors, "噪声谱段起点、终点和谱密度数组必须等长"]
        if not self.noise_band_starts_hz:
            return [*errors, "至少需要一个噪声谱段"]
        try:
            nyquist = noise_waveform_settings(self.noise_repeat_freq_hz)["nyquist_hz"]
        except ValueError as exc:
            return [*errors, str(exc)]
        ordered = []
        for index, (start, stop, psd) in enumerate(self.noise_bands()):
            if not all(math.isfinite(value) for value in (start, stop, psd)):
                errors.append(f"噪声谱段 {index} 必须为有限数值")
            elif not 0 < start < stop < nyquist or psd <= 0:
                errors.append(f"噪声谱段 {index} 必须满足 0 < 起点 < 终点 < Nyquist 且 PSD 为正")
            else:
                ordered.append((start, stop))
        ordered.sort()
        if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
            errors.append("噪声谱段不得重叠")
        if self.noise_amplitude_vpp <= 0:
            errors.append("AM 噪声输出幅度必须大于 0")
        if self.inject_controlled_noise:
            z_lengths = {len(self.z_noise_band_starts_hz), len(self.z_noise_band_stops_hz),
                         len(self.z_noise_band_psds_v2_per_hz)}
            if len(z_lengths) != 1 or not self.z_noise_band_starts_hz:
                errors.append("Z 噪声谱段起点、终点和谱密度数组必须非空且等长")
            else:
                try:
                    generate_known_noise_waveform(
                        repeat_freq_hz=self.z_noise_repeat_freq_hz,
                        bands=self.z_noise_bands(), seed=self.z_noise_seed)
                    validate_safety_limit("Z_magnetic_field", self.z_noise_amplitude_vpp)
                    validate_safety_limit("Z_magnetic_field", -self.z_noise_amplitude_vpp / 2)
                    validate_safety_limit("Z_magnetic_field", self.z_noise_amplitude_vpp / 2)
                except (ValueError, OverflowError) as exc:
                    errors.append(str(exc))
            if self.z_noise_amplitude_vpp <= 0:
                errors.append("Z 噪声输出幅度必须大于 0")
        try:
            validate_safety_limit("Probe_AOM_AM", -self.noise_amplitude_vpp / 2)
            validate_safety_limit("Probe_AOM_AM", self.noise_amplitude_vpp / 2)
            mapping = load_mapping(find_project_root())
            if mapping["Probe_AOM_Carrier"]["device_id"] != mapping["Probe_AOM_AM"]["device_id"]:
                errors.append("Probe_AOM_Carrier 与 Probe_AOM_AM 必须连接到同一台 DG4000")
            if mapping["Z_magnetic_field"]["device_id"] != mapping["Time_sequence_2"]["device_id"]:
                errors.append("Z_magnetic_field 与 Time_sequence_2 必须连接到同一台 DG4000")
            generate_known_noise_waveform(repeat_freq_hz=self.noise_repeat_freq_hz,
                                          bands=self.noise_bands(), seed=self.noise_seed)
        except (KeyError, ValueError) as exc:
            errors.append(str(exc))
        return errors
