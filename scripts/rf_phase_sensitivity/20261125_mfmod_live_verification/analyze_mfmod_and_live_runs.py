"""整理 MF-MOD 独立电学实机验证与正式实验实机运行的定量对比、B01 部分合格物理成因及可恢复频响预估。

用法（开发目录）：
    $env:LAB_DATA_ROOT = "D:\\Code\\exp_agent_run\\data"
    agent_exp_env\\Scripts\\python.exe scripts\\rf_phase_sensitivity\\20261125_mfmod_live_verification\\analyze_mfmod_and_live_runs.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy.signal import welch
import yaml

from lab_workflows.common import data_root
from lab_workflows.experiment_modules.rf_phase_sensitivity import analysis_steps, signal_fit
from lab_workflows.plotting import (
    COLOR_CYAN,
    COLOR_GRAY,
    COLOR_GREEN,
    COLOR_OPTIMAL,
    COLOR_ORANGE,
    COLOR_TRAD,
    format_axis,
    new_figure,
    plot_style_context,
    save_figure,
    style_legend,
)

STUDY_DIR = Path(__file__).resolve().parent
FIGURES_DIR = STUDY_DIR / "figures"

VERIF_REL = Path("rf_phase_sensitivity/_mfli_mod_verification")
RUN_SAT_REL = Path("rf_phase_sensitivity/1124_191042_phase_sensitivity")
RUN_A04_REL = Path("rf_phase_sensitivity/1124_191353_phase_sensitivity")
RUN_1125_REL = Path("rf_phase_sensitivity/1125_074220_phase_sensitivity")
RUN_DG_REL = Path("rf_phase_sensitivity/1124_115154_phase_sensitivity")


def block_demod(
    time_s: np.ndarray,
    ref_v: np.ndarray,
    coil_v: np.ndarray,
    nominal_hz: float = 5000.0,
    cycles_per_block: int = 10,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """用 CH3 参考信号实测载波频率对 CH4 做每 N 个周期的最小二乘 IQ 解调。"""
    carrier_hz = float(signal_fit.measure_electrical(time_s, ref_v, coil_v, nominal_hz)["reference_hz"])
    dt = float(np.median(np.diff(time_s)))
    block_pts = max(20, int(round(cycles_per_block / (carrier_hz * dt))))
    n_blocks = len(time_s) // block_pts
    trim = n_blocks * block_pts

    t_mat = time_s[:trim].reshape(n_blocks, block_pts)
    v_mat = coil_v[:trim].reshape(n_blocks, block_pts)

    omega = 2.0 * np.pi * carrier_hz
    t_center = np.empty(n_blocks, dtype=float)
    amp = np.empty(n_blocks, dtype=float)
    phase_deg = np.empty(n_blocks, dtype=float)

    for idx in range(n_blocks):
        tb = t_mat[idx]
        yb = v_mat[idx]
        design = np.column_stack([np.ones_like(tb), np.cos(omega * tb), np.sin(omega * tb)])
        coef, _, _, _ = np.linalg.lstsq(design, yb, rcond=None)
        a_cos, b_sin = float(coef[1]), float(coef[2])
        amp[idx] = float(np.hypot(a_cos, b_sin))
        phase_deg[idx] = float(np.arctan2(b_sin, a_cos))
        t_center[idx] = float(np.mean(tb))

    phase_deg = np.rad2deg(np.unwrap(phase_deg))
    return t_center, amp, phase_deg, carrier_hz


def plot_stage2_stability_and_phase(verif_dir: Path) -> Path:
    """绘制 Stage 2 MF-MOD 开关与幅值切换相位稳定性及 0-360° 移相线性度。"""
    summary = json.loads((verif_dir / "verification_summary.json").read_text(encoding="utf-8"))
    t1 = summary["test1_switch_and_amplitude"]
    t2 = summary["test2_phase_sweep"]

    short_labels = [
        "Base\n0.1V", "Tog 1", "Tog 2", "Tog 3", "Tog 4", "Tog 5",
        "0.025\nVpk", "0.25\nVpk", "Ret\n0.1V", "PM\nON", "PM\nOFF",
    ]
    x1 = np.arange(len(t1))
    deltas1 = np.array([row["delta_from_baseline_deg"] for row in t1], dtype=float)

    cmd_deg = np.array([row["command_deg"] for row in t2], dtype=float)
    meas_raw = np.array([row["carrier_center_deg"] for row in t2], dtype=float)
    meas_unwrap = np.rad2deg(np.unwrap(np.deg2rad(meas_raw)))
    slope, intercept = np.polyfit(cmd_deg, meas_unwrap, 1)
    fit_deg = slope * cmd_deg + intercept
    res_deg = meas_unwrap - fit_deg
    pm2 = np.array([row["phase_pk_deg"] for row in t2], dtype=float)
    am2_pct = np.array([row["am_fraction_pk"] * 100.0 for row in t2], dtype=float)

    out_png = FIGURES_DIR / "mfmod_verification_stability_phase.png"
    with plot_style_context("paper"):
        fig, axes = new_figure(figsize=(7.4, 5.2), nrows=2, ncols=2, constrained_layout=True)

        ax = axes[0, 0]
        ax.axhspan(-0.05, 0.05, color=COLOR_GRAY, alpha=0.14, zorder=0)
        ax.axhline(0.0, color=COLOR_GRAY, ls="--", lw=0.8, zorder=1)
        ax.plot(x1, deltas1, color="#BBBBBB", lw=0.9, zorder=2)
        idx_tog = list(range(0, 6)) + [8]
        idx_amp = [6, 7]
        idx_pm = [9, 10]
        ax.scatter(x1[idx_tog], deltas1[idx_tog], color=COLOR_OPTIMAL, s=26, zorder=3, edgecolors="white", linewidths=0.4, label="0.1 Vpk switch")
        ax.scatter(x1[idx_amp], deltas1[idx_amp], color=COLOR_ORANGE, s=28, zorder=3, edgecolors="white", linewidths=0.4, label="Amp step")
        ax.scatter(x1[idx_pm], deltas1[idx_pm], color=COLOR_GREEN, s=28, zorder=3, edgecolors="white", linewidths=0.4, label="PM ON / OFF")
        ax.annotate("+0.308 deg\n(0.25 Vpk)", xy=(7, deltas1[7]), xytext=(9.1, 0.255), fontsize=6.2,
                    color=COLOR_TRAD, ha="center", arrowprops={"arrowstyle": "->", "color": COLOR_TRAD, "lw": 0.65})
        ax.annotate("-0.064 deg\n(0.025 Vpk)", xy=(6, deltas1[6]), xytext=(4.1, -0.115), fontsize=6.2,
                    color=COLOR_ORANGE, ha="center", arrowprops={"arrowstyle": "->", "color": COLOR_ORANGE, "lw": 0.65})
        ax.set_xticks(x1)
        ax.set_xticklabels(short_labels, fontsize=6.0)
        ax.set_ylim(-0.15, 0.39)
        format_axis(ax, xlabel="Test 1 sequence step", ylabel="Carrier phase shift (deg)")
        style_legend(ax, loc="upper left", bbox_to_anchor=(0.01, 0.87), fontsize=5.8, frameon=True)
        ax.text(0.03, 0.92, "(a)", transform=ax.transAxes, fontweight="bold", fontsize=9)
        ax.text(0.98, 0.96, f"5x ON/OFF max |delta| = {np.max(np.abs(deltas1[1:6])):.4f} deg\nPM ON delta = {deltas1[9]:+.4f} deg",
                transform=ax.transAxes, ha="right", va="top", fontsize=6.0,
                bbox={"boxstyle": "round,pad=0.22", "facecolor": "white", "alpha": 0.88, "edgecolor": "#CCCCCC"})

        ax = axes[0, 1]
        ax.plot(cmd_deg, fit_deg, color=COLOR_GRAY, ls="--", lw=1.1, label=f"Fit: {slope:.5f}*cmd + {intercept:.2f} deg")
        ax.scatter(cmd_deg, meas_unwrap, color=COLOR_OPTIMAL, s=24, zorder=3, label="DHO924 CH4 unwrapped")
        ax.set_xticks(np.arange(0, 361, 60))
        format_axis(ax, xlabel="MFLI mods/0/carrier/phaseshift (deg)", ylabel="Measured CH4 phase (deg)")
        style_legend(ax, loc="upper left", fontsize=6.4, frameon=True)
        ax.text(0.96, 0.08, "(b)", transform=ax.transAxes, ha="right", fontweight="bold", fontsize=9)

        ax = axes[1, 0]
        ax.axhspan(-0.03, 0.03, color=COLOR_GRAY, alpha=0.14, zorder=0)
        ax.axhline(0.0, color=COLOR_GRAY, ls="--", lw=0.8)
        ax.plot(cmd_deg, res_deg, "-o", color=COLOR_OPTIMAL, ms=4.0, lw=1.1)
        ax.set_xticks(np.arange(0, 361, 60))
        max_res = float(np.max(np.abs(res_deg)))
        rms_res = float(np.sqrt(np.mean(res_deg**2)))
        ax.set_ylim(-0.045, 0.048)
        format_axis(ax, xlabel="MFLI mods/0/carrier/phaseshift (deg)", ylabel="Phase linear residual (deg)")
        ax.text(0.03, 0.92, "(c)", transform=ax.transAxes, fontweight="bold", fontsize=9)
        ax.text(0.97, 0.92, f"Max |res| = {max_res:.4f} deg\nRMS = {rms_res:.4f} deg",
                transform=ax.transAxes, ha="right", va="top", fontsize=6.3,
                bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "alpha": 0.88, "edgecolor": "#CCCCCC"})

        ax = axes[1, 1]
        ax.axhline(2.0, color=COLOR_OPTIMAL, ls=":", lw=0.9, alpha=0.7)
        l1 = ax.plot(cmd_deg, pm2, "-o", color=COLOR_OPTIMAL, ms=3.8, lw=1.1, label="PM peak (deg)")
        ax.set_ylim(1.945, 2.035)
        ax.set_xticks(np.arange(0, 361, 60))
        format_axis(ax, xlabel="MFLI mods/0/carrier/phaseshift (deg)", ylabel="Measured PM depth (deg)")
        ax.tick_params(axis="y", colors=COLOR_OPTIMAL)
        ax.yaxis.label.set_color(COLOR_OPTIMAL)

        ax2 = ax.twinx()
        l2 = ax2.plot(cmd_deg, am2_pct, "--s", color=COLOR_TRAD, ms=3.5, lw=1.0, label="Residual AM (%)")
        ax2.set_ylim(-0.002, 0.024)
        ax2.set_ylabel("Residual 1fm AM (%)", fontweight="normal", color=COLOR_TRAD)
        ax2.tick_params(axis="y", colors=COLOR_TRAD)
        ax2.spines["top"].set_visible(False)
        lines = l1 + l2
        ax.legend(lines, [ln.get_label() for ln in lines], loc="upper right", fontsize=6.1, frameon=True)
        ax.text(0.03, 0.92, "(d)", transform=ax.transAxes, fontweight="bold", fontsize=9)
        ax.text(0.50, 0.06, f"AM gate: 0.100% | Max AM: {np.max(am2_pct):.4f}%", transform=ax.transAxes,
                ha="center", va="bottom", fontsize=6.0, color=COLOR_TRAD,
                bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "alpha": 0.85, "edgecolor": "#E0C0B0"})

        save_figure(fig, out_png, dpi=200, paired=False)
    return out_png


def plot_stage2_modulation_sweep(verif_dir: Path) -> Path:
    """绘制 Stage 2 5-80 Hz 调制频点扫描稳定性与 20 Hz 时域相角/包络对比。"""
    summary = json.loads((verif_dir / "verification_summary.json").read_text(encoding="utf-8"))
    t3 = summary["test3_fm_sweep"]
    t4 = summary["test4_unmodulated_return"]

    anchor = float(t3[0]["carrier_center_deg"])
    step_labels = [f"{int(r['fm_hz'])} Hz" + (" (rep)" if r["repeat"] else "") for r in t3] + ["PM OFF"]
    deltas = [float(r["delta_from_anchor_deg"]) for r in t3] + [float(t4["carrier_center_deg"] - anchor)]
    pms = [float(r["phase_pk_deg"]) for r in t3] + [float(t4["phase_pk_deg"])]
    ams = [float(r["am_fraction_pk"] * 100.0) for r in t3] + [float(t4["am_fraction_pk"] * 100.0)]
    xs = np.arange(len(step_labels))

    with np.load(verif_dir / "test3_00_fm_20hz.npz") as d_pm, np.load(verif_dir / "test4_unmodulated.npz") as d_un:
        t_pm, a_pm, phi_pm, _ = block_demod(d_pm["time_s"], d_pm["reference_v"], d_pm["coil_v"], 5000.0, 10)
        t_un, a_un, phi_un, _ = block_demod(d_un["time_s"], d_un["reference_v"], d_un["coil_v"], 5000.0, 10)

    phi_pm_zero = phi_pm - float(np.mean(phi_pm))
    phi_un_zero = phi_un - float(np.mean(phi_un))
    a_pm_pct = (a_pm / float(np.mean(a_pm)) - 1.0) * 100.0
    a_un_pct = (a_un / float(np.mean(a_un)) - 1.0) * 100.0

    m_pm = (t_pm >= -0.10) & (t_pm <= 0.10)
    m_un = (t_un >= -0.10) & (t_un <= 0.10)
    t_ms = t_pm[m_pm] * 1000.0
    t_un_ms = t_un[m_un] * 1000.0
    y_pm_win = phi_pm_zero[m_pm]
    y_un_win = phi_un_zero[m_un]

    w = 2.0 * np.pi * 20.0
    design = np.column_stack([np.ones_like(t_pm), np.cos(w * t_pm), np.sin(w * t_pm)])
    beta, _, _, _ = np.linalg.lstsq(design, phi_pm_zero, rcond=None)
    phi_fit = (design @ beta)[m_pm]
    fit_amp = float(np.hypot(beta[1], beta[2]))

    out_png = FIGURES_DIR / "mfmod_verification_modulation_sweep.png"
    with plot_style_context("paper"):
        fig, axes = new_figure(figsize=(7.4, 5.2), nrows=2, ncols=2, constrained_layout=True)

        ax = axes[0, 0]
        ax.axhspan(-0.05, 0.05, color=COLOR_GRAY, alpha=0.14, zorder=0, label="±0.05 deg band")
        ax.axhline(0.0, color=COLOR_GRAY, ls="--", lw=0.8)
        ax.plot(xs[:-1], deltas[:-1], "-o", color=COLOR_OPTIMAL, ms=4.2, lw=1.1, label="FM ON (2.0 deg)")
        ax.scatter(xs[-1:], deltas[-1:], color=COLOR_GREEN, s=36, zorder=4, label="PM OFF return")
        ax.set_xticks(xs)
        ax.set_xticklabels(step_labels, fontsize=6.0, rotation=20, ha="right")
        ax.set_ylim(-0.058, 0.058)
        format_axis(ax, xlabel="Modulation step", ylabel="Carrier center drift (deg)")
        style_legend(ax, loc="upper right", fontsize=6.0, frameon=True)
        ax.text(0.03, 0.92, "(a)", transform=ax.transAxes, fontweight="bold", fontsize=9)

        ax = axes[0, 1]
        ax.axhline(2.0, color=COLOR_OPTIMAL, ls=":", lw=0.9, alpha=0.7)
        l1 = ax.plot(xs, pms, "-o", color=COLOR_OPTIMAL, ms=4.0, lw=1.1, label="PM peak (deg)")
        ax.set_ylim(-0.18, 2.55)
        ax.set_xticks(xs)
        ax.set_xticklabels(step_labels, fontsize=6.0, rotation=20, ha="right")
        format_axis(ax, xlabel="Modulation step", ylabel="Measured PM depth (deg)")
        ax.tick_params(axis="y", colors=COLOR_OPTIMAL)
        ax.yaxis.label.set_color(COLOR_OPTIMAL)

        ax2 = ax.twinx()
        l2 = ax2.plot(xs, ams, "--s", color=COLOR_TRAD, ms=3.5, lw=1.0, label="Residual AM (%)")
        ax2.axhline(0.10, color=COLOR_TRAD, ls=":", lw=0.9, alpha=0.75)
        ax2.set_ylim(-0.008, 0.118)
        ax2.set_ylabel("Residual 1fm AM (%)", fontweight="normal", color=COLOR_TRAD)
        ax2.tick_params(axis="y", colors=COLOR_TRAD)
        ax2.spines["top"].set_visible(False)
        lines = l1 + l2
        ax.legend(lines, [ln.get_label() for ln in lines], loc="center left", bbox_to_anchor=(0.03, 0.45), fontsize=6.0, frameon=True)
        ax.text(0.03, 0.92, "(b)", transform=ax.transAxes, fontweight="bold", fontsize=9)
        ax2.text(0.97, 0.88, "0.10% AM gate", transform=ax2.transAxes, ha="right", va="bottom", fontsize=5.8, color=COLOR_TRAD)

        ax = axes[1, 0]
        ax.plot(t_ms, y_pm_win, color=COLOR_CYAN, lw=2.0, alpha=0.85, label="20 Hz PM (2 ms IQ)")
        ax.plot(t_ms, phi_fit, color=COLOR_OPTIMAL, ls="--", lw=1.0, label=f"20 Hz fit ({fit_amp:.3f} deg pk)")
        ax.plot(t_un_ms, y_un_win, color=COLOR_GREEN, lw=1.0, label=f"PM OFF (std {np.std(y_un_win):.3f} deg)")
        ax.set_xlim(-100, 100)
        ax.set_ylim(-2.5, 2.75)
        format_axis(ax, xlabel="Time relative to Z trigger (ms)", ylabel="Instantaneous phase (deg)")
        style_legend(ax, loc="upper right", fontsize=5.8, frameon=True)
        ax.text(0.03, 0.92, "(c)", transform=ax.transAxes, fontweight="bold", fontsize=9)

        ax = axes[1, 1]
        a_pm_win = a_pm_pct[m_pm]
        a_un_win = a_un_pct[m_un]
        beta_a, _, _, _ = np.linalg.lstsq(design, a_pm_pct, rcond=None)
        a_fit_win = (design @ beta_a)[m_pm]
        a_1fm_pk = float(np.hypot(beta_a[1], beta_a[2]))
        ax.axhline(0.10, color=COLOR_TRAD, ls=":", lw=1.0, label="±0.10% AM gate")
        ax.axhline(-0.10, color=COLOR_TRAD, ls=":", lw=1.0)
        ax.plot(t_ms, a_pm_win, color=COLOR_CYAN, lw=0.85, alpha=0.8, label="20 Hz PM (2 ms IQ)")
        ax.plot(t_un_ms, a_un_win, color=COLOR_GREEN, lw=0.7, alpha=0.65, label="PM OFF (2 ms IQ)")
        ax.plot(t_ms, a_fit_win, color=COLOR_OPTIMAL, ls="-", lw=1.3, label=f"20 Hz 1fm fit ({a_1fm_pk:.4f}% pk)")
        ax.set_xlim(-100, 100)
        ax.set_ylim(-0.36, 0.36)
        format_axis(ax, xlabel="Time relative to Z trigger (ms)", ylabel="Envelope deviation dA/Ac (%)")
        style_legend(ax, loc="upper right", fontsize=5.6, ncol=2, frameon=True)
        ax.text(0.03, 0.92, "(d)", transform=ax.transAxes, fontweight="bold", fontsize=9)

        save_figure(fig, out_png, dpi=200, paired=False)
    return out_png


def _env_spectrum(npz_path: Path) -> tuple[np.ndarray, np.ndarray]:
    with np.load(npz_path, allow_pickle=True) as z:
        t = np.asarray(z["time_s"], dtype=float)
        ref = np.asarray(z["reference_v"], dtype=float)
        coil = np.asarray(z["coil_v"], dtype=float)
    t0 = t - t[0]
    dt = float(np.median(np.diff(t0)))
    fs = 1.0 / dt
    first, fc, _ = signal_fit._reference(t0, ref, 5000.0)
    blk = int(round(0.002 * fs))
    nb = len(coil) // blk
    amp = np.empty(nb)
    for k in range(nb):
        sl = slice(k * blk, (k + 1) * blk)
        c = 2.0 * np.mean(coil[sl] * np.exp(-1j * 2 * np.pi * fc * (t0[sl] - first)))
        amp[k] = abs(c)
    amp_norm = (amp - amp.mean()) / amp.mean() * 100.0
    fb = np.fft.rfftfreq(len(amp_norm), d=0.002)
    sb = np.abs(np.fft.rfft(amp_norm)) * 2.0 / len(amp_norm)
    return fb, sb


def _fit_xy_with_mains_notch(npz_path: Path, freq_hz: float) -> dict[str, float]:
    """分别按正式 fit_tone 口径与剔除 50/100/150 Hz 工频口径拟合锁相 Y 基带音调。"""
    with np.load(npz_path, allow_pickle=True) as z:
        t = np.asarray(z["time_s"], dtype=float)
        x = np.asarray(z["x_v"], dtype=float)
        y = np.asarray(z["y_v"], dtype=float)
        meta = json.loads(str(z["metadata"].item()))
    rate = float(meta["rate_sa_s"])
    corr_s = float(meta["lockin"]["order"]) * float(meta["lockin"]["time_constant_s"])
    stock = analysis_steps.fit_tone(t, x, y, rate, freq_hz, correlation_s=corr_s)

    tt = t - t[0]
    cols = [
        np.sin(2.0 * np.pi * freq_hz * tt),
        np.cos(2.0 * np.pi * freq_hz * tt),
        np.ones_like(tt),
        tt / tt[-1] - 0.5,
        np.sin(2.0 * np.pi * 2.0 * freq_hz * tt),
        np.cos(2.0 * np.pi * 2.0 * freq_hz * tt),
    ]
    for harmonic_hz in (50.0, 100.0, 150.0):
        cols.extend([
            np.sin(2.0 * np.pi * harmonic_hz * tt),
            np.cos(2.0 * np.pi * harmonic_hz * tt),
        ])
    design = np.column_stack(cols)
    coef, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    res = y - design @ coef
    dof = len(tt) - design.shape[1]
    inv_g = np.linalg.inv(design.T @ design)
    y_pk_clean = float(np.hypot(coef[0], coef[1]))
    direction = coef[:2] / y_pk_clean
    se_clean = float(np.sqrt((res @ res / dof) * (direction @ inv_g[:2, :2] @ direction)))
    return {
        "y_pk_stock_v": float(stock["y_pk_v"]),
        "se_stock_v": float(stock["y_pk_se_v"]),
        "snr_stock": float(stock["y_snr"]),
        "y_pk_clean_v": y_pk_clean,
        "se_clean_v": se_clean,
        "snr_clean": float(y_pk_clean / se_clean),
        "raw_std_v": float(np.std(y)),
        "clean_res_std_v": float(np.std(res)),
    }


def plot_1125_root_causes(run_1125: Path, run_dg: Path) -> tuple[Path, list[dict]]:
    """绘制 1125_074220 B01 部分合格两大硬件/测量成因，并对比 DG4000 基线。"""
    cfg_1125 = yaml.safe_load((run_1125 / "experiment_config.yaml").read_text(encoding="utf-8"))
    cfg_dg = yaml.safe_load((run_dg / "experiment_config.yaml").read_text(encoding="utf-8"))

    # 汇总 20/40/20(rep) 在两种拟合口径下的数值
    xy_stats = []
    for row in cfg_1125["transfer"]:
        xy_label = row.get("xy_label")
        if not xy_label:
            continue
        f_hz = float(row["frequency_hz"])
        pm_rad = float(row["phase_pk_rad"])
        fit = _fit_xy_with_mains_notch(run_1125 / "raw" / f"{xy_label}.npz", f_hz)
        k0 = float(cfg_1125["calibration"]["k0_v_per_rad"])
        h_v_rad = fit["y_pk_stock_v"] / pm_rad
        xy_stats.append({
            "freq_hz": f_hz,
            "repeat": bool(row["repeat"]),
            "pm_rad": pm_rad,
            "h_v_per_rad": h_v_rad,
            "norm_gain": h_v_rad / abs(k0),
            **fit,
        })

    out_png = FIGURES_DIR / "diag_1125_partial_root_causes.png"
    with plot_style_context("paper"):
        fig, axes = new_figure(figsize=(10.5, 3.6), nrows=1, ncols=3, constrained_layout=True)

        # Panel A: DHO924 降速混叠导致 5/10 Hz 与 76.6 Hz 杂散泄漏
        ax = axes[0]
        for fname, label, color, lw in (
            ("pm_00_scope.npz", "20 Hz PM (fs=1.25 MSa/s)", COLOR_OPTIMAL, 1.15),
            ("pm_01_scope.npz", "5 Hz PM (fs=500 kSa/s)", COLOR_TRAD, 1.2),
            ("pm_02_scope.npz", "10 Hz PM (fs=500 kSa/s)", COLOR_ORANGE, 1.05),
        ):
            fb, sb = _env_spectrum(run_1125 / "raw" / fname)
            m = (fb >= 1.0) & (fb <= 95.0)
            ax.plot(fb[m], sb[m], label=label, color=color, lw=lw, alpha=0.9)
        ax.axhline(0.10, color=COLOR_GRAY, ls="--", lw=0.9, label="0.10% AM gate")
        ax.annotate("CH3 99th/101st alias\n5.1 Hz (1.01-1.13%)", xy=(5.1, 1.12), xytext=(16, 1.02),
                    arrowprops={"arrowstyle": "->", "color": COLOR_TRAD, "lw": 0.8}, fontsize=6.4, color=COLOR_TRAD)
        ax.annotate("200th+/-1 alias\n10.2 Hz (~0.82%)", xy=(10.2, 0.82), xytext=(20, 0.72),
                    arrowprops={"arrowstyle": "->", "color": COLOR_ORANGE, "lw": 0.8}, fontsize=6.4, color=COLOR_ORANGE)
        ax.annotate("76.6 Hz spur\n(near 80 Hz)", xy=(76.6, 0.65), xytext=(45, 0.56),
                    arrowprops={"arrowstyle": "->", "color": COLOR_OPTIMAL, "lw": 0.8}, fontsize=6.4, color=COLOR_OPTIMAL)
        ax.set_xlim(1, 95)
        ax.set_ylim(0, 1.25)
        ax.set_title("A. Scope Envelope Aliasing (1125)")
        format_axis(ax, xlabel="Envelope frequency (Hz)", ylabel="Envelope AM (% of carrier)")
        style_legend(ax, loc="upper right", fontsize=5.8, frameon=True)

        # Panel B: Demod 4 50 Hz 干扰与实际信号对比
        ax = axes[1]
        for fname, label, color in (
            ("pm_00_xy.npz", "20 Hz PM (pm_00_xy)", COLOR_OPTIMAL),
            ("pm_03_xy.npz", "40 Hz PM (pm_03_xy)", COLOR_GREEN),
        ):
            with np.load(run_1125 / "raw" / fname) as z:
                tt = np.asarray(z["time_s"], dtype=float)
                yy = np.asarray(z["y_v"], dtype=float)
            dt = float(np.median(np.diff(tt)))
            f = np.fft.rfftfreq(len(yy), d=dt)
            s = np.abs(np.fft.rfft(yy - yy.mean())) * 2.0 / len(yy) * 1e3
            m = (f >= 1.0) & (f <= 110.0)
            ax.semilogy(f[m], np.maximum(s[m], 1e-3), label=label, color=color, lw=1.0, alpha=0.88)
        ax.plot(20.0, 5.716, "o", color=COLOR_OPTIMAL, ms=4.5)
        ax.plot(40.0, 4.975, "o", color=COLOR_GREEN, ms=4.5)
        ax.plot(50.0, 53.79, "s", color=COLOR_TRAD, ms=4.5)
        ax.annotate("20 Hz: 5.72 mV\n(clean SNR=84; stock=3.02)", xy=(20.0, 5.72), xytext=(3.0, 14.5),
                    arrowprops={"arrowstyle": "->", "color": COLOR_OPTIMAL, "lw": 0.8}, fontsize=6.2, color=COLOR_OPTIMAL)
        ax.annotate("40 Hz: 4.98 mV\n(stock SNR=1.37)", xy=(40.0, 4.98), xytext=(56.0, 10.5),
                    arrowprops={"arrowstyle": "->", "color": COLOR_GREEN, "lw": 0.8}, fontsize=6.2, color=COLOR_GREEN)
        ax.annotate("50 Hz mains:\n50.5-53.8 mV", xy=(50.0, 53.8), xytext=(58.0, 39.0),
                    arrowprops={"arrowstyle": "->", "color": COLOR_TRAD, "lw": 0.8}, fontsize=6.2, color=COLOR_TRAD)
        ax.set_xlim(1, 110)
        ax.set_ylim(0.02, 100.0)
        ax.set_title("B. Demod 4 Y Spectrum (0.05 Vpp)")
        format_axis(ax, xlabel="Baseband frequency (Hz)", ylabel="Tone peak (mV)")
        style_legend(ax, loc="lower right", fontsize=5.8, frameon=True)

        # Panel C: 同在 1.25 MSa/s 下 MF-MOD vs DG4000 残余 AM
        ax = axes[2]
        idx_select = [0, 3, 4, 5]
        labels = ["20 Hz", "40 Hz", "80 Hz", "20 Hz\n(rep)"]
        am_dg = [float(cfg_dg["transfer"][i]["am_fraction_pk"]) * 100.0 for i in idx_select]
        am_mod = [float(cfg_1125["transfer"][i]["am_fraction_pk"]) * 100.0 for i in idx_select]
        x = np.arange(len(labels))
        w = 0.35
        b1 = ax.bar(x - w / 2, am_dg, width=w, label="1124 DG4000 (1 deg PM)", color=COLOR_GRAY, alpha=0.85)
        b2 = ax.bar(x + w / 2, am_mod, width=w, label="1125 MFLI MF-MOD (2 deg PM)", color=COLOR_OPTIMAL, alpha=0.9)
        ax.axhline(0.10, color=COLOR_TRAD, ls="--", lw=0.9, label="0.10% AM gate")
        for bars in (b1, b2):
            for rect in bars:
                h = rect.get_height()
                ax.text(rect.get_x() + rect.get_width() / 2, h + 0.004, f"{h:.3f}%", ha="center", va="bottom", fontsize=6.0)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=7.0)
        ax.set_ylim(0, 0.20)
        ax.set_title("C. Electrical AM at 1.25 MSa/s")
        format_axis(ax, xlabel="Modulation point", ylabel="Residual 1fm AM (%)")
        style_legend(ax, loc="upper left", fontsize=5.8, frameon=True)

        save_figure(fig, out_png, dpi=200, paired=False)
    return out_png, xy_stats


def _read_coarse_curve(run_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    degs, ys = [], []
    for path in sorted((run_dir / "raw").glob("coarse_*.npz")):
        with np.load(path, allow_pickle=True) as z:
            meta = json.loads(str(z["metadata"].item()))
            y = np.asarray(z["y_v"], dtype=float)
            degs.append(float(meta["burst_command_deg"]))
            ys.append(float(np.mean(y)))
    order = np.argsort(degs)
    return np.asarray(degs)[order], np.asarray(ys)[order]


def _notch_mains(time_s: np.ndarray, values: np.ndarray, harmonics_hz=(50.0, 100.0, 150.0, 200.0, 250.0, 300.0)) -> np.ndarray:
    """仅作离线探索展示：在时域用线性最小二乘扣除工频及其谐波（包含附近±0.05 Hz飘移扩展项），不修改官方B01判定。"""
    t = time_s - time_s[0]
    cols = [np.ones_like(t), t / t[-1] - 0.5]
    # 每根工频线配 0 Hz 与 +/-0.04 Hz 漏泄基，仅用于评估剔除 50 Hz 倍频后宽带本底
    for f0 in harmonics_hz:
        for df in (0.0, -0.04, 0.04):
            w = 2.0 * np.pi * (f0 + df)
            cols.extend([np.cos(w * t), np.sin(w * t)])
    design = np.column_stack(cols)
    coef, _, _, _ = np.linalg.lstsq(design, values, rcond=None)
    return values - design @ coef


def plot_posthoc_physical_recovery(run_sat: Path, run_1125: Path, xy_stats: list[dict]) -> tuple[Path, dict]:
    """直观展示 1125_074220 其实已测出静态标定与 20/40 Hz 物理响应，仅被严格门控扣留折算。"""
    deg_sat, y_sat = _read_coarse_curve(run_sat)
    deg_lin, y_lin = _read_coarse_curve(run_1125)
    cfg_1125 = yaml.safe_load((run_1125 / "experiment_config.yaml").read_text(encoding="utf-8"))
    k0 = float(cfg_1125["calibration"]["k0_v_per_rad"])
    k0_se = float(cfg_1125["calibration"]["k0_se_v_per_rad"])

    with np.load(run_1125 / "raw" / "noise_xy.npz", allow_pickle=True) as z:
        t_n = np.asarray(z["time_s"], dtype=float)
        y_n = np.asarray(z["y_v"], dtype=float)
        meta_n = json.loads(str(z["metadata"].item()))
    t_grid, rate = analysis_steps.sample_grid(t_n, meta_n["rate_sa_s"])
    nperseg = min(math.ceil(rate * 4.0), y_n.size)
    f_raw, psd_raw = welch(y_n, fs=rate, window="hann", nperseg=nperseg, noverlap=nperseg // 2, detrend="linear")
    y_notched = _notch_mains(t_grid, y_n)
    f_cln, psd_cln = welch(y_notched, fs=rate, window="hann", nperseg=nperseg, noverlap=nperseg // 2, detrend="linear")
    keep = f_raw >= 1.0
    f_p = f_raw[keep]
    asd_raw = np.sqrt(psd_raw[keep])
    asd_cln = np.sqrt(psd_cln[keep])

    # 在 [20, 40] Hz 频带按实测主测点 log-log 插值做非官方预估折算
    h20 = xy_stats[0]["h_v_per_rad"]
    h40 = xy_stats[1]["h_v_per_rad"]
    band_m = (f_p >= 20.0) & (f_p <= 40.0)
    h_interp = np.exp(np.interp(np.log(f_p[band_m]), np.log([20.0, 40.0]), np.log([h20, h40])))
    phi_asd_posthoc = asd_cln[band_m] / h_interp

    out_png = FIGURES_DIR / "diag_1125_posthoc_physical_response.png"
    with plot_style_context("paper"):
        fig, axes = new_figure(figsize=(10.5, 3.6), nrows=1, ncols=3, constrained_layout=True)

        # (a) 0.5 Vpp 饱和 vs 0.05 Vpp 线性粗扫
        ax = axes[0]
        ax.plot(deg_sat, y_sat * 1e3, "--s", color=COLOR_GRAY, ms=3.2, lw=1.0, label="1124_191042: 0.5 Vpp (saturated)")
        ax.plot(deg_lin, y_lin * 1e3, "-o", color=COLOR_OPTIMAL, ms=3.4, lw=1.2, label="1125_074220: 0.05 Vpp (linear)")
        wp = float(cfg_1125["calibration"]["carrier_command_deg"])
        ax.axvline(wp, color=COLOR_GREEN, ls=":", lw=1.0, label=f"1125 work point ({wp:.1f} deg)")
        ax.axhline(0.0, color=COLOR_GRAY, ls=":", lw=0.7)
        ax.set_xticks(np.arange(0, 361, 90))
        ax.set_ylim(-295, 255)
        ax.set_title("A. Coarse Phase Response (A03, raw Y)")
        format_axis(ax, xlabel="MFLI carrier phaseshift (deg)", ylabel="Demod 4 Y mean at 0 deg (mV)")
        style_legend(ax, loc="lower left", fontsize=5.5, frameon=True)

        # (b) 其实已经测出的 |H(f)| vs K0
        ax = axes[1]
        ax.axhspan(0.8, 1.2, color=COLOR_GRAY, alpha=0.14, label="Closure band 0.8-1.2")
        ax.axhline(1.0, color=COLOR_OPTIMAL, ls="--", lw=1.0, label=f"Static K0 = {k0:.4f} V/rad")
        f_pts = [xy_stats[0]["freq_hz"], xy_stats[1]["freq_hz"]]
        g_pts = [xy_stats[0]["norm_gain"], xy_stats[1]["norm_gain"]]
        ax.plot(f_pts, g_pts, "-o", color=COLOR_OPTIMAL, ms=5.0, lw=1.2, label="Primary (20/40 Hz; blocked by 50Hz SNR)")
        ax.plot([xy_stats[2]["freq_hz"]], [xy_stats[2]["norm_gain"]], "x", color=COLOR_TRAD, ms=6.5, mew=1.6,
                label=f"20 Hz repeat (ratio {xy_stats[2]['h_v_per_rad']/xy_stats[0]['h_v_per_rad']:.3f})")
        ax.set_xscale("log")
        ax.set_xlim(4, 100)
        ax.set_ylim(0.55, 1.30)
        ax.set_title("B. Measured Gain |H|/|K0| (post-hoc)")
        format_axis(ax, xlabel="Modulation frequency (Hz)", ylabel="Normalized gain |H| / |K0|")
        style_legend(ax, loc="lower left", fontsize=5.5, frameon=True)

        # (c) 为什么 phase_noise 只有上图 + 扣除工频后 20-40 Hz 相位噪声预估
        ax = axes[2]
        ax.loglog(f_p, asd_raw, color=COLOR_GRAY, lw=0.85, alpha=0.8, label="Raw Detector Y ASD (top panel)")
        ax.loglog(f_p, asd_cln, color=COLOR_OPTIMAL, lw=1.0, label="50-300Hz notched Y ASD (V/rtHz)")
        ax.loglog(f_p[band_m], phi_asd_posthoc, color=COLOR_GREEN, lw=1.5, label="Post-hoc Phase ASD [20-40 Hz] (rad/rtHz)")
        ax.annotate(f"20-40 Hz median:\n{np.median(phi_asd_posthoc)*1e3:.2f} mrad/rtHz\n(withheld in B01)",
                    xy=(28.0, float(np.median(phi_asd_posthoc))), xytext=(3.0, 7e-3),
                    arrowprops={"arrowstyle": "->", "color": COLOR_GREEN, "lw": 0.8}, fontsize=6.0, color=COLOR_GREEN)
        ax.set_xlim(1, 800)
        ax.set_title("C. Noise ASD & Post-hoc Referral")
        format_axis(ax, xlabel="Frequency (Hz)", ylabel="ASD (V/rtHz or rad/rtHz)")
        style_legend(ax, loc="lower left", fontsize=5.3, frameon=True)

        save_figure(fig, out_png, dpi=200, paired=False)

    mid_idx = int(np.argmin(np.abs(f_p[band_m] - 30.0)))
    posthoc_info = {
        "k0_v_per_rad": k0,
        "k0_se_v_per_rad": k0_se,
        "band_20_40_median_rad_sqrt_hz": float(np.median(phi_asd_posthoc)),
        "band_20_40_mid_hz": float(f_p[band_m][mid_idx]),
        "band_20_40_mid_rad_sqrt_hz": float(phi_asd_posthoc[mid_idx]),
        "band_raw_median_v_sqrt_hz": float(np.median(asd_raw[band_m])),
        "band_cln_median_v_sqrt_hz": float(np.median(asd_cln[band_m])),
    }
    return out_png, posthoc_info


def main() -> None:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    root = data_root()
    verif_dir = root / VERIF_REL
    run_sat = root / RUN_SAT_REL
    run_a04 = root / RUN_A04_REL
    run_1125 = root / RUN_1125_REL
    run_dg = root / RUN_DG_REL

    f1 = plot_stage2_stability_and_phase(verif_dir)
    f2 = plot_stage2_modulation_sweep(verif_dir)
    f3, xy_stats = plot_1125_root_causes(run_1125, run_dg)
    f4, posthoc = plot_posthoc_physical_recovery(run_sat, run_1125, xy_stats)

    cfg_a04 = yaml.safe_load((run_a04 / "experiment_config.yaml").read_text(encoding="utf-8"))
    cfg_1125 = yaml.safe_load((run_1125 / "experiment_config.yaml").read_text(encoding="utf-8"))

    print("=== GENERATED FIGURES ===")
    for p in (f1, f2, f3, f4):
        print(f"  {p} ({p.stat().st_size / 1024:.1f} KB)")

    print("\n=== LIVE RUN SUMMARY ===")
    print(f"  1124_191353 (A01-A04, 0.05 Vpp): K0 = {cfg_a04['calibration']['k0_v_per_rad']:.5f} +/- {cfg_a04['calibration']['k0_se_v_per_rad']:.5f} V/rad")
    print(f"  1125_074220 (Full,    0.05 Vpp): K0 = {cfg_1125['calibration']['k0_v_per_rad']:.5f} +/- {cfg_1125['calibration']['k0_se_v_per_rad']:.5f} V/rad, lockin_phase = {cfg_1125['calibration']['lockin_phase_deg']:.3f} deg")
    print("  1125_074220 XY points (with vs without 50Hz):")
    for row in xy_stats:
        print(
            f"    f={row['freq_hz']:4.0f} Hz (rep={row['repeat']}): Y_pk={row['y_pk_stock_v']*1e3:.3f} mV, "
            f"|H|={row['h_v_per_rad']:.5f} V/rad (|H|/|K0|={row['norm_gain']:.3f}), "
            f"stock_SNR={row['snr_stock']:.2f} vs notched_SNR={row['snr_clean']:.1f}"
        )
    print(f"  Post-hoc [20-40 Hz] (non-official, 50Hz notched): median={posthoc['band_20_40_median_rad_sqrt_hz']*1e3:.3f} mrad/rtHz "
          f"(mid@{posthoc['band_20_40_mid_hz']:.2f}Hz={posthoc['band_20_40_mid_rad_sqrt_hz']*1e3:.3f} mrad/rtHz)")


if __name__ == "__main__":
    main()
