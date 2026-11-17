"""射频相位粗扫的纯计算与作图：角度网格、峰值选取、抛物线精化；不连接仪器。

读出的幅度满足 ``R(φ) ∝ |cos(φ − ψ0)|``：曲线在 0–360° 内有两个等高的峰、相隔 180°，
因此只扫 0–180° 即可（另一半是重复的）。判据（π 宽度）与相位无关，粗扫只为把信噪比放到
峰上。峰不在端点时用相邻三点做抛物线插值，给出亚步进的精化角度。
"""
from __future__ import annotations

import math

# 角度一律按这个精度取整，避免 0.1+0.2 式的浮点尾数进 CSV 与摘要。
ROUND_DIGITS = 9


def phase_grid(start_deg: float, stop_deg: float, step_deg: float) -> list[float]:
    """含两端的角度网格（度）；步进除不尽时按实际间隔补上终点。"""
    for name, value in (("Y_RF_PHASE_START_DEG", start_deg), ("Y_RF_PHASE_STOP_DEG", stop_deg),
                        ("Y_RF_PHASE_STEP_DEG", step_deg)):
        if not math.isfinite(value):
            raise ValueError(f"{name} 必须是有限数")
    if step_deg <= 0:
        raise ValueError("Y_RF_PHASE_STEP_DEG 必须为正")
    if stop_deg < start_deg:
        raise ValueError("Y_RF_PHASE_STOP_DEG 不能小于 Y_RF_PHASE_START_DEG")
    count = int(math.floor((stop_deg - start_deg) / step_deg + 1e-9))
    angles = [round(start_deg + index * step_deg, ROUND_DIGITS) for index in range(count + 1)]
    if angles[-1] < stop_deg - 1e-9:
        angles.append(round(stop_deg, ROUND_DIGITS))
    if not angles:
        raise ValueError("射频相位粗扫角度列表为空，请检查起点、终点与步进")
    return angles


def parabolic_peak(x_left: float, y_left: float, x_mid: float, y_mid: float,
                   x_right: float, y_right: float) -> float | None:
    """三点插值抛物线的顶点横坐标；顶点落在两端之间才返回，否则不做精化。

    用一般（不等间距）拉格朗日形式解 y'(x)=0，不假设三点等距。
    """
    if not x_left < x_mid < x_right:
        return None
    if not all(math.isfinite(value) for value in (y_left, y_mid, y_right)):
        return None
    weights, weighted = 0.0, 0.0
    for xi, yi, xj, xk in ((x_left, y_left, x_mid, x_right),
                           (x_mid, y_mid, x_left, x_right),
                           (x_right, y_right, x_left, x_mid)):
        denominator = (xi - xj) * (xi - xk)
        weights += yi / denominator
        weighted += yi * (xj + xk) / denominator
    if abs(weights) < 1e-18:
        return None
    peak = weighted / (2.0 * weights)
    if not math.isfinite(peak) or not x_left < peak < x_right:
        return None
    return round(peak, ROUND_DIGITS)


def choose_peak(points: list[dict]) -> dict:
    """取 R 最大的角度；峰在内部时给出抛物线精化角度与整条曲线。"""
    if not points:
        raise ValueError("射频相位粗扫没有可用读数")
    best = max(range(len(points)), key=lambda index: points[index]["r_mean_v"])
    chosen = float(points[best]["phase_deg"])
    refined = None
    if 0 < best < len(points) - 1:
        refined = parabolic_peak(points[best - 1]["phase_deg"], points[best - 1]["r_mean_v"],
                                 chosen, points[best]["r_mean_v"],
                                 points[best + 1]["phase_deg"], points[best + 1]["r_mean_v"])
    return dict(chosen_deg=chosen, refined_deg=refined,
                R_chosen=float(points[best]["r_mean_v"]),
                R_curve=[dict(phase_deg=float(item["phase_deg"]), R_v=float(item["r_mean_v"]))
                         for item in points])


def plot_phase_scan(path, points: list[dict], record: dict) -> None:
    """R 随射频相位的散点（带误差棒）与选中点标注；图中文字用英文。"""
    import matplotlib

    matplotlib.use("Agg")
    from ...plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    import numpy as np

    angles = np.array([item["phase_deg"] for item in points], dtype=float)
    values = np.array([item["r_mean_v"] for item in points], dtype=float)
    errors = np.array([item["r_sem_v"] for item in points], dtype=float)
    chosen = float(record["chosen_deg"])
    refined = record.get("refined_deg")
    with plot_style_context("paper"):
        figure, axis = new_figure()
        axis.errorbar(angles, values, yerr=errors, fmt="o-", ms=4, lw=1, capsize=2,
                      color=COLOR_OPTIMAL, label="MFLI R (Demod0)")
        axis.plot([chosen], [float(record["R_chosen"])], "o", ms=9, mfc="none", mew=1.2,
                  color=COLOR_TRAD, label=f"chosen {chosen:g} deg")
        if refined is not None:
            axis.axvline(float(refined), color="0.4", ls=":", lw=0.9,
                         label=f"refined {float(refined):.2f} deg")
        format_axis(axis, xlabel="RF phase (deg)", ylabel="R (V)")
        axis.legend(loc="best", fontsize=7)
        save_figure(figure, path)
