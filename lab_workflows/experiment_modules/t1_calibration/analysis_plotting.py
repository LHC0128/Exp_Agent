"""保持旧科学图文件名，所有标注使用英文与公共 paper 样式。"""
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
from lab_workflows.plotting import new_figure, save_figure, set_plot_style
from lab_workflows.workflow_state import write_state
from .analysis_steps import load_inputs, output_path
from .fitting import exp_decay

PLOT_FILES = tuple(name+suffix for name in ("PDB_T1_decay", "T1_fit_residual", "T1_vs_probe_power",
    "inv_T1_vs_probe_power", "T1_all_decay_curves") for suffix in (".png", ".pdf"))


def render(run_dir):
    set_plot_style("paper")
    data = load_inputs(run_dir)
    results = output_path(run_dir, "results")
    summary = json.loads((results/"analysis.json").read_text(encoding="utf-8"))
    with np.load(results/"fit_points.npz", allow_pickle=False) as fits:
        p, t1, err, popt, valid = (fits[k] for k in ("probe_power", "T1", "T1_err", "popt", "valid_mask"))
    artifacts = []
    def save(fig, name):
        save_figure(fig, results/(name+".png"), close=True)
        artifacts.extend([name+".png", name+".pdf"])
    if data["scan"]:
        fig, ax = new_figure(kind="standard", height_mm=65)
        ax.errorbar(p, t1*1e3, yerr=err*1e3, fmt="o-", capsize=3)
        ax.set(xlabel="Probe control voltage (V)", ylabel="$T_1$ (ms)", title="$T_1$ vs Probe Control Voltage")
        save(fig, "T1_vs_probe_power")
        if summary["linear_fit_available"]:
            fig, ax = new_figure(kind="standard", height_mm=65)
            v = p[valid]
            ax.errorbar(v, 1/t1[valid], yerr=err[valid]/t1[valid]**2, fmt="o", capsize=3, label="Data")
            axis = np.linspace(v.min(), v.max(), 50)
            ax.plot(axis, summary["alpha"]*axis+summary["T1_0_inv"], "r--",
                label=f"Intercept = {summary['T1_0_inv']:.2f} s$^{{-1}}$, slope = {summary['alpha']:.3f} s$^{{-1}}$/V")
            ax.set(xlabel="Probe control voltage (V)", ylabel="$T_1^{-1}$ (s$^{-1}$)", title="Inverse Relaxation Rate")
            ax.legend()
            save(fig, "inv_T1_vs_probe_power")
        if summary["has_waveforms"]:
            n = len(p); cols = min(3, n); rows = (n+cols-1)//cols
            fig, axes = new_figure(nrows=rows, ncols=cols, kind="wide", height_mm=max(65, 55*rows), squeeze=False)
            for i, ax in enumerate(axes.flat):
                if i >= n:
                    ax.set_visible(False)
                    continue
                point = data["points"][i]
                t, y = point["t_axis"], point["voltage"]
                mask = t >= 0
                ax.plot(t[mask]*1e3, y[mask], linewidth=.4, label="Data")
                if valid[i]:
                    ax.plot(t[mask]*1e3, exp_decay(t[mask], *popt[i]), "r--", linewidth=1)
                    ax.text(.97, .92, f"$T_1$ = {t1[i]*1e3:.1f} ms", transform=ax.transAxes, ha="right", va="top")
                ax.set(title=f"Probe = {p[i]:.3f} V", xlabel="Time (ms)", ylabel="PDB differential signal (V)")
            fig.suptitle("$T_1$ decay curves - all probe settings")
            save(fig, "T1_all_decay_curves")
    else:
        point = data["points"][0]
        t, y = point["t_axis"], point["voltage"]
        mask = t >= 0
        fig, ax = new_figure(kind="standard", height_mm=65)
        ax.plot(t*1e3, y, linewidth=.5, label="PDB differential signal")
        if valid[0]:
            ax.plot(t[mask]*1e3, exp_decay(t[mask], *popt[0]), "r--",
                label=f"$T_1$ = {t1[0]*1e3:.2f} +/- {err[0]*1e3:.2f} ms")
        ax.axvline(0, color="gray", linestyle=":", label="Pump OFF")
        ax.set(xlabel="Time (ms)", ylabel="PDB differential signal (V)", title="Longitudinal Relaxation")
        ax.legend()
        save(fig, "PDB_T1_decay")
        if valid[0]:
            fig, ax = new_figure(kind="standard", height_mm=65)
            ax.plot(t[mask]*1e3, (y[mask]-exp_decay(t[mask], *popt[0]))*1e3, linewidth=.5)
            ax.set(xlabel="Time (ms)", ylabel="Residual (mV)", title="Exponential Fit Residual")
            save(fig, "T1_fit_residual")
    write_state(results/"plot_manifest.json", dict(artifacts=artifacts))
    return artifacts
