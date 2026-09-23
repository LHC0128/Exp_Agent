"""已知谱噪声波形生成与注入真值链加载；全部纯函数，不连接硬件。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml
from scipy.signal import welch

from .models import NOISE_POINTS

# 生成自检：带内中位 PSD 相对设计值的容差（随机相位实现的谱波动）。
_PSD_TOLERANCE = 0.25


@dataclass(slots=True)
class KnownNoiseWaveform:
    """归一化噪声波形及其设计谱元数据。"""

    normalized: np.ndarray          # [-1, 1]，可直接上传任意波
    design_psd_v2_per_hz: np.ndarray  # 与 line_frequencies_hz 对齐的设计谱（0 表示带外）
    line_frequencies_hz: np.ndarray
    sample_rate_sa_s: float
    line_spacing_hz: float
    design_peak_v: float            # 设计波形（未归一化）的峰值电压
    rms_v: float                    # 设计波形的 RMS 电压
    seed: int

    def band_median_psd(self) -> float:
        """设计谱带内中位值（V²/Hz），用于预览等效噪声。"""
        in_band = self.design_psd_v2_per_hz > 0
        if not in_band.any():
            raise ValueError("设计谱没有带内谱线")
        return float(np.median(self.design_psd_v2_per_hz[in_band]))


def calculate_noise_preview(
    waveform: KnownNoiseWaveform,
    *,
    amplitude_vpp: float,
    bands: list[tuple[float, float, float]],
    bin_width_hz: float,
) -> dict[str, np.ndarray | float | int]:
    """返回实际播放波形、目标谱和由波形计算的 Welch 谱。"""
    if not math.isfinite(amplitude_vpp) or amplitude_vpp <= 0:
        raise ValueError("噪声注入幅度必须为正")
    if not math.isfinite(bin_width_hz) or bin_width_hz <= 0:
        raise ValueError("频率格间距必须为正")
    voltage = waveform.normalized * (amplitude_vpp / 2.0)
    nperseg = min(NOISE_POINTS, max(2, int(math.ceil(waveform.sample_rate_sa_s / bin_width_hz))))
    frequencies, calculated = welch(
        voltage,
        fs=waveform.sample_rate_sa_s,
        nperseg=nperseg,
        noverlap=nperseg // 2,
        window="hann",
        detrend="constant",
        scaling="density",
    )
    scale = (amplitude_vpp / 2.0 / waveform.design_peak_v) ** 2
    target = np.zeros_like(frequencies)
    for start, stop, psd in bands:
        target[(frequencies >= start) & (frequencies <= stop)] = psd * scale
    return {
        "time_s": np.arange(voltage.size, dtype=float) / waveform.sample_rate_sa_s,
        "voltage_v": voltage,
        "frequency_hz": frequencies,
        "target_psd_v2_per_hz": target,
        "calculated_psd_v2_per_hz": calculated,
        "nperseg": nperseg,
        "realized_psd_scale": scale,
    }


def _design_spectrum(line_frequencies_hz: np.ndarray,
                     bands: list[tuple[float, float, float]]) -> np.ndarray:
    psd = np.zeros(line_frequencies_hz.size)
    occupied = np.zeros(line_frequencies_hz.size, bool)
    for start, stop, level in bands:
        mask = (line_frequencies_hz >= start) & (line_frequencies_hz <= stop)
        if overlap := (occupied & mask).any():
            raise ValueError(f"噪声段 [{start:g}, {stop:g}] Hz 与已有段重叠")
        occupied |= mask
        psd[mask] = level
    return psd


def generate_known_noise_waveform(
    *,
    repeat_freq_hz: float,
    bands: list[tuple[float, float, float]],
    seed: int,
) -> KnownNoiseWaveform:
    """生成分段平顶谱的周期伪噪声；同参数同种子完全可复现。

    线幅度 A_k = sqrt(2·S·Δf) 使单边 PSD 等于设计谱；irfft 的归一化
    （X[k] = n/2·A_k·e^{iφ}）保证 x[j] = Σ A_k·cos(2πf_k t_j + φ_k)。
    """
    if not math.isfinite(repeat_freq_hz) or repeat_freq_hz <= 0:
        raise ValueError("波形重复频率必须为有限正数")
    if not bands:
        raise ValueError("至少需要一个噪声谱段")
    sample_rate = NOISE_POINTS * repeat_freq_hz
    nyquist = sample_rate / 2.0
    for start, stop, _ in bands:
        if not (0 < start < stop < nyquist):
            raise ValueError(f"噪声段 [{start:g}, {stop:g}] Hz 必须位于 (0, {nyquist:g}) Hz 内")
    line_frequencies = np.fft.rfftfreq(NOISE_POINTS, 1.0 / sample_rate)
    design_psd = _design_spectrum(line_frequencies, bands)
    amplitudes = np.sqrt(2.0 * design_psd * repeat_freq_hz)
    rng = np.random.default_rng(seed)
    spectrum = (NOISE_POINTS / 2.0) * amplitudes * np.exp(
        1j * rng.uniform(0.0, 2.0 * np.pi, line_frequencies.size))
    spectrum[0] = 0.0
    waveform = np.fft.irfft(spectrum, NOISE_POINTS)
    rms_v = float(np.sqrt(np.mean(waveform**2)))
    if rms_v <= 0:
        raise ValueError("生成波形 RMS 为零")
    design_peak_v = float(np.max(np.abs(waveform)))
    if design_peak_v <= 0:
        raise ValueError("生成波形峰值为零")
    # 与主分析一致的 Welch 分段（bin 宽 50 Hz 量级）做带内自检。
    nperseg = max(2, int(math.ceil(sample_rate / 50.0)))
    nperseg = min(nperseg, NOISE_POINTS)
    _, psd_check = welch(waveform, fs=sample_rate, nperseg=nperseg,
                         noverlap=nperseg // 2, window="hann",
                         detrend="constant", scaling="density")
    check_frequencies = np.fft.rfftfreq(nperseg, 1.0 / sample_rate)
    for start, stop, level in bands:
        in_band = (check_frequencies >= start) & (check_frequencies <= stop)
        if not in_band.any() or psd_check[in_band].min() <= 0:
            raise ValueError(f"段 [{start:g}, {stop:g}] Hz 内没有可用的自检频率格")
        median = float(np.median(psd_check[in_band]))
        if abs(median - level) > _PSD_TOLERANCE * level:
            raise ValueError(
                f"生成波形段 [{start:g}, {stop:g}] Hz 带内中位 PSD {median:.4g} "
                f"偏离设计值 {level:.4g} 超过 {_PSD_TOLERANCE:.0%}"
            )
    normalized = waveform / design_peak_v
    if float(np.max(np.abs(normalized))) > 1.0 + 1e-12:
        raise ValueError("归一化波形超出 [-1, 1]")
    return KnownNoiseWaveform(
        normalized=normalized,
        design_psd_v2_per_hz=design_psd,
        line_frequencies_hz=line_frequencies,
        sample_rate_sa_s=sample_rate,
        line_spacing_hz=repeat_freq_hz,
        design_peak_v=design_peak_v,
        rms_v=rms_v,
        seed=seed,
    )


@dataclass(frozen=True, slots=True)
class ZFrequencyCalibration:
    """Bell-Bloom Z 场频率标定的斜率；共振中心由主磁场决定，不进入真值链。"""

    k_hz_per_v: float
    uncertainty_hz_per_v: float
    source_run: str


def load_z_calibration(root: Path, run_name: str) -> ZFrequencyCalibration:
    path = (root / "data" / "Bell_Bloom_Z_Field_Calibration" / run_name
            / "results" / "analysis.yaml").resolve()
    source_root = (root / "data" / "Bell_Bloom_Z_Field_Calibration").resolve()
    if source_root not in path.parents:
        raise ValueError("K_Z 标定路径超出结果目录")
    if not path.is_file():
        raise FileNotFoundError(f"未找到 K_Z 标定结果: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if payload.get("experiment_id") != "bell-bloom-z-field-calibration":
        raise ValueError(f"{run_name} 不是 Bell Bloom Z 场标定运行")
    if payload.get("success") is not True:
        raise ValueError(f"K_Z 标定运行 {run_name} 未成功")
    slope = payload.get("K_Z_Hz_per_V")
    if not isinstance(slope, (int, float)) or not math.isfinite(float(slope)) or slope <= 0:
        raise ValueError(f"K_Z 标定运行 {run_name} 缺少有效斜率")
    uncertainty = float("nan")
    linear = payload.get("linear_fit") or {}
    values = linear.get("uncertainties")
    if isinstance(values, list) and values and isinstance(values[0], (int, float)):
        uncertainty = float(values[0])
    return ZFrequencyCalibration(float(slope), uncertainty, run_name)


@dataclass(frozen=True, slots=True)
class ZCoilTransfer:
    """Z 线圈电流频响的幅值归一化模型 H_norm(f) = |H(f)|/|H(f_ref)|。"""

    reference_hz: float
    magnitude: np.ndarray            # 与 frequencies_hz 对齐的 |H|（A/V）
    frequencies_hz: np.ndarray
    source_run: str

    def normalized_gain(self, frequencies_hz: np.ndarray) -> np.ndarray:
        """对数插值 |H(f)|/|H(f_ref)|；外推端用最近值。"""
        target = np.asarray(frequencies_hz, float)
        log_gain = np.interp(np.log(np.maximum(target, 1e-9)),
                              np.log(self.frequencies_hz), self.magnitude)
        return log_gain / self.magnitude[0]


def load_z_coil_transfer(root: Path, run_name: str) -> ZCoilTransfer:
    path = (root / "data" / "Z_Coil_Current_Frequency_Response" / run_name
            / "results" / "frequency_response.npz").resolve()
    source_root = (root / "data" / "Z_Coil_Current_Frequency_Response").resolve()
    if source_root not in path.parents:
        raise ValueError("频响路径超出结果目录")
    if not path.is_file():
        raise FileNotFoundError(f"未找到 Z 线圈频响结果: {path}")
    with np.load(path, allow_pickle=False) as data:
        frequencies = np.asarray(data["frequency_hz"], float)
        magnitude = np.asarray(data["transfer_magnitude_a_per_v_mean"], float)
        reliable = np.asarray(data["reliable"]).astype(bool)
    order = np.argsort(frequencies)
    frequencies, magnitude, reliable = frequencies[order], magnitude[order], reliable[order]
    usable = reliable & np.isfinite(frequencies) & np.isfinite(magnitude) & (frequencies > 0) & (magnitude > 0)
    if usable.sum() < 2:
        raise ValueError(f"频响运行 {run_name} 可靠点不足 2 个，无法归一化")
    return ZCoilTransfer(
        reference_hz=float(frequencies[usable][0]),
        magnitude=magnitude[usable],
        frequencies_hz=frequencies[usable],
        source_run=run_name,
    )
