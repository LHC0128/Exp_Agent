"""XYZ 平衡场的线性（V 形）拟合与孤立异常点检测共享算法。

模型：一维切片上的 ``R = |k (s - s0)| + e``（色散零点附近响应取模后的
绝对值线性形式）。拟合分两阶段：先用 L1（最小一乘）粗拟合稳健定位，
再按残差 MAD 迭代剔除高残差点，最后对保留点做最小二乘精修。该流程
对瞬态异常低点（最优控制触发毛刺等）稳健。

异常点检测：对三维 R 网格逐点计算与 4 邻域中位数的偏差，偏差超过
``threshold_scale * 1.4826 * MAD``（并超过绝对下限）的点标记为孤立异常点。
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.optimize import least_squares, linprog

_MAD_SCALE = 1.4826


def _l1_vshape(
    s: np.ndarray,
    r: np.ndarray,
) -> tuple[float, float, float] | None:
    """L1 拟合 ``R = |k (s - s0)| + e``（k>=0），s0 取相邻中点候选网格。

    返回 ``(k, s0, e)``；失败返回 None。候选网格分辨率受扫描步长限制，
    因此仅用于稳健定位，最终零点由后续最小二乘精修给出。
    """
    s = np.asarray(s, dtype=float)
    r = np.asarray(r, dtype=float)
    if s.size != r.size or s.size < 3:
        return None
    order = np.argsort(s)
    s_sorted = s[order]
    r_sorted = r[order]
    candidates = [
        float((s_sorted[i] + s_sorted[i + 1]) / 2.0)
        for i in range(s_sorted.size - 1)
    ]
    candidates += [float(s_sorted[0]), float(s_sorted[-1])]
    n_points = int(r.size)
    best: tuple[float, float, float, float] | None = None
    for s0 in candidates:
        x = np.abs(s - s0)
        # 变量 [k, e, t_0..t_{n-1}]，min sum t
        # 约束：k x_i + e - t_i <= r_i；-k x_i - e - t_i <= -r_i；k >= 0
        c = np.concatenate(([0.0, 0.0], np.ones(n_points)))
        upper_rows = np.zeros((2 * n_points + 1, n_points + 2))
        upper = np.zeros(2 * n_points + 1)
        for i in range(n_points):
            upper_rows[i, 0] = x[i]
            upper_rows[i, 1] = 1.0
            upper_rows[i, 2 + i] = -1.0
            upper[i] = r[i]
            upper_rows[n_points + i, 0] = -x[i]
            upper_rows[n_points + i, 1] = -1.0
            upper_rows[n_points + i, 2 + i] = -1.0
            upper[n_points + i] = -r[i]
        upper_rows[2 * n_points, 0] = -1.0  # -k <= 0
        result = linprog(
            c,
            A_ub=upper_rows,
            b_ub=upper,
            bounds=[(0.0, None), (None, None)] + [(0.0, None)] * n_points,
            method="highs",
        )
        if not result.success or not np.isfinite(result.fun):
            continue
        k = float(result.x[0])
        e = float(result.x[1])
        if best is None or result.fun < best[3]:
            best = (k, s0, e, float(result.fun))
    if best is None:
        return None
    return best[0], best[1], best[2]


def _least_squares_refine(
    s: np.ndarray,
    r: np.ndarray,
    initial: tuple[float, float, float],
) -> tuple[float, float, float]:
    """在 L1 粗零点邻域做细网格最小二乘精修。

    V 形模型在拐点处不可微，通用 LM 精修会失稳；固定 s0 后
    (k, e) 是线性子问题，因此对每个细网格候选用 lstsq 求解并
    取 SSE 最小的候选，稳定且精度受细网格步长控制。
    """
    k0, s0, e0 = initial
    if s.size < 2:
        return k0, s0, e0
    step = float(np.median(np.diff(np.sort(s))))
    half = max(step, abs(s0) * 0.05 + 1e-9)
    candidates = np.linspace(s0 - half, s0 + half, 41)
    ones = np.ones(s.size)
    best: tuple[float, float, float, float] | None = None
    for candidate in candidates:
        x = np.abs(s - candidate)
        matrix = np.column_stack((x, ones))
        coefficients, *_ = np.linalg.lstsq(matrix, r, rcond=None)
        residual = r - matrix @ coefficients
        sse = float(np.sum(residual**2))
        if best is None or sse < best[0]:
            best = (
                sse,
                float(candidate),
                float(coefficients[0]),
                float(coefficients[1]),
            )
    if best is None:
        return k0, s0, e0
    _, s0, k, e = best
    return k, s0, e


def fit_complex_linear_mod(
    s: np.ndarray,
    r: np.ndarray,
    *,
    min_points: int = 5,
    max_iterations: int = 3,
    residual_floor: float = 0.01,
) -> dict[str, Any]:
    """复线性模拟合 ``R = sqrt((a*s + c)^2 + (b*s + d)^2) + e0``。

    模型主体是复线性函数 ``L(s) = (a+bi)s + (c+di)`` 的模，等价于
    12 kHz 复响应在单个扫描轴上的投影 ``|J*s + C|``；``e0`` 是加性
    背景（其他层的响应残余与控制剩余背景的一阶近似）。相比实 V 形，
    它能表示零点附近由复相位干涉产生的圆滑不对称谷形，谷底可以
    不为零。

    零点（模平方的顶点）为 ``s0 = -(a*c + b*d) / (a^2 + b^2)``，
    谷底值为 ``e = e0 + sqrt((a*s0 + c)^2 + (b*s0 + d)^2)``。模型具有
    整体相位旋转冗余（(a,b) 与 (c,d) 同角旋转不变），仅影响
    a/b/c/d 的取值，不影响 s0/e。

    拟合流程与 :func:`fit_vshape_1d` 相同：V 形粗定位初始化，
    MAD 迭代剔点，最小二乘精修。
    """
    s = np.asarray(s, dtype=float)
    r = np.asarray(r, dtype=float)
    result: dict[str, Any] = {
        "success": False,
        "failure_reason": None,
        "mode": "complex_linear_mod",
        "a": np.nan,
        "b": np.nan,
        "c": np.nan,
        "d": np.nan,
        "e0": np.nan,
        "s0": np.nan,
        "e": np.nan,
        "k": np.nan,
        "n_points": int(s.size),
        "n_used": 0,
        "r_squared": np.nan,
        "s0_inside_range": False,
        "left_points": 0,
        "right_points": 0,
        "rejected_points": 0,
    }
    if s.size != r.size:
        result["failure_reason"] = "mismatched_lengths"
        return result
    if not np.all(np.isfinite(s)) or not np.all(np.isfinite(r)):
        result["failure_reason"] = "non_finite"
        return result
    if s.size < min_points:
        result["failure_reason"] = "insufficient_points"
        return result

    # V 形粗定位作为初始化（并沿用其剔点机制）
    coarse = fit_vshape_1d(
        s,
        r,
        min_points=min_points,
        max_iterations=max_iterations,
        residual_floor=residual_floor,
    )
    if not coarse["success"]:
        result["failure_reason"] = f"vshape_init: {coarse['failure_reason']}"
        return result
    a0 = coarse["k"]
    c0 = -coarse["k"] * coarse["s0"]
    b0 = 0.15 * coarse["k"]
    d0 = max(coarse["e"], 1e-4)
    e00 = 0.0

    def model(p: np.ndarray, ss: np.ndarray) -> np.ndarray:
        a, b, c, d, e0 = p
        return np.sqrt((a * ss + c) ** 2 + (b * ss + d) ** 2) + e0

    mask = (
        coarse["final_mask"].copy()
        if coarse.get("final_mask") is not None
        else np.ones(s.size, dtype=bool)
    )
    params = np.asarray([a0, b0, c0, d0, e00], dtype=float)
    for _ in range(max_iterations + 1):
        # 每轮用当前掩码上的 V 形粗拟合重新初始化，避免 LM 陷入
        # 毛刺/冗余参数造成的局部极小。
        coarse = fit_vshape_1d(
            s[mask],
            r[mask],
            min_points=min_points,
            max_iterations=max_iterations,
            residual_floor=residual_floor,
        )
        if coarse["success"]:
            params = np.asarray(
                [
                    coarse["k"],
                    0.15 * coarse["k"],
                    -coarse["k"] * coarse["s0"],
                    max(coarse["e"], 1e-4),
                    0.0,
                ],
                dtype=float,
            )

        def residual(p: np.ndarray) -> np.ndarray:
            return model(p, s[mask]) - r[mask]

        try:
            solution = least_squares(
                residual,
                x0=params,
                method="lm",
                max_nfev=20000,
            )
            params = solution.x
        except Exception:
            result["failure_reason"] = "fit_failed"
            return result
        res = r - model(params, s)
        masked_res = res[mask]
        mad = float(
            np.median(np.abs(masked_res - np.median(masked_res)))
        )
        threshold = max(3.0 * _MAD_SCALE * mad, residual_floor) + 1e-9
        new_mask = mask.copy()
        new_mask[mask] = np.abs(res[mask]) <= threshold
        if (
            new_mask.sum() == mask.sum()
            or int(new_mask.sum()) < min_points
        ):
            break
        mask = new_mask

    a, b, c, d, e0 = (float(value) for value in params)
    denom = a * a + b * b
    if denom < 1e-24 or not np.all(np.isfinite(params)):
        result["failure_reason"] = "degenerate"
        return result
    s0 = -(a * c + b * d) / denom
    valley = float(np.sqrt((a * s0 + c) ** 2 + (b * s0 + d) ** 2))
    e = valley + e0
    used = int(mask.sum())
    if used < min_points:
        result["failure_reason"] = "too_few_valid_points"
        return result
    final_model = (
        np.sqrt((a * s + c) ** 2 + (b * s + d) ** 2) + e0
    )
    residual_total = r[mask] - final_model[mask]
    total = r[mask] - np.mean(r[mask])
    r_squared = 1.0 - float(
        np.sum(residual_total**2) / max(float(np.sum(total**2)), 1e-300)
    )
    result.update(
        {
            "success": True,
            "a": a,
            "b": b,
            "c": c,
            "d": d,
            "e0": e0,
            "s0": s0,
            "e": e,
            "k": float(np.sqrt(denom)),
            "n_used": used,
            "r_squared": r_squared,
            "s0_inside_range": bool(float(s.min()) <= s0 <= float(s.max())),
            "left_points": int(np.sum(s[mask] < s0)),
            "right_points": int(np.sum(s[mask] > s0)),
            "rejected_points": int(np.sum(~mask)),
        }
    )
    return result


def fit_vshape_1d(
    s: np.ndarray,
    r: np.ndarray,
    *,
    min_points: int = 4,
    max_iterations: int = 3,
    residual_floor: float = 0.01,
) -> dict[str, Any]:
    """一维 V 形拟合 ``R = |k (s - s0)| + e``，迭代剔除高残差点。

    参数
    ----
    s: 扫描轴坐标（如 X/Y 电压）。
    r: 对应的 HF2 Demod0 R 均值。
    min_points: 拟合所需的最少点数。
    max_iterations: 异常点剔除迭代次数。
    residual_floor: 剔点残差绝对下限（V）。L1 粗拟合的候选网格与
        真值零点错位时，两翼会留下约 ``k * step / 2`` 的系统残差，
        该下限防止这些正常点被 MAD 阈值误剔。

    返回
    ----
    dict，``success=False`` 时附 ``failure_reason``；成功时包含
    ``k/s0/e/n_used/r_squared/s0_inside_range/left_points/right_points/
    rejected_points``。
    """
    s = np.asarray(s, dtype=float)
    r = np.asarray(r, dtype=float)
    result: dict[str, Any] = {
        "success": False,
        "failure_reason": None,
        "k": np.nan,
        "s0": np.nan,
        "e": np.nan,
        "n_points": int(s.size),
        "n_used": 0,
        "r_squared": np.nan,
        "s0_inside_range": False,
        "left_points": 0,
        "right_points": 0,
        "rejected_points": 0,
    }
    if s.size != r.size:
        result["failure_reason"] = "mismatched_lengths"
        return result
    if not np.all(np.isfinite(s)) or not np.all(np.isfinite(r)):
        result["failure_reason"] = "non_finite"
        return result
    if s.size < min_points:
        result["failure_reason"] = "insufficient_points"
        return result

    mask = np.ones(s.size, dtype=bool)
    k, s0, e = np.nan, np.nan, np.nan
    for _ in range(max_iterations + 1):
        coarse = _l1_vshape(s[mask], r[mask])
        if coarse is None:
            break
        k, s0, e = coarse
        model = np.abs(k * (s - s0)) + e
        residual = r - model
        masked_residual = residual[mask]
        mad = float(
            np.median(
                np.abs(masked_residual - np.median(masked_residual))
            )
        )
        threshold = max(3.0 * _MAD_SCALE * mad, residual_floor) + 1e-9
        new_mask = mask.copy()
        new_mask[mask] = np.abs(residual[mask]) <= threshold
        if (
            new_mask.sum() == mask.sum()
            or int(new_mask.sum()) < min_points
        ):
            break
        mask = new_mask

    if not np.isfinite(k) or not np.isfinite(s0) or not np.isfinite(e):
        result["failure_reason"] = "fit_failed"
        return result
    used = int(mask.sum())
    if used < min_points:
        result["failure_reason"] = "too_few_valid_points"
        return result
    # 最终在保留点上做最小二乘精修，突破 L1 候选网格的分辨率限制
    try:
        k, s0, e = _least_squares_refine(s[mask], r[mask], (k, s0, e))
    except Exception:
        pass
    if not np.isfinite(k) or not np.isfinite(s0) or not np.isfinite(e):
        result["failure_reason"] = "refine_failed"
        return result
    model = np.abs(k * (s - s0)) + e
    residual = r[mask] - model[mask]
    total = r[mask] - np.mean(r[mask])
    r_squared = 1.0 - float(
        np.sum(residual**2) / max(float(np.sum(total**2)), 1e-300)
    )
    result.update(
        {
            "success": True,
            "k": float(k),
            "s0": float(s0),
            "e": float(e),
            "n_used": used,
            "r_squared": float(r_squared),
            "s0_inside_range": bool(float(s.min()) <= s0 <= float(s.max())),
            "left_points": int(np.sum(s[mask] < s0)),
            "right_points": int(np.sum(s[mask] > s0)),
            "rejected_points": int(np.sum(~mask)),
            "final_mask": mask.copy(),
        }
    )
    return result


def detect_grid_outliers(
    r_mean: np.ndarray,
    *,
    threshold_scale: float = 3.0,
    absolute_floor: float = 0.15,
) -> np.ndarray:
    """按 4 邻域中位数偏差标记三维 R 网格中的孤立瞬态异常点。

    返回与 ``r_mean`` 同形的 bool 数组。偏差超过
    ``threshold_scale * 1.4826 * MAD(dev)`` 且超过 ``absolute_floor``
    （V）的点被标记；单点轴（无邻居）不标记。V 形谷壁的斜率会使
    正常点产生 ~0.1 V 量级的邻域偏差，因此绝对下限默认取 0.15 V，
    只标记明显的瞬态毛刺（触发/相位跳变）。
    """
    r = np.asarray(r_mean, dtype=float)
    mask = np.zeros(r.shape, dtype=bool)
    if r.ndim != 3 or min(r.shape) < 1:
        return mask
    deviation = np.full(r.shape, np.nan)
    for zi in range(r.shape[0]):
        for xi in range(r.shape[1]):
            for yi in range(r.shape[2]):
                neighbors: list[float] = []
                if xi > 0:
                    neighbors.append(r[zi, xi - 1, yi])
                if xi < r.shape[1] - 1:
                    neighbors.append(r[zi, xi + 1, yi])
                if yi > 0:
                    neighbors.append(r[zi, xi, yi - 1])
                if yi < r.shape[2] - 1:
                    neighbors.append(r[zi, xi, yi + 1])
                if neighbors:
                    deviation[zi, xi, yi] = abs(
                        r[zi, xi, yi] - float(np.median(neighbors))
                    )
    finite = deviation[np.isfinite(deviation)]
    if finite.size < 2:
        return mask
    mad = float(np.median(np.abs(finite - np.median(finite))))
    scale = _MAD_SCALE * mad
    threshold = max(threshold_scale * scale, absolute_floor)
    return deviation > threshold
