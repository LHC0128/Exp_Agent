"""灵敏度谱计算 — 在灵敏度谱平坦段寻找真实磁力仪灵敏度.

灵敏度谱形状:
- 极低频 (< a few Hz):  1/f 噪声主导，灵敏度高
- 平坦段 (a few Hz ~ HWHM以内): 真实磁力仪本底灵敏度
- 平坦段 (a few Hz ~ HWHM): 真实磁力仪本底灵敏度, 校正保持平坦
- 高频段 (> HWHM): 超出磁力仪带宽, 校正放大噪声, 不可靠

策略: 在 [f_min, f_max] 区间取校正后灵敏度的中位数
  f_min = f_skip_low (默认 3 Hz), 避开 1/f 噪声
  f_max = f_larmor_Hz * flat_frac (默认 1.0), 即 HWHM 带宽内
"""

from dataclasses import dataclass
import numpy as np


@dataclass
class SensitivityResult:
    """灵敏度计算结果."""

    sens_raw: np.ndarray             # 未校正灵敏度谱 (fT/sqrtHz)
    sens_corrected: np.ndarray       # 响应校正后灵敏度谱
    freq: np.ndarray                 # 频率轴 (Hz)
    sens_flat: float                 # 平坦段中位数灵敏度 (fT/sqrtHz)
    flat_fmin: float                 # 平坦段起始频率 (Hz)
    flat_fmax: float                 # 平坦段结束频率 (Hz)
    flat_mask: np.ndarray            # 平坦段频率布尔掩码
    correction_factor: np.ndarray    # 校正因子 = sqrt(1 + (f/f_larmor)^2)


def compute_sensitivity(
    psd: np.ndarray,
    freq: np.ndarray,
    slope_V_per_fT: float,
    f_larmor_Hz: float,
    f_skip_low: float = 3.0,
    flat_frac: float = 1.0,
) -> SensitivityResult:
    """由 PSD 和色散斜率计算灵敏度谱, 在平坦段取中位数.

    Parameters
    ----------
    psd : PSD 数组 (V^2/Hz)
    freq : 频率轴 (Hz)
    slope_V_per_fT : 色散斜率 dY/dB (V/fT)
    f_larmor_Hz : HWHM 线宽 (Hz) — 即磁力仪响应带宽
    f_skip_low : 低频截止 (Hz), 跳过 1/f 噪声区
    flat_frac : 平坦段上限 = flat_frac * f_larmor_Hz, 默认 1.0=全带宽

    Returns
    -------
    SensitivityResult
    """
    abs_slope = abs(slope_V_per_fT) if abs(slope_V_per_fT) > 1e-30 else 1e-30

    # 原始灵敏度
    sens_raw = np.sqrt(np.maximum(psd, 0)) / abs_slope

    # 响应曲线校正: 洛伦兹型滚降补偿
    if f_larmor_Hz > 1e-9:
        correction = np.sqrt(1.0 + (freq / f_larmor_Hz) ** 2)
    else:
        correction = np.ones_like(freq)
    sens_corrected = sens_raw * correction

    # 平坦段: [f_skip_low, flat_frac * f_larmor_Hz]
    # 避开 1/f 低频区和仪器响应滚降的高频区
    f_max = flat_frac * f_larmor_Hz if f_larmor_Hz > 1e-9 else np.inf
    flat_mask = (freq >= f_skip_low) & (freq <= f_max)

    sens_in_flat = sens_corrected[flat_mask]
    if len(sens_in_flat) > 0:
        sens_flat = float(np.median(sens_in_flat))
    else:
        # 回退: 不用 f_max 限制, 只用 f_skip_low
        fallback_mask = freq >= f_skip_low
        sens_flat = float(np.median(sens_corrected[fallback_mask]))
        flat_mask = fallback_mask
        f_max = float(freq[-1])

    return SensitivityResult(
        sens_raw=sens_raw,
        sens_corrected=sens_corrected,
        freq=freq,
        sens_flat=sens_flat,
        flat_fmin=f_skip_low,
        flat_fmax=f_max,
        flat_mask=flat_mask,
        correction_factor=correction,
    )
