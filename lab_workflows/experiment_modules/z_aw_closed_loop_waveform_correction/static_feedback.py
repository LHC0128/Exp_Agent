"""静态电流拟合、周期对齐和理论量程；纯数值，不连接仪器。"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy.optimize import minimize_scalar

from ...current_feedback import CurrentCouplingCalibration
from ...control_sources import applied_from_voltage


@dataclass(frozen=True)
class StaticFit:
    """全部直流标定点的带截距最小二乘结果。"""

    gain_a_per_v: float
    intercept_a: float
    r_squared: float
    voltage_v: np.ndarray
    current_a: np.ndarray


def scope_range(voltage: np.ndarray, divisions: int, headroom: float) -> tuple[float, float]:
    """按峰峰值和余量向上选择 10 mV/div 至 10 V/div 的 1/2/5 档，并居中。"""
    minimum, maximum = float(voltage.min()), float(voltage.max())
    required = (maximum - minimum) * headroom / divisions
    scales = np.asarray([0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0])
    index = int(np.searchsorted(scales, required, side="left"))
    if index == scales.size:
        raise ValueError(f"所需示波器量程 {required:.6g} V/div 超过 10 V/div")
    return float(scales[index]), -(minimum + maximum) / 2


def rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(values ** 2)))


def fit_static_current(calibration: CurrentCouplingCalibration) -> StaticFit:
    """使用原标定全部正反向记录，不按共振拟合结果筛选电流点。"""
    curves = calibration.payload["curves"]
    voltage = np.asarray([row["z_bias_v"] for row in curves], dtype=float)
    current = np.asarray([row["current_a"] for row in curves], dtype=float)
    if voltage.size < 2 or not np.all(np.isfinite([voltage, current])):
        raise ValueError("静态标定至少需要两个有限的电压、电流点")
    if np.ptp(voltage) == 0 or np.ptp(current) == 0:
        raise ValueError("静态标定的电压和电流必须具有非零跨度")
    gain, intercept = np.linalg.lstsq(
        np.column_stack((voltage, np.ones(voltage.size))), current, rcond=None
    )[0]
    if gain == 0:
        raise ValueError("静态标定斜率不能为零")
    residual = current - (gain * voltage + intercept)
    r_squared = 1 - np.sum(residual ** 2) / np.sum((current - current.mean()) ** 2)
    return StaticFit(float(gain), float(intercept), float(r_squared), voltage, current)


def average_complete_cycles(
    time_s: np.ndarray, current_a: np.ndarray, grid_s: np.ndarray, period_s: float,
) -> np.ndarray:
    """按 CH4 时间参考提取完整周期；只在采集范围内插值，不外推边缘。"""
    grid = grid_s - grid_s[0]
    dt = float(np.median(np.diff(time_s)))
    first = int(np.ceil(time_s[0] / period_s))
    # 最后一个采样代表一个采样间隔，允许窗口恰好以 endpoint=False 结束。
    stop = int(np.floor((time_s[-1] + dt * (1 + 1e-9)) / period_s))
    cycles = []
    for index in range(first, stop):
        start = index * period_s
        mask = (time_s >= start) & (time_s < start + period_s)
        phase = time_s[mask] - start
        if phase.size < 2:
            raise ValueError("完整周期内采样不足两个点")
        cycles.append(np.interp(grid, phase, current_a[mask], period=period_s))
    if not cycles:
        raise ValueError("采集窗口没有完整任意波周期")
    return np.mean(cycles, axis=0)


def align_cycle(measured: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, float]:
    """寻找正向读取 measured[n+shift] 的循环平移；只改时间，不改幅度。"""
    count = target.size
    # 整数平移不改变测量能量，最大相关等价于最小平方误差。
    correlation = np.fft.irfft(np.conj(np.fft.rfft(target)) * np.fft.rfft(measured), n=count)
    candidates = np.flatnonzero(np.isclose(correlation, correlation.max(), rtol=1e-12, atol=0))
    signed = (candidates + count // 2) % count - count // 2
    integer = int(signed[np.argmin(np.abs(signed))])
    grid = np.arange(count, dtype=float)

    def shifted(shift: float) -> np.ndarray:
        return np.interp(grid + shift, grid, measured, period=count)

    def loss(shift: float) -> float:
        return float(np.sum((target - shifted(shift)) ** 2))

    refined = minimize_scalar(loss, bounds=(integer - 1, integer + 1), method="bounded",
                              options={"xatol": 1e-6})
    shift = float(refined.x) if refined.fun < loss(integer) else float(integer)
    shift = (shift + count / 2) % count - count / 2
    return shifted(shift), shift
