"""线圈驱动链路（信号源 → 功放 → 阻尼电阻 → 线圈 → 采样电阻）的科学算法与绘图，不连接仪器。"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np


@dataclass(frozen=True, slots=True)
class SineFit:
    """v(t) = amplitude·cos(2πft + phase) + offset 的最小二乘拟合。"""

    amplitude: float
    phase_rad: float
    offset: float
    residual_rms: float

    @property
    def quality(self) -> float:
        """1 − 残差 RMS / 正弦 RMS；纯正弦为 1。"""
        return 1.0 - self.residual_rms / (self.amplitude / math.sqrt(2.0))


def fit_sine(time_s: np.ndarray, values: np.ndarray, frequency_hz: float) -> SineFit:
    omega = 2.0 * math.pi * frequency_hz
    design = np.column_stack([np.cos(omega * time_s), np.sin(omega * time_s), np.ones_like(time_s)])
    (a, b, c), *_ = np.linalg.lstsq(design, values, rcond=None)
    residual = values - design @ np.array([a, b, c])
    return SineFit(float(math.hypot(a, b)), float(math.atan2(-b, a)), float(c),
                   float(np.sqrt(np.mean(residual ** 2))))


@dataclass(frozen=True, slots=True)
class TransferPoint:
    frequency_hz: float
    drive_peak_v: float
    current_peak_a: float
    admittance_a_per_v: float
    phase_deg: float
    fit_quality: float


def transfer_point(frequency_hz: float, drive_peak_v: float, time_s: np.ndarray,
                   sense_v: np.ndarray, sense_resistor_ohm: float) -> TransferPoint:
    """相位以同步沿（示波器 t=0）为参考，包含信号源同步与功放的固定延迟。"""
    fit = fit_sine(time_s, sense_v, frequency_hz)
    current = fit.amplitude / sense_resistor_ohm
    return TransferPoint(frequency_hz, drive_peak_v, current, current / drive_peak_v,
                         math.degrees(fit.phase_rad), fit.quality)


def bandwidth_3db(points: Sequence[TransferPoint]) -> float | None:
    """以最低频点为直流参考，返回幅度首次降到 1/√2 的频率（对数插值）；扫频内未降到则为 None。"""
    ordered = sorted(points, key=lambda point: point.frequency_hz)
    reference = ordered[0].admittance_a_per_v
    threshold = reference / math.sqrt(2.0)
    for previous, current in zip(ordered, ordered[1:]):
        if current.admittance_a_per_v < threshold <= previous.admittance_a_per_v:
            fraction = (previous.admittance_a_per_v - threshold) / (
                previous.admittance_a_per_v - current.admittance_a_per_v)
            log_f = math.log10(previous.frequency_hz) + fraction * (
                math.log10(current.frequency_hz) - math.log10(previous.frequency_hz))
            return 10.0 ** log_f
    return None


def _crossing(time_s: np.ndarray, values: np.ndarray, level: float, start: float, rising: bool) -> float:
    """start 之后首次穿越 level 的时刻（线性插值）；找不到抛 ValueError。"""
    index = int(np.searchsorted(time_s, start))
    above = values[index:] >= level if rising else values[index:] <= level
    hits = np.flatnonzero(above)
    if not len(hits):
        raise ValueError(f"t>{start:g} s 内未找到 {level:g} 的穿越")
    k = index + int(hits[0])
    if k == 0:
        return float(time_s[0])
    t0, t1, v0, v1 = time_s[k - 1], time_s[k], values[k - 1], values[k]
    return float(t0 if v1 == v0 else t0 + (level - v0) * (t1 - t0) / (v1 - v0))


@dataclass(frozen=True, slots=True)
class StepMetrics:
    width_s: float
    baseline_a: float
    plateau_a: float
    rise_10_90_s: float
    fall_90_10_s: float
    delay_50_s: float
    overshoot_fraction: float
    peak_a: float
    area_a_s: float


def step_metrics(time_s: np.ndarray, current_a: np.ndarray, width_s: float) -> StepMetrics:
    """单个矩形脉冲（同步沿 t=0 开始、宽 width_s）的电流响应指标。"""
    before = time_s < -0.02 * width_s
    if before.sum() < 10:
        raise ValueError("触发前样本不足，无法确定基线")
    baseline = float(np.median(current_a[before]))
    late = (time_s >= 0.8 * width_s) & (time_s <= 0.97 * width_s)
    if late.sum() < 5:
        raise ValueError("脉冲平台样本不足，请加大采样率")
    plateau = float(np.mean(current_a[late]))
    step = plateau - baseline
    if step == 0.0:
        raise ValueError("脉冲平台与基线相同，未测到电流")
    normalized = (current_a - baseline) / step
    t10 = _crossing(time_s, normalized, 0.1, 0.0, True)
    t50 = _crossing(time_s, normalized, 0.5, 0.0, True)
    t90 = _crossing(time_s, normalized, 0.9, 0.0, True)
    f90 = _crossing(time_s, normalized, 0.9, width_s, False)
    f10 = _crossing(time_s, normalized, 0.1, width_s, False)
    inside = (time_s >= 0.0) & (time_s <= width_s)
    peak = float(np.max(normalized[inside]))
    window = time_s >= -0.02 * width_s
    area = float(np.trapezoid(current_a[window] - baseline, time_s[window]))
    return StepMetrics(width_s, baseline, plateau, t90 - t10, f10 - f90, t50, peak - 1.0,
                       baseline + peak * step, area)


def pulse_area_table(metrics: Sequence[StepMetrics]) -> list[dict[str, float]]:
    """以最宽脉冲的平台电流为名义电流，比较 ∫I dt（含拖尾）与 I_plateau·width。

    线性 RL 回路里含拖尾的面积守恒，面积偏差反映功放压摆/非线性与截断；脉冲是否到达平台另看 peak_fraction。
    短脉冲未到平台时，rise/fall 以本脉冲自身末段为参照，只作诊断；上升时间以最宽脉冲为准。
    """
    reference = max(metrics, key=lambda item: item.width_s)
    rows = []
    for item in sorted(metrics, key=lambda m: m.width_s):
        step = reference.plateau_a - reference.baseline_a
        nominal = step * item.width_s
        rows.append({
            "width_s": item.width_s,
            "area_a_s": item.area_a_s,
            "nominal_area_a_s": nominal,
            "area_deviation_fraction": item.area_a_s / nominal - 1.0,
            "effective_width_s": item.area_a_s / step,
            "rise_10_90_s": item.rise_10_90_s,
            "fall_90_10_s": item.fall_90_10_s,
            "delay_50_s": item.delay_50_s,
            "overshoot_fraction": item.overshoot_fraction,
            "peak_fraction": (item.peak_a - item.baseline_a) / step,
        })
    return rows


def shortest_usable_width(rows: Sequence[dict[str, float]], tolerance: float = 0.02) -> float | None:
    """面积偏差 ≤ tolerance 且峰值达到平台 1−tolerance 的最短实测脉宽；不外推到未测脉宽。"""
    usable = [row["width_s"] for row in rows
              if abs(row["area_deviation_fraction"]) <= tolerance and row["peak_fraction"] >= 1.0 - tolerance]
    return min(usable) if usable else None


def summarize(zero: dict[str, Any], points: Sequence[TransferPoint], steps: Sequence[StepMetrics],
              area_tolerance: float = 0.02) -> dict[str, Any]:
    rows = pulse_area_table(steps) if steps else []
    return {
        "zero_offset": zero,
        "transfer": [asdict(point) for point in points],
        "bandwidth_3db_hz": bandwidth_3db(points) if len(points) > 1 else None,
        "pulse": rows,
        "area_tolerance_fraction": area_tolerance,
        "shortest_usable_pulse_width_s": shortest_usable_width(rows, area_tolerance) if rows else None,
    }


def plot_summary(summary: dict[str, Any], step_traces: Sequence[tuple[float, np.ndarray, np.ndarray]],
                 path: Path) -> Path:
    from lab_workflows.plotting import new_figure, save_figure, set_plot_style

    set_plot_style("paper")
    figure, axes = new_figure(nrows=3, ncols=1, height_mm=190)
    transfer = summary.get("transfer") or []
    if transfer:
        freq = [row["frequency_hz"] for row in transfer]
        axes[0].loglog(freq, [row["admittance_a_per_v"] for row in transfer], "o-")
        bandwidth = summary.get("bandwidth_3db_hz")
        axes[0].set_title("Coil drive transfer" + (f" (-3 dB at {bandwidth:.3g} Hz)" if bandwidth else ""))
        phase = np.unwrap(np.radians([row["phase_deg"] for row in transfer])) * 180 / math.pi
        axes[1].semilogx(freq, phase, "o-")
    axes[0].set_ylabel("|I / V_drive| (A/V)")
    axes[1].set_ylabel("Phase vs sync (deg)")
    axes[1].set_xlabel("Frequency (Hz)")
    for width, time_s, current in step_traces:
        axes[2].plot(time_s * 1e6, current * 1e3, label=f"{width * 1e6:g} us")
    axes[2].set_xlabel("Time from sync edge (us)")
    axes[2].set_ylabel("Coil current (mA)")
    axes[2].set_title("Pulse response")
    if step_traces:
        axes[2].legend(fontsize=6)
    for axis in axes:
        axis.grid(True, which="both", alpha=0.3)
    return save_figure(figure, path, paired=False)
