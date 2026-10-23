"""四项拟合/灵敏度比较；图例保留用户选择的控制波形原名。"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from ...plotting import plot_style_context, save_figure

FULL_ANALYSIS_PANELS = (
    ("slope_V_per_nT", "Absolute dispersive slope (V/nT)"),
    ("dispersive_hwhm_Hz", "Dispersive HWHM (Hz)"),
    ("center_Hz", "Lorentzian center frequency (Hz)"),
    ("sensitivity_fT_per_sqrtHz", "Slope-referred sensitivity (fT/√Hz)"),
)


def waveform_font_family():
    """波形原名可包含中文，仅为名称选取本机可用字体。"""
    available = {font.name for font in font_manager.fontManager.ttflist}
    return [name for name in ("Microsoft YaHei", "Noto Sans SC", "Noto Sans CJK SC", "SimHei") if name in available] or ["Arial", "DejaVu Sans"]


def plot_results(directory, rows, spectra, *, comparison_band_hz=None):
    groups = {name: [r for r in rows if r["control"] == name] for name in dict.fromkeys(r["control"] for r in rows)}
    legend_family = waveform_font_family()
    with plot_style_context("paper"):
        figure, axes = plt.subplots(2, 2, figsize=(7.0, 5.2), layout="constrained")
        for axis, (key, ylabel) in zip(axes.flat, FULL_ANALYSIS_PANELS):
            for name, group in groups.items():
                # 缺失拟合用 NaN 断线，不跨过无效点连成一条有效曲线。
                axis.plot([r["scale"] for r in group], [r.get(key) if r.get(key) is not None else np.nan for r in group],
                          "o-", ms=3, label=name)
            axis.set(xlabel="Scan scale", ylabel=ylabel)
            axis.grid(alpha=0.2)
        axes[0, 0].set_ylim(bottom=0)
        axes[0, 0].legend(prop={"family": legend_family, "size": 7})
        if comparison_band_hz is not None:
            low, high = comparison_band_hz
            axes[1, 1].set_title(f"Median over {low:g}-{high:g} Hz")
        for suffix in ("png", "pdf"):
            save_figure(figure, directory / "results" / f"full_analysis.{suffix}")
        plt.close(figure)
        with np.load(directory / "results/fit_source_data.npz", allow_pickle=False) as source:
            for stem, xkey, ykey, fit_xkey, fit_ykey, xlabel, ylabel in (
                ("response_curves", "_field_nT", "_x_V", "_fit_field_nT", "_fit_x_V", "RF field (nT)", "RF quadrature X (V)"),
                ("frequency_curves", "_frequency_Hz", "_frequency_r_V", "_frequency_fit_Hz", "_frequency_fit_r_V", "RF frequency (Hz)", "RF magnitude R (V)"),
            ):
                figure, axis = plt.subplots(figsize=(7.0, 4.0), layout="constrained")
                for row in rows:
                    key = row["key"]
                    line, = axis.plot(source[key + xkey], source[key + ykey], "o", ms=2,
                                      label=f"{row['control']} · {row['scale']:g}")
                    if key + fit_xkey in source:
                        axis.plot(source[key + fit_xkey], source[key + fit_ykey], "-",
                                  color=line.get_color(), lw=0.9)
                axis.set(xlabel=xlabel, ylabel=ylabel)
                axis.legend(prop={"family": legend_family, "size": 6}, ncol=3)
                for suffix in ("png", "pdf"):
                    save_figure(figure, directory / "results" / f"{stem}.{suffix}")
                plt.close(figure)
        figure, axis = plt.subplots(figsize=(7.0, 4.0), layout="constrained")
        for row in rows:
            key = row["key"]
            if key + "_frequency_Hz" not in spectra:
                continue
            frequency = spectra[key + "_frequency_Hz"]
            mask = frequency > 0
            axis.loglog(frequency[mask], spectra[key + "_raw_fT_per_sqrtHz"][mask],
                        label=f"{row['control']} · {row['scale']:g}", lw=0.8)
        axis.set(xlabel="Baseband frequency (Hz)", ylabel="Slope-referred sensitivity (fT/√Hz)")
        if spectra:
            axis.legend(prop={"family": legend_family, "size": 6}, ncol=3)
        else:
            axis.text(0.5, 0.5, "Noise acquisition disabled", transform=axis.transAxes, ha="center")
        for suffix in ("png", "pdf"):
            save_figure(figure, directory / "results" / f"sensitivity_spectra.{suffix}")
        plt.close(figure)
