"""固定 CH4 时间坐标的谐波选择、辨识质量与正则化更新；不连接仪器。"""

import numpy as np

from .static_feedback import rms


class MeasurementQualityError(RuntimeError):
    """采集不稳定或辨识不可用；不得把异常帧用于学习。"""


def select_harmonics(target, frequency_hz, params):
    """从目标自动选择交流谐波；不静默截断过多谐波，不删除目标直流。"""
    if abs(float(np.mean(target))) > 1e-6 * rms(target):
        raise ValueError("高通谐波模式不能实现目标直流；请使用独立直流磁场路径，不能直接去均值")
    spectrum = np.abs(np.fft.rfft(target))
    candidates = (frequency_hz > 0) & (frequency_hz <= params.error_cutoff_hz)
    candidates[-1] = False  # 不辨识奈奎斯特的退化正弦基。
    selected = np.flatnonzero(candidates & (spectrum >= spectrum[1:].max() * params.harmonic_amplitude_fraction))
    if not selected.size:
        raise ValueError("学习频带内没有目标主要谐波")
    if selected.size > params.maximum_harmonics:
        raise ValueError(f"目标选中 {selected.size} 个谐波，超过 MAXIMUM_HARMONICS；请检查频带和幅度门限")
    return selected


def harmonic_basis(count, bins):
    """每个谐波按 cos、sin 排列，系数单位为峰值 V 或 A。"""
    phase = 2 * np.pi * np.arange(count)[:, None] * np.asarray(bins)[None, :] / count
    return np.stack((np.cos(phase), np.sin(phase)), axis=-1).reshape(count, -1)


def coefficients(values, basis):
    return np.asarray(values) @ basis * (2.0 / basis.shape[0])


def require_repeatability(reference, candidate, basis, params, target_rms):
    """逐谐波复系数检查重载漂移；同时检查全波形，不能逐帧重新对齐。"""
    a = coefficients(reference["measured"], basis).reshape(-1, 2)
    b = coefficients(candidate["measured"], basis).reshape(-1, 2)
    relative = np.linalg.norm(a - b, axis=1) / np.maximum(np.linalg.norm(a, axis=1), target_rms * 1e-3)
    # CH3 的 ADC 量化和宽带噪声会让逐点波形 RMS 明显变化；闭环只在
    # 被学习谐波的复系数上判断稳定性。掉帧仍由 _measure_round 的 RMS 门限单独拦截。
    if np.max(relative) > params.harmonic_repeatability_fraction:
        raise MeasurementQualityError("重复加载后的谐波幅度/相位不稳定，停止辨识和更新；请检查 Burst、触发与接线")


def acceptance_margin(reference, candidate, params):
    """保守使用逐帧评分标准误和固定最小改善门限，避免追逐测量噪声。"""
    return float(max(params.minimum_improvement, 3 * np.hypot(reference["score_sem"], candidate["score_sem"])))


def bounded_update(jacobian, error, basis, regularization, damping, maximum_step_v):
    """SVD 正则化局部逆；只缩放完整增量，保持各谐波相对相位。"""
    left, singular, right = np.linalg.svd(jacobian, full_matrices=False)
    if not np.all(np.isfinite(singular)) or singular[-1] <= singular[0] * 1e-4:
        raise MeasurementQualityError("谐波响应矩阵病态或无可辨识增益，停止更新")
    scale = regularization * singular[0]
    delta = right.T @ ((singular / (singular ** 2 + scale ** 2)) * (left.T @ coefficients(error, basis)))
    increment = damping * (basis @ delta)
    peak = float(np.max(np.abs(increment)))
    return increment * min(1.0, maximum_step_v / max(peak, 1e-30))
