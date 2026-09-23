"""XY 控制已知噪声注入实验的强类型参数；继承正弦控制的全部扫描与解调参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np

from ...common import find_project_root
from ...experiment_params import parameter
from .dispersion import MIN_DISPERSION_FIT_POINTS
from ..noise_spectrum_xy.models import (
    NoiseSpectrumXYParams,
    NoiseSpectrumXYParams as _ParentParams,
    welch_settings,
)

# 与 DG4000 任意波点数上限一致；采样率 = NOISE_POINTS * NOISE_REPEAT_FREQ_HZ。
NOISE_POINTS = 16384
DEFAULT_Z_CALIBRATION_RUN = "0920_183423_bb_z_cal"
DEFAULT_Z_TF_RUN = "0820_083722_z_coil_current_frequency_response"


def noise_waveform_settings(repeat_freq_hz: float) -> dict[str, float]:
    """由波形重复频率推导采样率与谱线间隔；不连接硬件。"""
    if not math.isfinite(repeat_freq_hz) or repeat_freq_hz <= 0:
        raise ValueError("噪声波形重复频率必须为有限正数")
    return {
        "sample_rate_sa_s": NOISE_POINTS * repeat_freq_hz,
        "line_spacing_hz": repeat_freq_hz,
        "nyquist_hz": NOISE_POINTS * repeat_freq_hz / 2.0,
    }


@dataclass(slots=True)
class NoiseSpectrumXYKnownNoiseParams(NoiseSpectrumXYParams):
    schema_version: ClassVar[int] = 1

    run_tag: str = parameter(default="known_noise", external_name="RUN_TAG", label="运行标签", group="basic")
    target_noise_freq_stop_hz: float = parameter(default=30000.0, external_name="TARGET_NOISE_FREQ_STOP_HZ", label="目标噪声频率终点", unit="Hz", group="basic", minimum=0)

    # ---- 已知噪声注入（分段平顶谱：三个等长数组，单段即平顶谱） ----
    noise_band_starts_hz: list[float] = parameter(default_factory=lambda: [300.0], external_name="NOISE_BAND_STARTS_HZ", label="噪声段起点", unit="Hz", group="basic", description="分段平顶谱各段起点；与终点、谱密度数组等长。")
    noise_band_stops_hz: list[float] = parameter(default_factory=lambda: [30000.0], external_name="NOISE_BAND_STOPS_HZ", label="噪声段终点", unit="Hz", group="basic", description="分段平顶谱各段终点；段不得重叠。")
    noise_band_psds_v2_per_hz: list[float] = parameter(default_factory=lambda: [1e-6], external_name="NOISE_BAND_PSDS_V2_PER_HZ", label="噪声段设计谱密度", unit="V²/Hz", group="basic", description="设计电压谱密度；实际注入谱 = 设计值 × (幅度 / (2×设计峰值))²，以预览和分析结果为准。")
    noise_repeat_freq_hz: float = parameter(default=4.0, external_name="NOISE_REPEAT_FREQ_HZ", label="波形重复频率", unit="Hz", group="basic", minimum=0.001, description=f"16384 点波形的重复率；采样率 = {NOISE_POINTS}×重复频率，Nyquist = 采样率/2。")
    noise_amplitude_vpp: float = parameter(default=1.0, external_name="NOISE_AMPLITUDE_VPP", label="噪声注入幅度", unit="Vpp", group="basic", minimum=0.0, safety_key="Z_magnetic_field")
    noise_seed: int = parameter(default=20260922, external_name="NOISE_SEED", label="噪声波形种子", group="advanced", minimum=0)

    # ---- 注入开关切换与真值链来源 ----
    z_injection_settle_s: float = parameter(default=0.2, external_name="Z_INJECTION_SETTLE_S", label="注入两态切换等待", unit="s", group="advanced", minimum=0, description="每段采集前设置注入 ON 或 OFF 后均等待；相邻控制点交替两态顺序。")
    z_calibration_source_run: str = parameter(default=DEFAULT_Z_CALIBRATION_RUN, external_name="Z_CALIBRATION_SOURCE_RUN", label="K_Z 标定运行", group="advanced", options_from_directory="data/Bell_Bloom_Z_Field_Calibration", options_pattern="*", options_include_directories=True, options_require_analysis="results/analysis.yaml", options_require_experiment_id="bell-bloom-z-field-calibration", description="真值链只取该运行的斜率 K_Z（Hz/V）；共振中心由主磁场决定，不使用 f_0V。")
    z_tf_source_run: str = parameter(default=DEFAULT_Z_TF_RUN, external_name="Z_TF_SOURCE_RUN", label="Z 线圈频响运行", group="advanced", options_from_directory="data/Z_Coil_Current_Frequency_Response", options_pattern="*", options_include_directories=True, options_require_relative_file="results/frequency_response.npz", description="高频修正 |H(f)|/|H(f_ref)|，f_ref 取该运行最低可靠频点；K_Z 为直流标定。")

    # ---- 内置色散斜率扫描（增益换算） ----
    dispersion_span_v: float = parameter(default=0.17, external_name="DISPERSION_SPAN_V", label="色散扫描半宽", unit="V", group="advanced", minimum=0.001, safety_key="Z_magnetic_field", description="z 偏置步进扫描 ±该电压，覆盖共振色散中心段；默认约 ±2 kHz（按 K_Z 换算）。")
    dispersion_steps: int = parameter(default=21, external_name="DISPERSION_STEPS", label="色散扫描点数", group="advanced", minimum=5)
    dispersion_step_settle_s: float = parameter(default=0.2, external_name="DISPERSION_STEP_SETTLE_S", label="色散每步等待", unit="s", group="advanced", minimum=0)
    dispersion_samples_per_step: int = parameter(default=10, external_name="DISPERSION_SAMPLES_PER_STEP", label="色散每步采样数", group="advanced", minimum=1)
    dispersion_fit_skip_start: int = parameter(default=0, external_name="DISPERSION_FIT_SKIP_START", label="色散拟合起始剔除点数", group="advanced", minimum=0, description="从色散线形起始端剔除的点数；与终止剔除点数共同界定参与线形拟合的幅度点范围，两端剔除后至少保留 7 点。")
    dispersion_fit_skip_end: int = parameter(default=0, external_name="DISPERSION_FIT_SKIP_END", label="色散拟合终止剔除点数", group="advanced", minimum=0, description="从色散线形终止端剔除的点数；与起始剔除点数共同界定参与线形拟合的幅度点范围。")
    dispersion_temp_switch_interval_points: int = parameter(default=5, external_name="DISPERSION_TEMP_SWITCH_INTERVAL_POINTS", label="色散温控恢复间隔点数", group="advanced", minimum=0, description="每测完该数量的色散点后打开温度开关一次（按 TEMP_SWITCH_ON_SETTLE_S 等待后重新关闭），避免长时间关闭温控导致温度漂移；0 表示整段保持关闭。")

    # ---- 定量判据 ----
    analysis_fit_half_width_hz: float = parameter(default=3000.0, external_name="ANALYSIS_FIT_HALF_WIDTH_HZ", label="峰定位半窗口", unit="Hz", group="advanced", minimum=1, description="用于峰两侧点数、线宽/频移边界及峰区残差验收；实际拟合使用脊线支持内的全部控制点，远端用于约束背景。")
    analysis_frequency_max_hz: float = parameter(default=50000.0, external_name="ANALYSIS_FREQUENCY_MAX_HZ", label="分析输出频带上限", unit="Hz", group="advanced", minimum=1, description="控制输出谱的频带；脊线搜索同时覆盖保存的扫描目标终点，不用手动 K/B 替代实测标定。")
    ratio_pass_low: float = parameter(default=0.5, external_name="RATIO_PASS_LOW", label="判据比值下限", group="advanced", minimum=0.001, description="带内中位 S_meas/S_truth 低于该值判为不通过。")
    ratio_pass_high: float = parameter(default=2.0, external_name="RATIO_PASS_HIGH", label="判据比值上限", group="advanced", minimum=0.001, description="带内中位 S_meas/S_truth 高于该值判为不通过。")

    @classmethod
    def migrate_external(cls, values: dict[str, Any], schema_version: int) -> dict[str, Any]:
        # 删除已废弃字段；旧快照仍能加载，但新表单和运行不再保存它。
        # slots dataclass 子类不能用零参 super()（类被重建），须显式绑定父类。
        migrated = _ParentParams.migrate_external(values, 1 if schema_version < 1 else schema_version)
        migrated.pop("ANALYSIS_BACKGROUND_HALF_WIDTH_HZ", None)
        return migrated

    def noise_bands(self) -> list[tuple[float, float, float]]:
        """返回 (start, stop, psd) 段列表；长度一致性由 validate_model 保证。"""
        return list(zip(self.noise_band_starts_hz, self.noise_band_stops_hz,
                        self.noise_band_psds_v2_per_hz, strict=True))

    @classmethod
    def derive_external(cls, values: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        try:
            params = cls.from_external(values)
        except (TypeError, ValueError, OverflowError) as exc:
            return {"error": str(exc)}
        try:
            welch = welch_settings(params.hf2_daq_rate, params.hf2_daq_duration,
                                   params.analysis_bin_width_hz)
            result.update(welch)
            result["scan_seconds"] = params.target_noise_freq_points * (
                params.hf2_daq_duration * 2 + params.xy_settle_time
                + params.temp_switch_on_settle_s + 2 * params.z_injection_settle_s + 0.3)
            result.update(noise_waveform_settings(params.noise_repeat_freq_hz))
            preview = _preview_injection(params)
            result["design_peak_v"] = preview["design_peak_v"]
            result["aligned_amplitude_vpp"] = preview["aligned_amplitude_vpp"]
            result["realized_psd_scale"] = preview["realized_psd_scale"]
            result["waveform_rms_v"] = preview["waveform_rms_v"]
            result["equivalent_noise_hz_per_rt_hz"] = preview["equivalent_noise_hz_per_rt_hz"]
            result["z_calibration_k_hz_per_v"] = preview["z_calibration_k_hz_per_v"]
            for key in ("waveform_time_s", "waveform_voltage_v", "spectrum_frequency_hz",
                        "target_psd_v2_per_hz", "calculated_psd_v2_per_hz"):
                result[key] = preview[key]
        except (TypeError, ValueError, OverflowError) as exc:
            result["error"] = str(exc)
        return result

    def validate_model(self) -> list[str]:
        errors = _ParentParams.validate_model(self)
        lengths = {len(self.noise_band_starts_hz), len(self.noise_band_stops_hz),
                   len(self.noise_band_psds_v2_per_hz)}
        if len(lengths) != 1:
            errors.append("NOISE_BAND_STARTS_HZ / NOISE_BAND_STOPS_HZ / NOISE_BAND_PSDS_V2_PER_HZ 必须等长")
            return errors
        if not self.noise_band_starts_hz:
            errors.append("至少需要一个噪声谱段")
            return errors
        settings = noise_waveform_settings(self.noise_repeat_freq_hz)
        nyquist = settings["nyquist_hz"]
        ordered = []
        for index, (start, stop, psd) in enumerate(self.noise_bands()):
            if not (math.isfinite(start) and math.isfinite(stop) and math.isfinite(psd)):
                errors.append(f"噪声段 {index} 含非有限数值")
                continue
            if start <= 0 or stop <= start:
                errors.append(f"噪声段 {index} 必须满足 0 < 起点 < 终点")
                continue
            if stop >= nyquist:
                errors.append(f"噪声段 {index} 终点 {stop:g} Hz 超出 Nyquist {nyquist:g} Hz")
                continue
            if psd <= 0:
                errors.append(f"噪声段 {index} 谱密度必须为正")
                continue
            ordered.append((start, stop))
        ordered.sort()
        for (start_a, stop_a), (start_b, _) in zip(ordered, ordered[1:]):
            if start_b < stop_a:
                errors.append(f"噪声段重叠：[{start_b:g}, …] 与 […, {stop_a:g}]")
        if errors:
            return errors
        # 段边界对齐谱线栅格只影响定义粒度，不对齐时由最接近的谱线承载；
        # 波形生成内部自检带内 PSD，幅度低于设计峰值只是谱缩放，由预览给出实际值。
        if self.noise_amplitude_vpp <= 0:
            errors.append("NOISE_AMPLITUDE_VPP 必须大于 0")
            return errors
        try:
            from .generation import generate_known_noise_waveform
            generate_known_noise_waveform(
                repeat_freq_hz=self.noise_repeat_freq_hz,
                bands=self.noise_bands(),
                seed=self.noise_seed)
        except ValueError as exc:
            errors.append(str(exc))
        if self.ratio_pass_low >= self.ratio_pass_high:
            errors.append("RATIO_PASS_LOW 必须小于 RATIO_PASS_HIGH")
        if self.dispersion_steps - self.dispersion_fit_skip_start - self.dispersion_fit_skip_end < MIN_DISPERSION_FIT_POINTS:
            errors.append(
                "DISPERSION_FIT_SKIP_START / DISPERSION_FIT_SKIP_END 剔除后参与拟合的色散点不足 "
                f"{MIN_DISPERSION_FIT_POINTS} 个"
            )
        return errors


def _preview_injection(params: NoiseSpectrumXYKnownNoiseParams) -> dict[str, Any]:
    """只读预览实际注入谱与等效频率噪声；不连接硬件。"""
    from .generation import calculate_noise_preview, generate_known_noise_waveform, load_z_calibration
    waveform = generate_known_noise_waveform(
        repeat_freq_hz=params.noise_repeat_freq_hz,
        bands=params.noise_bands(),
        seed=params.noise_seed)
    scale = (params.noise_amplitude_vpp / 2.0 / waveform.design_peak_v) ** 2
    calibration = load_z_calibration(find_project_root(), params.z_calibration_source_run)
    equivalent = math.sqrt(waveform.band_median_psd() * scale) * calibration.k_hz_per_v
    calculated = calculate_noise_preview(
        waveform,
        amplitude_vpp=params.noise_amplitude_vpp,
        bands=params.noise_bands(),
        bin_width_hz=params.analysis_bin_width_hz,
    )

    def compact(values: Any, limit: int = 1200) -> list[float]:
        array = values if len(values) <= limit else values[np.linspace(0, len(values) - 1, limit, dtype=int)]
        return [float(value) for value in array]

    return {
        "design_peak_v": waveform.design_peak_v,
        # 达到设计谱密度（缩放系数=1）所需的注入幅度，供"对齐幅度"一键写入。
        "aligned_amplitude_vpp": 2.0 * waveform.design_peak_v,
        "realized_psd_scale": scale,
        "waveform_rms_v": waveform.rms_v * math.sqrt(scale),
        "equivalent_noise_hz_per_rt_hz": equivalent,
        "z_calibration_k_hz_per_v": calibration.k_hz_per_v,
        "waveform_time_s": compact(calculated["time_s"]),
        "waveform_voltage_v": compact(calculated["voltage_v"]),
        "spectrum_frequency_hz": compact(calculated["frequency_hz"]),
        "target_psd_v2_per_hz": compact(calculated["target_psd_v2_per_hz"]),
        "calculated_psd_v2_per_hz": compact(calculated["calculated_psd_v2_per_hz"]),
    }
