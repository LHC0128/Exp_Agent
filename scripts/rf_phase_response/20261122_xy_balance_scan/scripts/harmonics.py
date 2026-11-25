"""谐波分解：把一次运行的逐相位 Z(φ) 拟合成 c0 + Σ(c_k e^{ikφ} + c_-k e^{-ikφ})。

与 20261120_rf_vpp_scan 的口径一致（三次谐波 = 削顶的直接产物）；这里多算到 ±3 次，
用来给"响应对称性"一个数：对称的椭圆只有 ±1 次项，削顶与偏置不对称会把能量推到偶次项。
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

ORDERS = (1, 2, 3)


def read_curve(run_dir):
    """读 raw/phase_response.csv 的逐相位平均 Z=x+iy。"""
    path = Path(run_dir) / "raw" / "phase_response.csv"
    phases, values = [], {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                phase = float(row["phase_deg"])
                x_v, y_v = float(row["x_v"]), float(row["y_v"])
            except (KeyError, TypeError, ValueError):
                continue
            phases.append(phase)
            values.setdefault(round(phase, 6), []).append(complex(x_v, y_v))
    order = sorted(values)
    mean = [sum(values[key]) / len(values[key]) for key in order]
    return np.radians(np.array(order)), np.array(mean)


def decompose(phases_rad, values, orders=ORDERS):
    """最小二乘分解，返回 {('c0'|'+k'|'-k'): 系数} 与残差 RMS。"""
    design = [np.ones_like(phases_rad)]
    labels = ["c0"]
    for k in orders:
        design.append(np.exp(1j * k * phases_rad))
        labels.append(f"+{k}")
        design.append(np.exp(-1j * k * phases_rad))
        labels.append(f"-{k}")
    matrix = np.column_stack(design)
    coef, *_ = np.linalg.lstsq(matrix, values, rcond=None)
    residual = values - matrix @ coef
    out = {label: value for label, value in zip(labels, coef)}
    return out, float(np.sqrt(np.mean(np.abs(residual) ** 2)))


def lobe_metrics(phases_deg, magnitudes):
    """两个瓣的峰高与 180° 对称性（用户口径：R(φ) 两处峰等高）。

    先在对角附近找两个谷（60–110° 与 240–290°），把 R(φ) 分成两个 180° 窗口，
    各取最大值；不平衡度 = (P_A − P_B) / 均值。镜像残差 = 各点 |R(φ) − R(φ+180°)| 的均值。
    """
    phases = np.asarray(phases_deg, float)
    values = np.asarray(magnitudes, float)
    order = np.argsort(phases)
    phases, values = phases[order], values[order]

    def dip(lo, hi):
        mask = (phases >= lo) & (phases <= hi)
        return float(phases[mask][np.argmin(values[mask])])

    dip_a, dip_b = dip(60.0, 110.0), dip(240.0, 290.0)
    interp = lambda target: float(np.interp(target % 360.0, phases, values))
    span = (dip_b - dip_a) % 360.0
    offset = np.mod(phases - dip_a, 360.0)
    inside_a = offset < span
    peak_a = float(values[inside_a].max()) if inside_a.any() else float("nan")
    peak_b = float(values[~inside_a].max()) if (~inside_a).any() else float("nan")
    mirrors = np.array([abs(interp(phi) - interp(phi + 180.0)) for phi in phases])
    mean = 0.5 * (peak_a + peak_b)
    return {
        "dip_a_deg": dip_a, "dip_b_deg": dip_b,
        "peak_a_mV": peak_a, "peak_b_mV": peak_b,
        "lobe_imbalance": float((peak_a - peak_b) / mean) if mean else float("nan"),
        "mirror_rms_mV": float(mirrors.mean()),
        "mirror_rel": float(mirrors.mean() / values.mean()) if values.mean() else float("nan"),
    }


def summarise(run_dir):
    """一次运行的关键量（单位 mV，斜率单位 mV/deg）。"""
    phases, values = read_curve(run_dir)
    coef, rms = decompose(phases, values)
    c1 = abs(coef["+1"])
    c2 = abs(coef["+2"])
    c3 = abs(coef["+3"])
    r_peak = float(np.max(np.abs(values))) * 1e3
    lobes = lobe_metrics(np.degrees(phases), np.abs(values) * 1e3)
    record = {
        "c0_mV": abs(coef["c0"]) * 1e3,
        "c0_arg_deg": float(np.degrees(np.angle(coef["c0"]))),
        "c1_mV": c1 * 1e3,
        "c2_mV": c2 * 1e3,
        "c3_mV": c3 * 1e3,
        "even_ratio": float(c2 / c1) if c1 else float("nan"),
        "third_ratio": float(c3 / c1) if c1 else float("nan"),
        "S_mV_per_deg": float((c1 + abs(coef["-1"])) * np.pi / 180.0) * 1e3,
        "r_peak_mV": r_peak,
        "harmonic_rms_mV": rms * 1e3,
        "headroom_mV": 344.0 - r_peak,
    }
    record.update(lobes)
    return record, values, phases
