"""线圈驱动链路（信号源 → 功放 → 阻尼电阻 → 线圈 → 采样电阻）的科学算法与绘图，不连接仪器。"""

from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np


def _use_file_backend() -> None:
    """图形只写文件：绘图前统一用 Agg，避免无显示环境退回 Tk 后端时报 TclError。"""
    import matplotlib

    matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))


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


@dataclass(frozen=True, slots=True)
class SineTrace:
    """一帧扫频记录的时域电流与拟合结果，仅供诊断图使用。"""

    frequency_hz: float
    drive_peak_v: float
    time_s: np.ndarray
    current_a: np.ndarray
    fit: SineFit


def point_from_fit(fit: SineFit, frequency_hz: float, drive_peak_v: float) -> TransferPoint:
    """把电流拟合结果折算成传递点。

    相位以同步沿（示波器 t=0）为参考，包含信号源同步与功放的固定延迟。
    """
    current = fit.amplitude
    return TransferPoint(frequency_hz, drive_peak_v, current, current / drive_peak_v,
                         math.degrees(fit.phase_rad), fit.quality)


def transfer_point(frequency_hz: float, drive_peak_v: float, time_s: np.ndarray,
                   sense_v: np.ndarray, sense_resistor_ohm: float) -> TransferPoint:
    """对电流 I = V_sense / R_sense 做正弦拟合并折算传递点。"""
    return point_from_fit(fit_sine(time_s, sense_v / sense_resistor_ohm, frequency_hz),
                          frequency_hz, drive_peak_v)


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


class TruncatedPulse(ValueError):
    """记录窗未包含脉冲的衰减尾部：下降沿指标与含拖尾面积都不可用，该帧应整帧跳过。"""


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
    try:
        f90 = _crossing(time_s, normalized, 0.9, width_s, False)
        f10 = _crossing(time_s, normalized, 0.1, width_s, False)
    except ValueError as exc:
        raise TruncatedPulse(f"记录窗在 {time_s[-1] * 1e6:g} µs 结束，未包含脉宽 {width_s * 1e6:g} µs "
                             f"之后的衰减尾部（{exc}）；加大 PULSE_WINDOW_MULTIPLE 或 PULSE_MIN_WINDOW_US 重采") from exc
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


def _thin(time_s: np.ndarray, values: np.ndarray, max_points: int = 2000) -> tuple[np.ndarray, np.ndarray]:
    """等间隔抽点，只用于显示；不参与拟合、指标或结果文件。"""
    step = max(1, int(math.ceil(len(time_s) / max_points)))
    return time_s[::step], values[::step]


def _width_ticks(widths_us: Sequence[float]) -> list[float]:
    """色条刻度取实测脉宽里的 1-2-5 序列值；不足两个时退回最小与最大。"""
    ordered = sorted(set(widths_us))
    ticks = [value for value in ordered
             if any(math.isclose(value, mantissa * 10.0 ** exponent)
                    for mantissa in (1.0, 2.0, 5.0) for exponent in range(-6, 7))]
    return ticks if len(ticks) >= 2 else [ordered[0], ordered[-1]]


def plot_summary(summary: dict[str, Any], step_traces: Sequence[tuple[float, np.ndarray, np.ndarray]],
                 path: Path) -> Path:
    """零偏/传递函数与脉冲阶跃的汇总图；样式按 docs/plotting_style.md 的 paper 配置。

    面板不写标题（图意由图注说明）；默认无网格；−3 dB 参考线在图例中标为 Reference；
    脉宽等连续量用 viridis 顺序色标 + 色条表示，不堆叠图例；导出时成对生成 PNG 与 PDF。
    """
    import matplotlib as mpl

    from lab_workflows.plotting import (COLOR_GRAY, COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure,
                                        plot_style_context, save_figure, style_legend)

    _use_file_backend()
    transfer = list(summary.get("transfer") or [])
    traces = sorted(step_traces, key=lambda item: item[0])
    with plot_style_context("paper"):
        figure, axes = new_figure(nrows=3 if transfer else 1, ncols=1, height_mm=185 if transfer else 75)
        panels = list(np.atleast_1d(axes).ravel())
        magnitude_axis = panels[0] if transfer else None
        phase_axis = panels[1] if transfer else None
        pulse_axis = panels[2] if transfer else panels[0]
        if transfer:
            frequency = np.array([row["frequency_hz"] for row in transfer])
            admittance = np.array([row["admittance_a_per_v"] for row in transfer]) * 1e3
            magnitude_axis.semilogx(frequency, admittance, "o-", color=COLOR_OPTIMAL, label="Measured")
            reference = admittance[0] / math.sqrt(2.0)
            magnitude_axis.axhline(reference, color=COLOR_GRAY, lw=0.8, ls="--",
                                   label=r"Reference ($-3$ dB)")
            bandwidth = summary.get("bandwidth_3db_hz")
            if bandwidth:
                magnitude_axis.plot([bandwidth], [reference], "D", color=COLOR_TRAD, ms=4, ls="none",
                                    label=f"$f_{{-3\\,\\mathrm{{dB}}}}$ = {bandwidth * 1e-3:.4g} kHz")
            format_axis(magnitude_axis, ylabel=r"$|I_\mathrm{pk}/V_\mathrm{drive,pk}|$ (mA/V)")
            style_legend(magnitude_axis, loc="lower left", fontsize=7)
            phase = np.unwrap(np.radians([row["phase_deg"] for row in transfer])) * 180 / math.pi
            phase_axis.semilogx(frequency, phase, "o-", color=COLOR_OPTIMAL)
            format_axis(phase_axis, xlabel="Frequency (Hz)", ylabel="Phase vs sync edge (deg)")
        if traces:
            widths = [width * 1e6 for width, _, _ in traces]
            cmap = mpl.colormaps["viridis"]
            norm = (mpl.colors.LogNorm(vmin=min(widths), vmax=max(widths))
                    if len(set(widths)) > 1 else None)
            for width, time_s, current in traces:
                pulse_axis.plot(time_s * 1e6, current * 1e3, lw=0.9,
                                color=cmap(norm(width * 1e6)) if norm else COLOR_OPTIMAL)
            if norm is not None:
                bar = figure.colorbar(mpl.cm.ScalarMappable(norm=norm, cmap=cmap), ax=pulse_axis, pad=0.015)
                bar.set_label("Pulse width (µs)")
                bar.set_ticks(_width_ticks(widths))
                bar.ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda value, _: f"{value:g}"))
                bar.ax.yaxis.set_minor_formatter(mpl.ticker.NullFormatter())
        format_axis(pulse_axis, xlabel="Time from sync edge (µs)", ylabel="Coil current (mA)")
        return save_figure(figure, path)


def plot_sine_frames(traces: Sequence[SineTrace], path: Path) -> Path:
    """正弦扫频各帧的时域诊断图：实测电流与拟合正弦，横轴为相对同步沿的周期数。

    横轴取 t·f，各频点都以同步沿为零点，相位差直接表现为过零点相对 0 的平移；
    记录窗长度与偏移取自各帧自身的时间轴，不假设触发居中。
    """
    from lab_workflows.plotting import (COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure,
                                        plot_style_context, save_figure)

    _use_file_backend()
    traces = list(traces)
    if not traces:
        raise ValueError("没有正弦扫频帧")
    ncols = min(4, len(traces))
    nrows = math.ceil(len(traces) / ncols)
    with plot_style_context("paper"):
        figure, axes = new_figure(nrows=nrows, ncols=ncols, width_mm=190, height_mm=14 + 30 * nrows)
        panels = np.atleast_1d(axes).ravel()
        for index, axis in enumerate(panels):
            if index >= len(traces):
                axis.set_visible(False)
                continue
            trace = traces[index]
            time_s, current_a = _thin(trace.time_s, trace.current_a)
            fitted = (trace.fit.amplitude * np.cos(2.0 * math.pi * trace.frequency_hz * time_s + trace.fit.phase_rad)
                      + trace.fit.offset)
            cycles = time_s * trace.frequency_hz
            axis.plot(cycles, current_a * 1e3, color=COLOR_OPTIMAL, lw=0.9, label="Measured")
            axis.plot(cycles, fitted * 1e3, color=COLOR_TRAD, lw=0.9, ls="--", label="Fit")
            frequency_label = (f"{trace.frequency_hz * 1e-3:.4g} kHz" if trace.frequency_hz >= 1e3
                               else f"{trace.frequency_hz:.4g} Hz")
            axis.text(0.03, 0.97,
                      f"f = {frequency_label}\n"
                      f"$|I/V|$ = {trace.fit.amplitude / trace.drive_peak_v * 1e3:.4g} mA/V\n"
                      f"$\\varphi$ = {math.degrees(trace.fit.phase_rad):.4g} deg, $q$ = {trace.fit.quality:.3f}",
                      transform=axis.transAxes, va="top", ha="left", fontsize=5.5,
                      bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.75))
            axis.tick_params(labelleft=index % ncols == 0, labelbottom=index // ncols == nrows - 1)
            format_axis(axis,
                        xlabel="Cycles from sync edge" if index // ncols == nrows - 1 else "",
                        ylabel="Coil current (mA)" if index % ncols == 0 else "")
        if panels.size:
            handles, labels = panels[0].get_legend_handles_labels()
            figure.legend(handles, labels, loc="outside upper center", ncol=2, fontsize=7)
        return save_figure(figure, path)
