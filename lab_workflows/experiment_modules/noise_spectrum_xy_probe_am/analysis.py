"""Probe AOM 外部 AM 噪声谱离线分析；不连接仪器。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import yaml

from ...experiment_runtime import runtime_run_dir
from ...plotting import format_axis, new_figure, save_figure, set_plot_style
from ..noise_spectrum_xy.processing import SEPARATION_METHOD, fit_local_spectra, locate_ridge
from ..noise_spectrum_xy_known_noise.analysis import (
    calibration_band,
    compute_psd_matrix,
    _reason_counts,
    plot_controlled_uncontrolled,
    plot_line_shapes,
    plot_two_dimensional,
    select_line_shape_columns,
    save_known_noise_preview,
)
from ..noise_spectrum_xy.models import NoiseSpectrumXYParams


EXPERIMENT_ID = "noise-spectrum-xy-uncontrolled-probe-am"


def analyze(run_dir: Path) -> dict:
    run_dir = Path(run_dir).resolve()
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    if config["experiment_id"] != EXPERIMENT_ID:
        raise ValueError("运行目录不是 XY Probe AOM AM 不可控噪声实验")
    saved = config["parameters"]
    defaults = NoiseSpectrumXYParams()
    options = {
        key: float(saved.get("ANALYSIS_" + key.upper(), getattr(defaults, "analysis_" + key)))
        for key in ("bin_width_hz", "frequency_min_hz", "frequency_max_hz", "fit_half_width_hz")
    }
    results = run_dir / "results"
    results.mkdir(exist_ok=True)
    existing = [path for path in results.iterdir() if path.is_file()]
    backup = None
    if existing:
        backup = results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        backup.mkdir()
        for path in existing:
            shutil.copy2(path, backup / path.name)
    summary = {"analysis_version": 2, "status": "running", "options": options,
               "method": SEPARATION_METHOD, "backup_directory": backup.name if backup else None,
               "warnings": [], "measurement_units": "HF2 demodulator PSD (V²/Hz)",
               "controlled_response_units": "V²·Hz"}
    (results / "analysis_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    set_plot_style("paper")
    try:
        psd_on, frequency, voltage, rows_on, psd_info = compute_psd_matrix(run_dir, config, options, "on")
        psd_off, _, _, rows_off, _ = compute_psd_matrix(run_dir, config, options, "off")
        rows = np.intersect1d(rows_on, rows_off)
        average = 0.5 * (psd_on + psd_off)
        summary["psd"] = {**psd_info, "paired_points": int(rows.size)}
        np.savez_compressed(results / "psd_matrices.npz", psd_on=psd_on, psd_off=psd_off,
                            average_psd=average, freq_axis=frequency,
                            amplitudes=voltage, paired_rows=rows)
        if rows.size < 20:
            raise ValueError(f"ON/OFF 成对有效控制点不足 20 个（{rows.size}）")

        band = ((frequency >= options["frequency_min_hz"])
                & (frequency <= options["frequency_max_hz"]))
        f = frequency[band]
        psd_on_band, psd_off_band = psd_on[rows][:, band], psd_off[rows][:, band]
        ridge_band = calibration_band(frequency, options, saved)
        try:
            ridge = locate_ridge(psd_off[rows][:, ridge_band], frequency[ridge_band], voltage[rows])
        except ValueError as exc:
            if str(exc) != "无足够显著的移动峰，拒绝生成控制轴":
                raise
            k = float(saved["XY_CTRL_K_HZ_PER_V"])
            b = float(saved["XY_CTRL_B_HZ"])
            if not np.isfinite(k) or k <= 0 or not np.isfinite(b):
                raise ValueError("移动峰不足，且运行参数中的正弦控制 K/B 无效") from exc
            omega_ctrl = k * voltage[rows] + b
            if not np.all(np.isfinite(omega_ctrl)) or np.any(np.diff(omega_ctrl) <= 0):
                raise ValueError("移动峰不足，且运行参数中的 K/B 未生成严格递增控制轴") from exc
            ridge = {
                "k": k, "b": b, "omega_ctrl": omega_ctrl,
                "spur_mask": np.zeros(np.count_nonzero(ridge_band), dtype=bool),
                "V_cal": voltage[rows], "provisional": True,
            }
            summary["warnings"].append(
                "未检测到足够显著的移动峰；本次按运行参数 K/B 推算峰位，图和拟合仅供暂时分析。"
            )
        spur_mask = ridge["spur_mask"][band[ridge_band]]
        support = (float(np.min(ridge["k"] * ridge["V_cal"] + ridge["b"])),
                   float(np.max(ridge["k"] * ridge["V_cal"] + ridge["b"])))
        plot_two_dimensional(
            results, average[rows][:, band], f, voltage[rows], ridge,
            "noise_spectrum_2d.png",
            "Average ON/OFF PSD (provisional K/B axis)" if ridge.get("provisional") else "Average ON/OFF PSD",
            r"$\log_{10}$ PSD (V$^2$/Hz)",
        )
        fit_on = fit_local_spectra(psd_on_band, f, ridge["omega_ctrl"], spur_mask,
                                   options["fit_half_width_hz"], support, full_range=True)
        fit_off = fit_local_spectra(psd_off_band, f, ridge["omega_ctrl"], spur_mask,
                                    options["fit_half_width_hz"], support, full_range=True)
        controlled_valid = fit_on["fit_mask"] & fit_off["fit_mask"]
        valid = fit_on["background_fit_mask"] & fit_off["background_fit_mask"]
        n_on, n_off = fit_on["N_S1"], fit_off["N_S1"]
        delta = np.where(valid, n_on - n_off, np.nan)
        controlled_on, controlled_off = fit_on["S_beta"], fit_off["S_beta"]
        delta_controlled = np.where(
            controlled_valid, controlled_on - controlled_off, np.nan)
        plot_controlled_uncontrolled(
            results, f, controlled_on, controlled_off, None, n_on, n_off,
            controlled_valid, valid,
            controlled_ylabel=r"$S_\beta$ response coefficient (V$^2$·Hz)",
        )
        plot_line_shapes(
            results, f, ridge["omega_ctrl"], psd_on_band, psd_off_band,
            fit_on["popt"], fit_off["popt"],
            fit_on["rejection_reason"], fit_off["rejection_reason"],
            select_line_shape_columns(
                f, valid | controlled_valid, 8),
            options["fit_half_width_hz"], ridge["k"], ridge["b"],
            background_on=n_on, background_off=n_off,
        )
        np.savez_compressed(
            results / "popt_fit.npz", freq_axis=f,
            **{f"{key}_{state}": value for state, fit in (("on", fit_on), ("off", fit_off))
               for key, value in fit.items() if key != "model_matrix"},
        )
        np.savez_compressed(
            results / "noise_spectra.npz", freq_axis=f, N_S1_on=n_on, N_S1_off=n_off,
            delta_N_S1=delta, valid_mask=valid,
            S_beta_on=controlled_on, S_beta_off=controlled_off,
            delta_S_beta=delta_controlled, controlled_valid_mask=controlled_valid,
            valid_mask_on=fit_on["background_fit_mask"],
            valid_mask_off=fit_off["background_fit_mask"],
            controlled_valid_mask_on=fit_on["fit_mask"],
            controlled_valid_mask_off=fit_off["fit_mask"],
            controlled_rejection_reason_on=fit_on["rejection_reason"],
            controlled_rejection_reason_off=fit_off["rejection_reason"],
            background_method_on=fit_on["background_method"],
            background_method_off=fit_off["background_method"],
            rejection_reason_on=fit_on["background_rejection_reason"],
            rejection_reason_off=fit_off["background_rejection_reason"],
            popt_on=fit_on["popt"], popt_off=fit_off["popt"],
            ridge_k=ridge["k"], ridge_b=ridge["b"], control_frequency=ridge["omega_ctrl"],
        )
        np.savetxt(results / "noise_spectra.csv",
                   np.column_stack((f, controlled_on, controlled_off, delta_controlled,
                                    n_on, n_off, delta, controlled_valid.astype(int),
                                    valid.astype(int))), delimiter=",",
                   header="frequency_Hz,S_beta_on_V2_Hz,S_beta_off_V2_Hz,delta_S_beta_V2_Hz,N_S1_on_V2_per_Hz,N_S1_off_V2_per_Hz,delta_N_S1_V2_per_Hz,controlled_valid,background_valid",
                   comments="")

        fig, axes = new_figure(kind="wide", height_mm=110, nrows=2,
                               constrained_layout=True)
        for ax, baseline, difference, mask, baseline_label, delta_label in (
            (axes[1], controlled_off, delta_controlled, controlled_valid,
             r"$S_\beta$ OFF (V$^2$·Hz)", r"$\Delta S_\beta$ (V$^2$·Hz)"),
            (axes[0], n_off, delta, valid,
             r"$N_{S_1}$ OFF (V$^2$/Hz)", r"$\Delta N_{S_1}$ (V$^2$/Hz)"),
        ):
            difference_axis = ax.twinx()
            ax.semilogy(f[mask], baseline[mask], ".", ms=2.5,
                        color="tab:blue", label="Noise OFF baseline")
            difference_axis.plot(f[mask], difference[mask], ".", ms=2.5,
                                 color="tab:orange", label="Signed ON - OFF")
            difference_axis.axhline(0, color="black", lw=0.8)
            format_axis(ax, xlabel="Frequency (Hz)", ylabel=baseline_label)
            difference_axis.set_ylabel(delta_label)
            handles, labels = ax.get_legend_handles_labels()
            right_handles, right_labels = difference_axis.get_legend_handles_labels()
            ax.legend(handles + right_handles, labels + right_labels, loc="best")
        axes[0].set_title("Uncontrolled background: OFF baseline and signed difference")
        axes[1].set_title("Controlled response: OFF baseline and signed difference")
        save_figure(fig, results / "signed_spectral_difference.png")
        known = config["probe_am_noise"]
        save_known_noise_preview(results, run_dir, known, options["bin_width_hz"])
        provisional = bool(ridge.get("provisional"))
        summary.update(status="completed" if valid.any() and not provisional else "quality_failed",
                       calibration={"k": float(ridge["k"]), "b": float(ridge["b"]),
                                     "support_points": int(len(ridge["V_cal"])),
                                     "source": "configured_K_B" if provisional else "moving_peaks"},
                       fit_quality={
                           "valid_background_bins": int(valid.sum()), "total_bins": int(f.size),
                           "valid_controlled_bins": int(controlled_valid.sum()),
                           "accepted_on": int(fit_on["background_fit_mask"].sum()),
                           "accepted_off": int(fit_off["background_fit_mask"].sum()),
                           "background_method_on": _reason_counts(fit_on["background_method"]),
                           "background_method_off": _reason_counts(fit_off["background_method"]),
                           "controlled_rejection_on": _reason_counts(fit_on["rejection_reason"]),
                           "controlled_rejection_off": _reason_counts(fit_off["rejection_reason"]),
                           "rejection_on": _reason_counts(fit_on["background_rejection_reason"]),
                           "rejection_off": _reason_counts(fit_off["background_rejection_reason"]),
                       },
                       signed_differences_retained=True,
                       interpretation="Lock-in measurement-end noise spectrum; not calibrated to optical power.")
        summary["warnings"].append(
            "不可控背景独立验收；峰未通过时仍可由两侧稳定远端平台提取 N_S1。"
            "受控响应仅展示各态通过的部分，差谱只使用对应两态共同有效点。"
            "popt/perr 仅描述洛伦兹拟合，平台背景以 N_S1 和 background_method 为准。")
        summary["fit_quality"]["negative_delta_bins"] = int(np.sum(valid & (delta < 0)))
        summary["fit_quality"]["positive_delta_bins"] = int(np.sum(valid & (delta > 0)))
        summary["fit_quality"]["negative_controlled_delta_bins"] = int(
            np.sum(controlled_valid & (delta_controlled < 0)))
        summary["fit_quality"]["positive_controlled_delta_bins"] = int(
            np.sum(controlled_valid & (delta_controlled > 0)))
        if provisional:
            summary["error"] = "移动峰自动标定未通过；已按运行参数 K/B 生成暂定控制轴，结果仅供暂时分析"
        elif not valid.any():
            summary["error"] = "没有频率点通过 ON/OFF 背景拟合质量验收"
    except Exception as exc:
        summary.update(status="quality_failed" if isinstance(exc, ValueError) else "failed", error=str(exc))
        raise
    finally:
        (results / "analysis_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    summary = analyze(runtime_run_dir())
    return 0 if summary["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
