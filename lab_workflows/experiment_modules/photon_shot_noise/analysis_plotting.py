"""保留原四组科学图及英文标注，统一 paper PNG/PDF。"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from ...experiment_runtime import check_cancelled
from ...plotting import new_figure, save_figure, set_plot_style
from lab_workflows.workflow_state import read_state
from .analysis_steps import output_path, statistics


def render(run_dir):
    set_plot_style("paper")
    data = statistics(run_dir)
    report = read_state(output_path(run_dir, "results/fit_summary.json"))["report"]
    powers, fit = data["probe_powers"], report["fit"]
    pf = np.linspace(powers[0], powers[-1], 100)
    created = []
    try:
        for channel, color, marker in (("x", "C0", "o"), ("y", "C1", "s")):
            check_cancelled()
            fig, ax = new_figure(kind="standard", height_mm=65)
            created.append(fig)
            alpha, beta = fit["alpha_" + channel], fit["beta_" + channel]
            noise = np.sqrt(max(beta, 0))
            ax.errorbar(powers, data["mean_asd_" + channel], yerr=data["std_asd_" + channel],
                fmt=marker, capsize=4, color=color, label=f"Mean ± SD ({channel.upper()})")
            ax.plot(pf, np.sqrt(np.maximum(alpha * pf + beta, 0)), "--", color=color, alpha=0.7, label="Shot-noise fit")
            ax.axhline(noise, color="gray", ls=":", alpha=0.5, label=f"Elec. noise = {noise:.2e} V/√Hz")
            ax.legend()
            ax.set_xlabel("Probe control voltage (V)")
            ax.set_ylabel("ASD Noise Floor (V/√Hz)")
            ax.set_title(f"Shot Noise Calibration — {channel.upper()} Channel")
            ax.grid(False)
            fig.set_layout_engine("constrained")
            save_figure(fig, output_path(run_dir, f"results/shot_noise_{channel}.png"))

        check_cancelled()
        fig, ax = new_figure(kind="standard", height_mm=65)
        created.append(fig)
        ax.errorbar(powers, data["mean_asd_x"], yerr=data["std_asd_x"], fmt="o-", capsize=3,
                    color="C0", label="ASD X (V/√Hz)", markersize=5)
        ax.set_xlabel("Probe control voltage (V)")
        ax.set_ylabel("ASD Noise Floor (V/√Hz)", color="C0")
        ax.tick_params(axis="y", labelcolor="C0")
        other = ax.twinx()
        other.errorbar(powers, data["mean_rms_x"], yerr=data["std_rms_x"], fmt="s--", capsize=3,
                       color="C2", label="RMS X (V)", markersize=5)
        other.set_ylabel("AC RMS (V)", color="C2")
        other.tick_params(axis="y", labelcolor="C2")
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = other.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, loc="upper left")
        ax.set_title("ASD and AC RMS")
        ax.grid(False)
        fig.set_layout_engine("constrained")
        save_figure(fig, output_path(run_dir, "results/asd_vs_rms.png"))

        check_cancelled()
        fig, ax = new_figure(kind="standard", height_mm=65)
        created.append(fig)
        frequency, limit = data["frequencies"], float(data["flat_freq_limit"])
        band = (frequency > 0) & (frequency <= limit)
        for index, color in zip((0, len(powers) // 2, -1), ("#2166ac", "#f4a582", "#b2182b")):
            ax.plot(frequency[band], data["psd_xm"][index][band], color=color, alpha=0.8, lw=0.8,
                    label=f"P = {powers[index]:.3f} V")
            ax.axhline(np.median(data["psd_xm"][index][band]), color=color, ls="--", alpha=0.35)
        ax.set_yscale("log")
        ax.axvline(limit, color="green", alpha=0.5, ls="--", label=f"Flat limit ({limit:.0f} Hz)")
        ax.legend()
        ax.set_xlabel("Frequency (Hz)")
        ax.set_ylabel("PSD (V²/Hz)")
        ax.set_title("X-Channel PSD — White Noise Check")
        ax.grid(False)
        fig.set_layout_engine("constrained")
        save_figure(fig, output_path(run_dir, "results/psd_white_noise_check.png"))
    finally:
        for fig in created:
            plt.close(fig)
