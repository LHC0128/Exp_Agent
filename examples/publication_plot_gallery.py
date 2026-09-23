"""用可复现的模拟数据生成四类投稿绘图样例，无需连接仪器。

仓库根目录运行：python -m examples.publication_plot_gallery
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import matplotlib

matplotlib.use("Agg")
import numpy as np
import scipy
from scipy.optimize import curve_fit
from scipy.signal import welch

from lab_workflows.plotting import (
    COLOR_GRAY, COLOR_OPTIMAL, COLOR_TRAD, format_axis,
    new_figure, plot_style_context, save_figure, style_legend,
)


def lorentzian(frequency, amplitude, center, fwhm, baseline):
    """幅值响应的洛伦兹示例模型；fwhm 明确为半高全宽，单位 Hz。"""
    return baseline + amplitude / (1 + (2 * (frequency - center) / fwhm) ** 2)


def simulate(seed: int = 20260920) -> tuple[dict, dict]:
    """生成模拟输入并计算 Welch ASD、加权拟合及重复测量统计量。"""
    rng = np.random.default_rng(seed)
    fs, nperseg, overlap, repeats = 1024, 2048, 1024, 6
    time = np.arange(8192) / fs
    arrays = {"time_s": time}
    band_values = []
    for name, floor in (("traditional", 34.0), ("optimal", 18.0)):
        traces = rng.normal(0, floor * np.sqrt(fs / 2), (repeats, time.size))
        traces += 300 * np.sin(2 * np.pi * 2 * time)
        traces += 120 * np.sin(2 * np.pi * 50 * time)
        frequency, psd = welch(
            traces, fs=fs, window="hann", nperseg=nperseg,
            noverlap=overlap, detrend="constant", scaling="density", axis=-1,
        )
        arrays[f"{name}_traces_fT"] = traces
        arrays[f"{name}_psd_fT2_per_Hz"] = psd
        arrays[f"{name}_asd_fT_per_sqrtHz"] = np.sqrt(psd.mean(axis=0))
        band = (frequency >= 100) & (frequency <= 200)
        band_values.append(np.median(np.sqrt(psd[:, band]), axis=1))
    arrays["frequency_Hz"] = frequency
    arrays["band_asd_fT_per_sqrtHz"] = np.asarray(band_values)

    detuning = np.linspace(-350, 350, 41)
    response = lorentzian(detuning, 2.0, 12.0, 110.0, 0.12)
    runs = response + rng.normal(0, 0.08, (repeats, detuning.size))
    mean = runs.mean(axis=0)
    sem = runs.std(axis=0, ddof=1) / np.sqrt(repeats)
    params, covariance = curve_fit(
        lorentzian, detuning, mean, p0=[2, 0, 100, 0.1],
        sigma=sem, absolute_sigma=True,
        bounds=([0, -100, 1, 0], [5, 100, 500, 1]),
    )
    dense = np.linspace(-350, 350, 501)
    control = np.linspace(0, 1, 51)
    heatmap = lorentzian(
        dense[None, :], 2 * (0.2 + control[:, None]),
        12 + 35 * control[:, None], 90 + 80 * control[:, None], 0.12,
    )
    arrays.update(
        detuning_Hz=detuning, response_runs_mV=runs, response_mean_mV=mean,
        response_sem_mV=sem, fit_parameters=params, fit_covariance=covariance,
        dense_detuning_Hz=dense, fitted_response_mV=lorentzian(dense, *params),
        control=control, response_map_mV=heatmap,
        residual_mV=mean - lorentzian(detuning, *params),
    )
    metadata = {
        "data_kind": "synthetic", "seed": seed, "independent_repeats": repeats,
        "spectrum": {"sample_rate_Hz": fs, "nperseg": nperseg, "noverlap": overlap,
                     "window": "hann", "detrend": "constant", "sides": "one-sided",
                     "scaling": "density", "average": "mean PSD, then square root",
                     "summary_band_Hz": [100, 200], "smoothing": "none"},
        "fit": {"model": "baseline + amplitude / (1 + (2*(f-center)/FWHM)**2)",
                "parameter_order": ["amplitude_mV", "center_Hz", "FWHM_Hz", "baseline_mV"],
                "parameters": params.tolist(), "standard_errors": np.sqrt(np.diag(covariance)).tolist(),
                "weighting": "SEM of 6 independent repeats; absolute_sigma=True"},
        "captions": {
            "01_noise_spectrum": "Synthetic magnetic-field ASD. Six independent traces per condition; mean one-sided Welch PSD followed by square root. No smoothing. The shaded region marks the 100–200 Hz summary band.",
            "02_response_fit": "Synthetic response: points are means and error bars are SEM of six independent repeats. Weighted Lorentzian fit; FWHM is reported in Hz. Lower panel shows mean-data residuals with the same SEM.",
            "03_response_heatmap": "Synthetic model response over frequency detuning and dimensionless control. A single absolute color scale is used; no row normalization. Heatmap is rasterized; axes and labels remain vector graphics.",
            "04_multipanel": "Synthetic examples: (a) ASD, (b) response fit, (c) response map, (d) median ASD within 100–200 Hz for each of six independent traces. Large symbols and error bars in (d) show mean ± sample SD; no statistical significance is claimed.",
        },
    }
    return arrays, metadata


def plot_spectrum(ax, data):
    """频谱图保留真实频点，以线型和颜色共同区分条件。"""
    f = data["frequency_Hz"]
    visible = (f >= 1) & (f <= 400)
    for name, color, linestyle in (("traditional", COLOR_TRAD, "--"), ("optimal", COLOR_OPTIMAL, "-")):
        ax.loglog(f[visible], data[f"{name}_asd_fT_per_sqrtHz"][visible],
                  color=color, linestyle=linestyle, label=name.capitalize(), linewidth=1)
    ax.axvspan(100, 200, color=COLOR_GRAY, alpha=0.12, linewidth=0)
    format_axis(ax, xlabel="Frequency (Hz)", ylabel=r"Magnetic ASD (fT/$\sqrt{\mathrm{Hz}}$)")
    ax.set_xlim(1, 400)
    style_legend(ax, loc="upper right")


def plot_response(ax, data):
    """数据和拟合同色，用标记和实线区分。"""
    ax.errorbar(data["detuning_Hz"], data["response_mean_mV"],
                yerr=data["response_sem_mV"], fmt="o", color=COLOR_OPTIMAL,
                markersize=3, elinewidth=0.7, capsize=1.5, label="Mean ± SEM")
    ax.plot(data["dense_detuning_Hz"], data["fitted_response_mV"],
            color=COLOR_OPTIMAL, label="Lorentzian fit")
    format_axis(ax, xlabel="Detuning (Hz)", ylabel="Response (mV)")
    ax.set_ylim(0, 2.75)
    ax.set_xlim(-370, 370)
    style_legend(ax, loc="upper left")


def plot_heatmap(ax, data):
    """仅栅格化密集网格，使用绝对数值色标。"""
    mesh = ax.pcolormesh(data["dense_detuning_Hz"], data["control"], data["response_map_mV"],
                         shading="auto", cmap="viridis", vmin=0, vmax=2.6, rasterized=True)
    format_axis(ax, xlabel="Detuning (Hz)", ylabel="Control (dimensionless)")
    ax.figure.colorbar(mesh, ax=ax, label="Response (mV)", pad=0.025)


def plot_comparison(ax, data):
    """呈现独立重复值和样本标准差，不把频点当作独立重复。"""
    values = data["band_asd_fT_per_sqrtHz"]
    for index, (color, marker) in enumerate(((COLOR_TRAD, "s"), (COLOR_OPTIMAL, "o"))):
        ax.scatter(index + np.linspace(-0.11, 0.11, values.shape[1]), values[index],
                   color=color, marker=marker, s=12, alpha=0.65, edgecolors="none")
        ax.errorbar(index, values[index].mean(), yerr=values[index].std(ddof=1),
                    fmt=marker, color="black", markerfacecolor="white", markersize=5,
                    capsize=4, linewidth=1, label="Mean ± SD" if index == 0 else None)
    ax.set_xticks([0, 1], ["Traditional", "Optimal"])
    ax.set_xlim(-0.5, 1.5)
    ax.set_ylim(0, 46)
    format_axis(ax, ylabel=r"Band ASD (fT/$\sqrt{\mathrm{Hz}}$)")
    ax.text(0.97, 0.95, "100–200 Hz; n = 6", transform=ax.transAxes, ha="right", va="top")
    style_legend(ax, loc="upper left")


def generate(output: Path, profile: str = "paper", seed: int = 20260920) -> Path:
    """生成独立图和组合图，并保存输入、分析结果与复现记录。"""
    output.mkdir(parents=True, exist_ok=True)
    data, metadata = simulate(seed)
    with plot_style_context(profile):
        fig, ax = new_figure(profile=profile, height_mm=67)
        plot_spectrum(ax, data)
        fig.suptitle("Synthetic data", color="0.4", fontsize=7)
        save_figure(fig, output / "01_noise_spectrum.pdf")

        fig, (ax, residual) = new_figure(profile=profile, height_mm=88, nrows=2,
                                         sharex=True, height_ratios=[3, 1])
        plot_response(ax, data)
        ax.set_xlabel("")
        residual.errorbar(data["detuning_Hz"], data["residual_mV"],
                          yerr=data["response_sem_mV"], fmt="o", color=COLOR_OPTIMAL,
                          markersize=2.5, elinewidth=0.7)
        residual.axhline(0, color=COLOR_GRAY, linestyle="--", linewidth=0.8)
        format_axis(residual, xlabel="Detuning (Hz)", ylabel="Residual (mV)")
        fig.suptitle("Synthetic data", color="0.4", fontsize=7)
        save_figure(fig, output / "02_response_fit.pdf")

        fig, ax = new_figure(profile=profile, height_mm=67)
        plot_heatmap(ax, data)
        fig.suptitle("Synthetic model", color="0.4", fontsize=7)
        save_figure(fig, output / "03_response_heatmap.pdf")

        fig, axes = new_figure(profile=profile, kind="wide", height_mm=135, nrows=2, ncols=2)
        for label, ax, plot in zip("abcd", axes.flat, (plot_spectrum, plot_response, plot_heatmap, plot_comparison)):
            plot(ax, data)
            ax.set_title(f"({label})", loc="left", fontweight="bold")
        fig.suptitle("Synthetic data — publication plotting examples", color="0.4", fontsize=8)
        save_figure(fig, output / "04_multipanel.pdf")

    np.savez_compressed(output / "source_data.npz", **data)
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False)
    sources = [Path(__file__), root / "lab_workflows/plotting/poster_style.py"]
    metadata.update(profile=profile, numpy=np.__version__, scipy=scipy.__version__,
                    matplotlib=matplotlib.__version__, git_revision=revision.stdout.strip(),
                    source_sha256={str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    (output / "manifest.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    captions = "\n\n".join(f"## {name}\n\n{text}" for name, text in metadata["captions"].items())
    (output / "captions.md").write_text("# 模拟数据样图\n\n所有数据均为模拟，不代表仪器实测性能。\n\n" + captions + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("results/publication_plot_gallery"))
    parser.add_argument("--profile", choices=["paper", "nature", "aps"], default="paper")
    parser.add_argument("--seed", type=int, default=20260920)
    args = parser.parse_args()
    print(generate(args.output, args.profile, args.seed).resolve())


if __name__ == "__main__":
    main()
