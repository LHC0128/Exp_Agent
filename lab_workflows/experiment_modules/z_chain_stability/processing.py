"""纯函数：双脉冲任意波、逐脉冲分割与积分、I–V 拟合、脉冲角与稳定度统计。

不连接仪器、不读写文件，便于假仪器与离线分析共用。
"""
from __future__ import annotations

import math

import numpy as np


def pulse_waveform(period_us: float, width_us: float, edge_us: float, points: int = 16384) -> np.ndarray:
    """一个周期内的双极 ±π 波形，归一化到 [-1, +1]。

    周期起点为 +π 脉冲上升沿，半周期处为 −π 脉冲上升沿；两个脉冲都用升余弦沿，
    单脉冲等效面积 = (width − edge) × 峰值。返回的首尾都落在 0，便于连续输出。
    """
    if period_us <= 0 or width_us <= 0 or not 0 < edge_us <= width_us / 2:
        raise ValueError("脉冲周期/宽度/沿宽无效")
    t = np.arange(points, dtype=float) * (period_us / points)

    def one(start_us: float, sign: float) -> np.ndarray:
        x = t - start_us
        value = np.zeros(points)
        rising = (x >= 0) & (x < edge_us)
        value[rising] = 0.5 * (1.0 - np.cos(np.pi * x[rising] / edge_us))
        flat = (x >= edge_us) & (x < width_us - edge_us)
        value[flat] = 1.0
        falling = (x >= width_us - edge_us) & (x < width_us)
        value[falling] = 0.5 * (1.0 + np.cos(np.pi * (x[falling] - (width_us - edge_us)) / edge_us))
        return sign * value

    wave = one(0.0, 1.0) + one(period_us / 2.0, -1.0)
    return np.clip(wave, -1.0, 1.0)


def pulse_area_equivalent_us(period_us: float, width_us: float, edge_us: float) -> float:
    """单个升余弦脉冲的等效面积（µs）：(width − edge)。"""
    return width_us - edge_us


def segment_pulses(time_s, current_a, *, period_s: float, width_s: float, tail_s: float,
                   threshold_fraction: float = 0.1) -> list[dict]:
    """把一段实测电流切成一个个脉冲，返回符号、起点、积分面积与基线。

    门限取 |I − base| 最大值的 threshold_fraction；连续超门限的样本归为一个脉冲。
    积分从脉冲起点到 起点+tail_s（覆盖退出沿与 RL 拖尾），基线取所有脉冲窗外样本的中位数。
    """
    t = np.asarray(time_s, dtype=float)
    i = np.asarray(current_a, dtype=float)
    if t.size != i.size or t.size < 8:
        return []
    base = float(np.median(i))
    peak = float(np.max(np.abs(i - base))) if i.size else 0.0
    if not math.isfinite(peak) or peak <= 0:
        return []
    active = np.abs(i - base) > threshold_fraction * peak
    pulses: list[dict] = []
    index = 0
    while index < active.size:
        if not active[index]:
            index += 1
            continue
        start = index
        while index < active.size and active[index]:
            index += 1
        # 取首次越过门限的样本作为脉冲起点；积分窗覆盖完整脉冲与尾场。
        onset = float(t[start])
        stop = onset + min(tail_s, period_s)
        window = (t >= onset) & (t < stop)
        if not np.any(window):
            continue
        q = float(np.trapezoid(i[window] - base, t[window]))
        pulses.append(dict(onset_s=onset, q_a_s=q, i_base_a=base, peak_a=float(np.max(np.abs(i[window] - base)))))
    return pulses


def pulse_angle_pi(q_a_s: float, coil_constant_hz_per_ma: float) -> float:
    """θ/π = 2·K[Hz/mA]·1000·Q[A·s]。"""
    return 2.0 * coil_constant_hz_per_ma * 1000.0 * float(q_a_s)


def iv_fit(volts, currents) -> dict:
    """最小二乘 I = k·V + b；返回 k(A/V)、b(A) 与残差。"""
    v = np.asarray(volts, dtype=float)
    i = np.asarray(currents, dtype=float)
    if v.size < 2 or v.size != i.size:
        raise ValueError("I–V 拟合至少需要两个同长度点")
    k, b = np.polyfit(v, i, 1)
    residual = i - (k * v + b)
    return dict(k_a_per_v=float(k), b_a=float(b), residual_a=[float(x) for x in residual])


def allan_deviation(values, dt_s: float) -> tuple[list[float], list[float]]:
    """重叠 Allan 偏差；返回 (平均时间, 偏差)。非均匀时间戳请先重采样。"""
    x = np.asarray(values, dtype=float)
    if x.size < 4 or dt_s <= 0:
        return [], []
    taus, devs = [], []
    max_m = x.size // 2
    m = 1
    while m <= max_m:
        groups = x.size // m
        means = x[: groups * m].reshape(groups, m).mean(axis=1)
        diffs = np.diff(means)
        if diffs.size:
            taus.append(m * dt_s)
            devs.append(float(np.sqrt(0.5 * np.mean(diffs ** 2))))
        m *= 2
    return taus, devs


def drift_rate(values, elapsed_s) -> float:
    """线性漂移率（单位/秒）；不足两点返回 0。"""
    y = np.asarray(values, dtype=float)
    x = np.asarray(elapsed_s, dtype=float)
    if y.size < 2 or x.size != y.size:
        return 0.0
    return float(np.polyfit(x - x[0], y, 1)[0])
