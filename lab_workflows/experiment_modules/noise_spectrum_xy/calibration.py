"""XY 噪声谱 PSD 脊线的鲁棒线性标定。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import theilslopes


@dataclass(frozen=True, slots=True)
class RobustLinearCalibration:
    """鲁棒线性标定结果。"""

    slope_hz_per_v: float
    intercept_hz: float
    inlier_mask: np.ndarray
    residual_sigma_hz: float


def fit_robust_linear_calibration(
    amplitudes_v,
    peak_frequencies_hz,
    *,
    rejection_sigma: float = 3.0,
    max_iterations: int = 8,
    min_inliers: int = 6,
) -> RobustLinearCalibration:
    """拟合 ``frequency = slope * amplitude + intercept`` 并剔除离群峰。

    相同包络电压处先只取一个峰频率中位数，用于 Theil-Sen 鲁棒初值，避免
    固定频率干扰形成的竖直峰簇因点数较多而主导拟合。随后在全部候选点上按
    MAD 估计残差尺度，迭代筛选并以最小二乘更新最终斜率和截距。
    """

    x = np.asarray(amplitudes_v, dtype=float).reshape(-1)
    y = np.asarray(peak_frequencies_hz, dtype=float).reshape(-1)
    if x.shape != y.shape:
        raise ValueError("标定包络与峰频率数组长度不一致")
    if rejection_sigma <= 0:
        raise ValueError("离群拒绝阈值必须大于 0")
    if max_iterations < 1:
        raise ValueError("鲁棒标定迭代次数必须至少为 1")

    finite = np.isfinite(x) & np.isfinite(y)
    if np.count_nonzero(finite) < min_inliers:
        raise ValueError(
            f"有效标定峰不足：至少需要 {min_inliers} 个，"
            f"实际只有 {np.count_nonzero(finite)} 个"
        )

    unique_x = np.unique(x[finite])
    if len(unique_x) < 3:
        raise ValueError("有效标定峰至少需要覆盖 3 个不同包络电压")
    representative_y = np.asarray(
        [np.median(y[finite & (x == value)]) for value in unique_x],
        dtype=float,
    )
    slope, intercept, _, _ = theilslopes(representative_y, unique_x)
    slope = float(slope)
    intercept = float(intercept)

    inlier = finite.copy()
    residual_sigma = float("nan")
    for _ in range(max_iterations):
        residual = y - (slope * x + intercept)
        residual_center = float(np.median(residual[inlier]))
        mad = float(np.median(np.abs(residual[inlier] - residual_center)))
        residual_sigma = 1.4826 * mad

        if not np.isfinite(residual_sigma):
            raise ValueError("标定残差尺度无效")
        if residual_sigma == 0:
            new_inlier = finite & np.isclose(
                residual,
                residual_center,
                rtol=0.0,
                atol=np.finfo(float).eps * max(1.0, np.nanmax(np.abs(y))),
            )
        else:
            new_inlier = finite & (
                np.abs(residual - residual_center)
                <= rejection_sigma * residual_sigma
            )

        if np.count_nonzero(new_inlier) < min_inliers:
            raise ValueError(
                "鲁棒筛选后的有效标定峰不足，无法可靠计算斜率和截距"
            )
        if len(np.unique(x[new_inlier])) < 3:
            raise ValueError(
                "鲁棒筛选后的标定峰未覆盖至少 3 个不同包络电压"
            )

        slope, intercept = (
            float(value) for value in np.polyfit(x[new_inlier], y[new_inlier], 1)
        )
        if np.array_equal(new_inlier, inlier):
            inlier = new_inlier
            break
        inlier = new_inlier

    if slope <= 0:
        raise ValueError(f"标定斜率必须为正，实际为 {slope:.6g} Hz/V")

    final_residual = y[inlier] - (slope * x[inlier] + intercept)
    final_center = float(np.median(final_residual))
    residual_sigma = 1.4826 * float(
        np.median(np.abs(final_residual - final_center))
    )
    return RobustLinearCalibration(
        slope_hz_per_v=slope,
        intercept_hz=intercept,
        inlier_mask=inlier,
        residual_sigma_hz=residual_sigma,
    )
