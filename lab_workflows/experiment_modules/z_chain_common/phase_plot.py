"""A03 整周相位响应的绘图数据与两联图；供其它实验复用 A03 相图。

绘图数据读 ``raw/coarse_*.npz`` 与配置里的 ``phase_response`` 状态，做旋转对齐、
三分量分解与式 (2.2.2) 整周拟合。本模块不依赖任何实验包：读帧函数与帧异常类型由
调用方注入，缺帧容错按注入的异常类型处理；matplotlib 保持延迟导入。
"""
from __future__ import annotations

import math
from pathlib import Path
import re

import numpy as np

from ...experiment_runtime import check_cancelled
from . import signal_fit
from .analysis import decompose

# 配置文件记录的数据文件路径与 raw 目录扫描用的帧名；与 A03 采集命名一致。
_COARSE_FILE = re.compile(r"raw/coarse_\d+\.npz")
_COARSE_LABEL = re.compile(r"coarse_\d+\.npz")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def coarse_plot_data(run_dir, config, *, read_frame, frame_errors=Exception):
    """读逐相位帧与 ``phase_response`` 状态，给 A03 相图/波形图所需的对齐数据。

    ``read_frame`` 是调用方的 ``(run_dir, label, kind) -> (metadata, arrays)``；
    ``frame_errors`` 是调用方认定的帧异常类型，缺帧或拟合失败时按它容错跳过。
    """
    files = config.get("data_files") or []
    labels = [Path(item).stem for item in files
              if isinstance(item, str) and _COARSE_FILE.fullmatch(item)]
    if not labels:
        raw_dir = Path(run_dir).resolve() / "raw"
        if raw_dir.is_dir():
            labels = [p.stem for p in raw_dir.iterdir()
                      if p.is_file() and _COARSE_LABEL.fullmatch(p.name)]
    labels.sort(key=lambda stem: int(stem.split("_")[1]))
    phis, raw_z = [], []
    for label in labels:
        check_cancelled()
        try:
            meta, arr = read_frame(run_dir, label, "xy")
        except frame_errors:
            continue
        phi = _number(meta.get("burst_command_deg"))
        if phi is None:
            continue
        x_m, y_m = float(np.mean(arr["x_v"])), float(np.mean(arr["y_v"]))
        if math.isfinite(x_m) and math.isfinite(y_m):
            phis.append(phi)
            raw_z.append(complex(x_m, y_m))
    if not phis:
        return None
    order = np.argsort(phis)
    phi_arr = np.asarray(phis, dtype=float)[order]
    phase_cfg = _mapping(config.get("phase_response"))
    best_deg = _number(phase_cfg.get("burst_command_deg"))
    rot_deg = _number(phase_cfg.get("rotation_deg"))
    z_aligned = np.asarray(raw_z, dtype=complex)[order] * np.exp(
        -1j * np.deg2rad(0.0 if rot_deg is None else rot_deg))
    curve_deg = z_curve = z_best = tangent = tangent_gain = eq222_fit = None
    if phi_arr.size >= 6:
        try:
            (c0, pos, neg), _ = decompose(phi_arr, z_aligned)
            curve_deg = np.linspace(0.0, 360.0, 721)
            rad = np.deg2rad(curve_deg)
            eq222_fit = signal_fit.fit_eq222_full_cycle(
                phi_arr, z_aligned.imag, phi_c_init_deg=0.0 if best_deg is None else best_deg
            )
            phi_c_rad = math.radians(eq222_fit["phi_c_deg"])
            y_eq = signal_fit.eq222_y(rad, eq222_fit["y0"], eq222_fit["amp"] * eq222_fit["r"],
                                      eq222_fit["r"] ** 2, phi_c_rad, eq222_fit["v0"])
            # X 分量以低阶正交谐波加色散投影拟合连续轨迹（若退化则回退三分量）。
            phi_data_rad = np.deg2rad(phi_arr)
            v_data = eq222_fit["v0"] + eq222_fit["r"] * np.sin(phi_data_rad - phi_c_rad)
            y_unit_data = v_data / (1.0 + v_data * v_data)
            x_design = np.column_stack((np.ones_like(phi_data_rad), np.sin(phi_data_rad - phi_c_rad),
                                        np.cos(phi_data_rad - phi_c_rad), y_unit_data))
            x_coef, _, x_rank, _ = np.linalg.lstsq(x_design, z_aligned.real, rcond=None)
            if x_rank == 4:
                v_grid = eq222_fit["v0"] + eq222_fit["r"] * np.sin(rad - phi_c_rad)
                x_eq = (x_coef[0] + x_coef[1] * np.sin(rad - phi_c_rad)
                        + x_coef[2] * np.cos(rad - phi_c_rad)
                        + x_coef[3] * (v_grid / (1.0 + v_grid * v_grid)))
            else:
                x_eq = (c0 + pos * np.exp(1j * rad) + neg * np.exp(-1j * rad)).real
            z_curve = x_eq + 1j * y_eq
            if best_deg is not None:
                phi_b = math.radians(best_deg)
                tangent = 1j * (pos * np.exp(1j * phi_b) - neg * np.exp(-1j * phi_b))
                tangent_gain = abs(tangent) if np.isfinite(tangent) else None
                v_b = eq222_fit["v0"] + eq222_fit["r"] * math.sin(phi_b - phi_c_rad)
                y_b = signal_fit.eq222_y(
                    np.array([phi_b]), eq222_fit["y0"], eq222_fit["amp"] * eq222_fit["r"],
                    eq222_fit["r"] ** 2, phi_c_rad, eq222_fit["v0"])[0]
                if x_rank == 4:
                    x_b = (x_coef[0] + x_coef[1] * math.sin(phi_b - phi_c_rad)
                           + x_coef[2] * math.cos(phi_b - phi_c_rad)
                           + x_coef[3] * (v_b / (1.0 + v_b * v_b)))
                else:
                    x_b = float((c0 + pos * np.exp(1j * phi_b) + neg * np.exp(-1j * phi_b)).real)
                z_best = complex(x_b, float(y_b))
        except frame_errors:
            pass
    if tangent_gain is None:
        tangent_gain = _number(_mapping(phase_cfg.get("coarse_fit")).get("tangent_v_per_command_rad"))
    return dict(phi_deg=phi_arr, z_aligned=z_aligned, rotated=rot_deg is not None, best_deg=best_deg,
                curve_deg=curve_deg, z_curve=z_curve, z_best=z_best, tangent=tangent,
                tangent_gain=tangent_gain, eq222=eq222_fit)


def _diagnostic(ax, text):
    ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes)


def _add_zoom_x_inset(parent, x_mv, y_mv, color_vals, cmap, *, line_x=None, line_y=None,
                      star_xy=None, title="X zoomed", bounds=(0.08, 0.14, 0.34, 0.72), s=12):
    ax_in = parent.inset_axes(list(bounds))
    if line_x is not None and line_y is not None:
        ax_in.plot(line_x, line_y, "-", color="#888888", lw=0.8, alpha=0.7)
    ax_in.scatter(x_mv, y_mv, c=color_vals, cmap=cmap, s=s, edgecolor="#222222", lw=0.25, zorder=3)
    if star_xy is not None:
        ax_in.plot(star_xy[0], star_xy[1], marker="*", markersize=7.5, color="#d55e00",
                   linestyle="none", zorder=4)
    x_pad = max(0.2 * float(np.ptp(x_mv)), 1.0)
    y_pad = max(0.08 * float(np.ptp(y_mv)), 1.0)
    ax_in.set_xlim(float(x_mv.min()) - x_pad, float(x_mv.max()) + x_pad)
    ax_in.set_ylim(float(y_mv.min()) - y_pad, float(y_mv.max()) + y_pad)
    ax_in.tick_params(labelsize=6.5)
    ax_in.set_title(title, fontsize=7, pad=2)
    ax_in.grid(alpha=0.25, lw=0.4)


def coarse_figure(coarse_data):
    """构建 A03 整周相位响应图（相图 + X/Y 波形）。"""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from ...plotting import figure_size

    # --- Fig 1: A03 Full-cycle phase portrait & Eq.(2.2.2)+v0 waveforms ---
    fig = Figure(figsize=figure_size("wide", height_mm=90), layout="constrained")
    FigureCanvasAgg(fig)
    ax_xy, ax_wave = fig.subplots(1, 2)
    if coarse_data is not None:
        phi_deg = coarse_data["phi_deg"]
        z_aligned = coarse_data["z_aligned"]
        x_mv, y_mv = z_aligned.real * 1e3, z_aligned.imag * 1e3
        eq222 = coarse_data.get("eq222")
        zc = coarse_data["z_curve"]
        if zc is not None:
            ax_xy.plot(zc.real * 1e3, zc.imag * 1e3, color="#888888", lw=0.9, alpha=0.75,
                       label="Eq. (2.2.2) + v0 locus" if eq222 else "Phase locus", zorder=2)
        sc = ax_xy.scatter(x_mv, y_mv, c=phi_deg, cmap="twilight", vmin=0, vmax=360,
                           s=14, edgecolor="none", alpha=0.9, zorder=3)
        fig.colorbar(sc, ax=ax_xy, label="Carrier phase (deg)", pad=0.02)
        star_xy = None
        if coarse_data["z_best"] is not None:
            zb = coarse_data["z_best"]
            bx, by = float(zb.real * 1e3), float(zb.imag * 1e3)
            star_xy = (bx, by)
            ax_xy.plot(bx, by, marker="*", markersize=9, color="#d55e00", linestyle="none",
                       zorder=4, label=f"Work point {coarse_data['best_deg']:.1f} deg")
            tang = coarse_data["tangent"]
            if tang is not None and abs(tang) > 0:
                span = max(float(np.ptp(x_mv)), float(np.ptp(y_mv)), 1e-3)
                vec = tang / abs(tang) * (0.22 * span)
                ax_xy.annotate("", xy=(bx + float(vec.real), by + float(vec.imag)), xytext=(bx, by),
                               arrowprops=dict(arrowstyle="->", lw=1.6, color="#d55e00"), zorder=5)
        ax_xy.set_aspect("equal", adjustable="datalim")
        ax_xy.legend(fontsize=7.5, loc="lower right")
        ax_xy.set(xlabel="Demod 4 X (mV)", ylabel="Demod 4 Y (mV)",
                  title="Phase portrait (1:1 aligned)" if coarse_data["rotated"] else "Phase portrait (1:1 raw)")
        if np.ptp(x_mv) < 0.25 * max(float(np.ptp(y_mv)), 1e-6):
            _add_zoom_x_inset(ax_xy, x_mv, y_mv, phi_deg, "twilight",
                              line_x=(zc.real * 1e3 if zc is not None else None),
                              line_y=(zc.imag * 1e3 if zc is not None else None),
                              star_xy=star_xy, title="X zoomed")

        if zc is not None:
            y_fit_lbl = (f"Eq. (2.2.2)+v0 Y (r={eq222['r']:.2f}, RMS {eq222['rms_v']*1e3:.1f} mV)"
                         if eq222 else "Y theory fit")
            ax_wave.plot(coarse_data["curve_deg"], zc.imag * 1e3, color="#d55e00", lw=1.5, label=y_fit_lbl)
            ax_wave.plot(coarse_data["curve_deg"], zc.real * 1e3, color="#1f77b4", lw=1.1, alpha=0.85)
        ax_wave.plot(phi_deg, y_mv, "o", markersize=3.2, color="#d55e00", label="Demod 4 Y data")
        ax_wave.plot(phi_deg, x_mv, "s", markersize=3.0, color="#1f77b4", label="Demod 4 X data")
        if coarse_data["best_deg"] is not None:
            ax_wave.axvline(coarse_data["best_deg"], color="#555555", ls=":", lw=1.0,
                            label=f"phi* = {coarse_data['best_deg']:.1f} deg")
        y_lo, y_hi = float(y_mv.min()), float(y_mv.max())
        y_sp = max(y_hi - y_lo, 1e-3)
        ax_wave.set(xlim=(0, 360), ylim=(y_lo - 0.14 * y_sp, y_hi + 0.34 * y_sp),
                    xlabel="Carrier command phase (deg)", ylabel="Demod 4 output (mV)",
                    title="Full-cycle X & Y waveforms")
        ax_wave.legend(fontsize=7.2, loc="upper left")
    else:
        _diagnostic(ax_xy, "No usable A03 coarse phase frames")
        _diagnostic(ax_wave, "No usable A03 coarse phase frames")
        ax_xy.set(xlabel="Demod 4 X (mV)", ylabel="Demod 4 Y (mV)", title="Phase portrait")
        ax_wave.set(xlabel="Carrier command phase (deg)", ylabel="Demod 4 output (mV)", title="Full-cycle waveforms")
    gain_txt = ""
    if coarse_data and coarse_data.get("eq222"):
        eq222 = coarse_data["eq222"]
        gain_txt = f" | r={eq222['r']:.2f}, v0={eq222['v0']:.3f}, crossing K={eq222['k0_v_per_rad']:.4g} V/rad"
    elif coarse_data and coarse_data["tangent_gain"]:
        gain_txt = f" | |dZ/dφ| = {coarse_data['tangent_gain']:.4g} V/rad"
    fig.suptitle(f"A03: Full-cycle phase portrait{gain_txt}")
    return fig
