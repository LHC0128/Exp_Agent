"""屏蔽模型下的 Z 脉冲总转角（相对示波器确认的干净 π 波形）。

物理屏蔽取 s_f*=0.544、τe*=46.9 µs（10_tail_compensation/结论.md §4.3 示波器确认）；
驱动波形按给定的补偿参数 (w, s_f, τe) 生成，与 rf-phase-response 写入的波形同源。
返回 (脉冲内面积, 脉冲后残余面积, 总面积)，都除以干净 π（8.03 / 0.544 / 46.9）的总面积。

用法：在开发目录 D:/Code/exp_agent 下
    agent_exp_env/Scripts/python.exe <本文件> 8.03 0.575 38.3
"""
from __future__ import annotations

import sys

import numpy as np

from lab_workflows.experiment_modules.bperp_null.z_waveform import exact_tail, tail_compensated_waveform

PERIOD_US = 200.0
POINTS = 20000
EDGE_US = 1.0
PEAK_MA = 190.0
SHIELD_SF = 0.544
SHIELD_TAU_E = 46.9


def _areas(w: float, sf: float, te: float) -> tuple[float, float]:
    tail_a, tau_c = exact_tail(w, EDGE_US, sf, te)
    wave, _ = tail_compensated_waveform(PERIOD_US, w, EDGE_US, tail_a, tau_c, POINTS)
    current = wave * PEAK_MA
    k = np.fft.rfftfreq(POINTS, PERIOD_US / POINTS) * 2 * np.pi
    slow = np.fft.irfft(np.fft.rfft(current) / (1 + 1j * k * SHIELD_TAU_E), POINTS)
    field = SHIELD_SF * current + (1 - SHIELD_SF) * slow
    t = np.arange(POINTS) * PERIOD_US / POINTS
    inside, after = t < w, (t >= w) & (t < PERIOD_US / 2)
    return float(np.trapezoid(field[inside], t[inside])), float(np.trapezoid(field[after], t[after]))


_REF = sum(_areas(8.03, 0.544, 46.9))


def relative_area(w: float, sf: float, te: float) -> tuple[float, float, float]:
    inside, after = _areas(w, sf, te)
    return inside / _REF, after / _REF, (inside + after) / _REF


if __name__ == "__main__":
    w, sf, te = map(float, sys.argv[1:4])
    print("in-pulse %.4f  post %.4f  total %.4f" % relative_area(w, sf, te))
