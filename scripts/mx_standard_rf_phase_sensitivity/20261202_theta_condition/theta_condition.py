"""θ 走线适用条件的实测对比：宽带档 −5 kHz 载波镜像隔离前 / 后。

数据：1127_174802_mx_std_smoke_v2c（0.5 Vpp，宽带档 TC 1 µs、53571 Sa/s）。
输出 figures/theta_condition.png|pdf 与终端数值，供工作总结引用。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from lab_workflows.experiment_modules.mx_standard_rf_phase_sensitivity import analysis_steps as A  # noqa: E402
from lab_workflows.experiment_modules.z_chain_common import welch_psd  # noqa: E402

RUN = Path(r"D:\Code\exp_agent_run\data\mx_standard_rf_phase_sensitivity\1127_174802_mx_std_smoke_v2c")
OUT = Path(__file__).resolve().parent / "figures"
BAND = (3.0, 50.0)


def main() -> int:
    coarse = A.load_frames(RUN, ["mx_std_coarse_%02d" % i for i in range(24)])
    phasors = A.frame_phasors(coarse)
    fit = A.circle_fit(phasors.real, phasors.imag)
    cx, cy, radius = float(fit["center_x_v"]), float(fit["center_y_v"]), float(fit["radius_v"])
    frames = A.load_frames(RUN, ["mx_std_noise_%02d_xy" % i for i in range(4)], dynamic=True)
    rate = float(frames[0]["metadata"]["rate_sa_s"])
    phi = float(np.degrees(np.mean(np.unwrap(np.radians(
        A.phasor_angles_deg(A.frame_phasors(frames), fit))))))
    slope = radius * abs(math.cos(math.radians(phi)))

    raw_x = [np.asarray(f["x_v"], float) for f in frames]
    raw_y = [np.asarray(f["y_v"], float) for f in frames]
    iso_x = [A.lowpass_keep_dc(v, rate) for v in raw_x]
    iso_y = [A.lowpass_keep_dc(v, rate) for v in raw_y]

    def spread(xs, ys):
        values = []
        for x_v, y_v in zip(xs, ys):
            theta = np.arctan2(y_v - cy, x_v - cx)
            mean = np.angle(np.mean(np.exp(1j * theta)))
            values.append(np.angle(np.exp(1j * (theta - mean))))
        return values

    raw_theta = spread(raw_x, raw_y)
    iso_theta = spread(iso_x, iso_y)
    spread_raw = float(np.std(np.concatenate(raw_theta)))
    spread_iso = float(np.std(np.concatenate(iso_theta)))

    times = [f["time_s"] for f in frames]
    freqs, psd_theta_raw, _ = welch_psd.segment_average(
        [(t, v, rate) for t, v in zip(times, raw_theta)], 4.0)
    _, psd_theta_iso, _ = welch_psd.segment_average(
        [(t, v, rate) for t, v in zip(times, iso_theta)], 4.0)
    # y 走线保持原始帧（线性折算不需要隔离），只有 θ 用隔离后的帧。
    _, psd_y, _ = welch_psd.segment_average(
        [(t, v, rate) for t, v in zip(times, raw_y)], 4.0)
    asd_theta_raw = np.sqrt(psd_theta_raw)
    asd_theta_iso = np.sqrt(psd_theta_iso)
    asd_y = np.sqrt(psd_y) / slope
    mask = (freqs >= BAND[0]) & (freqs <= BAND[1])
    band_raw = float(np.median(asd_theta_raw[mask] / asd_y[mask]))
    band_iso = float(np.median(asd_theta_iso[mask] / asd_y[mask]))

    print("R = %.6f V, phi = %.3f deg, slope = %.6e V/rad" % (radius, phi, slope))
    print("theta spread raw = %.4f rad, isolated = %.4f rad" % (spread_raw, spread_iso))
    print("band 3-50 Hz median theta/y raw = %.4f, isolated = %.4f" % (band_raw, band_iso))

    OUT.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(11.0, 8.0), constrained_layout=True)
    window = slice(0, int(0.4 * rate))
    for ax, (xs, ys, title) in zip(axes[0], (
            (raw_x[0], raw_y[0], "(a) raw XY frame: -5 kHz image swings the phasor"),
            (iso_x[0], iso_y[0], "(b) same frame after DC-preserving low-pass"))):
        ax.plot((xs[window] - cx) * 1e3, (ys[window] - cy) * 1e3, lw=0.6, color="#1f77b4")
        ax.set_xlabel("x - x0 (mV)")
        ax.set_ylabel("y - y0 (mV)")
        ax.set_title(title, fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.set_aspect("equal", adjustable="datalim")
    axes[1, 0].loglog(freqs[1:], asd_theta_raw[1:] * 1e6, lw=0.8, color="#d62728",
                      label="theta raw (wrapped)")
    axes[1, 0].loglog(freqs[1:], asd_theta_iso[1:] * 1e6, lw=0.8, color="#2ca02c",
                      label="theta after low-pass")
    axes[1, 0].loglog(freqs[1:], asd_y[1:] * 1e6, lw=0.8, color="#1f77b4", label="y route (y / K)")
    axes[1, 0].axvspan(*BAND, color="grey", alpha=0.12)
    axes[1, 0].set_xlabel("Frequency (Hz)")
    axes[1, 0].set_ylabel("Phase ASD (urad/sqrt(Hz))")
    axes[1, 0].set_title("(c) theta route with and without image isolation", fontsize=10)
    axes[1, 0].legend(fontsize=8)
    axes[1, 0].grid(True, which="both", alpha=0.3)
    axes[1, 1].semilogx(freqs[1:], asd_theta_raw[1:] / asd_y[1:], lw=0.8, color="#d62728",
                        label="raw: median %.3f" % band_raw)
    axes[1, 1].semilogx(freqs[1:], asd_theta_iso[1:] / asd_y[1:], lw=0.8, color="#2ca02c",
                        label="isolated: median %.3f" % band_iso)
    axes[1, 1].axhline(1.0, color="k", ls="--", lw=0.8)
    axes[1, 1].axvspan(*BAND, color="grey", alpha=0.12)
    axes[1, 1].set_ylim(0.0, 3.0)
    axes[1, 1].set_xlabel("Frequency (Hz)")
    axes[1, 1].set_ylabel("theta / (y / K)")
    axes[1, 1].set_title("(d) route ratio: unity only after isolation", fontsize=10)
    axes[1, 1].legend(fontsize=8)
    axes[1, 1].grid(True, which="both", alpha=0.3)
    fig.suptitle("Standard Mx: theta route usability, wideband readout (0.5 Vpp, TC 1 us)", fontsize=11)
    for suffix in ("png", "pdf"):
        fig.savefig(OUT / ("theta_condition." + suffix), dpi=150)
    plt.close(fig)
    print("figure:", OUT / "theta_condition.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())