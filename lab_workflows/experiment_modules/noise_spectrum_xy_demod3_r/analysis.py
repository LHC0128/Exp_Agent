"""XY DirectAW Demod3 平均 R 矩阵离线分析入口。"""

from __future__ import annotations

import json
import os

import matplotlib
import numpy as np
import yaml

matplotlib.use(os.environ.get("MPLBACKEND", "TkAgg"))
import matplotlib.pyplot as plt

from lab_workflows.experiment_runtime import runtime_run_dir

from .analysis_core import analyze_r_matrix


def main() -> None:
    run_dir = runtime_run_dir()
    raw_path = run_dir / "raw" / "demod3_r_mean_matrix.npz"
    config_path = run_dir / "experiment_config.yaml"
    if not raw_path.is_file():
        raise FileNotFoundError(f"平均 R 原始矩阵不存在: {raw_path}")
    if not config_path.is_file():
        raise FileNotFoundError(f"实验配置不存在: {config_path}")

    with np.load(raw_path) as raw:
        r_matrix = np.asarray(raw["r_mean_V"], dtype=float)
        control_configured = np.asarray(raw["control_frequency_Hz"], dtype=float)
        envelope = np.asarray(raw["xy_envelope_voltage_V"], dtype=float)
        demod3_frequency = np.asarray(raw["demod3_frequency_Hz"], dtype=float)
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}

    results_dir = run_dir / "results"
    results_dir.mkdir(exist_ok=True)
    np.savez(
        results_dir / "demod3_r_matrix.npz",
        r_mean_V=r_matrix,
        control_frequency_configured_Hz=control_configured,
        xy_envelope_voltage_V=envelope,
        demod3_frequency_Hz=demod3_frequency,
    )

    analysis = analyze_r_matrix(r_matrix, envelope, demod3_frequency)
    calibration = analysis.calibration
    np.savez(
        results_dir / "calibration.npz",
        slope_Hz_per_V=np.float64(calibration.slope_hz_per_v),
        intercept_Hz=np.float64(calibration.intercept_hz),
        residual_sigma_Hz=np.float64(calibration.residual_sigma_hz),
        ridge_envelope_V=analysis.ridge_envelope_v,
        ridge_frequency_Hz=analysis.ridge_frequency_hz,
        inlier_mask=calibration.inlier_mask,
        calibrated_control_frequency_Hz=analysis.calibrated_control_frequency_hz,
        source=np.array("demod3_r_ridge_robust_fit"),
    )

    parameter_names = np.array(
        ["gamma_Hz", "fit_amplitude", "r_baseline_V", "frequency_offset_Hz"]
    )
    np.savez(
        results_dir / "r_fit_params.npz",
        parameters=analysis.fit_parameters,
        errors=analysis.fit_errors,
        fit_mask=analysis.fit_mask,
        parameter_names=parameter_names,
        demod3_frequency_Hz=demod3_frequency,
        calibrated_control_frequency_Hz=analysis.calibrated_control_frequency_hz,
    )
    np.savez(
        results_dir / "r_spectra.npz",
        demod3_frequency_Hz=demod3_frequency,
        fit_amplitude=analysis.fit_parameters[:, 1],
        r_baseline_V=analysis.fit_parameters[:, 2],
        gamma_Hz=analysis.fit_parameters[:, 0],
        frequency_offset_Hz=analysis.fit_parameters[:, 3],
        fit_mask=analysis.fit_mask,
    )

    extent = [
        float(demod3_frequency[0]),
        float(demod3_frequency[-1]),
        float(analysis.calibrated_control_frequency_hz[0]),
        float(analysis.calibrated_control_frequency_hz[-1]),
    ]
    fig, ax = plt.subplots(figsize=(9, 6))
    image = ax.imshow(r_matrix, aspect="auto", origin="lower", extent=extent, cmap="viridis")
    ax.set_xlabel("Demod3 frequency (Hz)")
    ax.set_ylabel("Calibrated control frequency (Hz)")
    ax.set_title("Mean Demod3 R matrix")
    fig.colorbar(image, ax=ax, label="Mean R (V)")
    fig.tight_layout()
    fig.savefig(results_dir / "demod3_r_matrix.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 5))
    inlier = calibration.inlier_mask
    ax.scatter(
        analysis.ridge_envelope_v[~inlier],
        analysis.ridge_frequency_hz[~inlier],
        s=18,
        color="tab:red",
        label="Rejected ridge points",
    )
    ax.scatter(
        analysis.ridge_envelope_v[inlier],
        analysis.ridge_frequency_hz[inlier],
        s=18,
        color="tab:blue",
        label="Calibration ridge points",
    )
    x_line = np.linspace(float(np.min(envelope)), float(np.max(envelope)), 300)
    ax.plot(
        x_line,
        calibration.slope_hz_per_v * x_line + calibration.intercept_hz,
        color="black",
        label="Robust linear fit",
    )
    ax.set_xlabel("DirectAW envelope (V)")
    ax.set_ylabel("Ridge frequency (Hz)")
    ax.set_title("Demod3 R ridge calibration")
    ax.legend()
    fig.tight_layout()
    fig.savefig(results_dir / "calibration.png", dpi=160)
    plt.close(fig)

    fig, axes = plt.subplots(2, 2, figsize=(11, 8), sharex=True)
    labels = (
        (0, "Linewidth (Hz)"),
        (1, "Fit amplitude"),
        (2, "R baseline (V)"),
        (3, "Frequency offset (Hz)"),
    )
    for ax, (parameter_idx, label) in zip(axes.flat, labels, strict=True):
        ax.plot(demod3_frequency, analysis.fit_parameters[:, parameter_idx], ".-", ms=3)
        ax.set_ylabel(label)
        ax.grid(alpha=0.25)
    for ax in axes[-1]:
        ax.set_xlabel("Demod3 frequency (Hz)")
    fig.suptitle("Demod3 R profile fits")
    fig.tight_layout()
    fig.savefig(results_dir / "r_spectra.png", dpi=160)
    plt.close(fig)

    summary = {
        "experiment_id": config.get("experiment_id"),
        "analysis_source": "mean Demod3 sample.r matrix",
        "calibration_source": "demod3_r_ridge_robust_fit",
        "calibration_slope_Hz_per_V": float(calibration.slope_hz_per_v),
        "calibration_intercept_Hz": float(calibration.intercept_hz),
        "calibration_residual_sigma_Hz": float(calibration.residual_sigma_hz),
        "fitted_frequency_points": int(np.count_nonzero(analysis.fit_mask)),
        "total_frequency_points": int(len(demod3_frequency)),
        "units_note": "R is voltage amplitude, not PSD; no S_beta/N_S1 fields are emitted.",
    }
    with (results_dir / "analysis.yaml").open("w", encoding="utf-8") as stream:
        yaml.safe_dump(summary, stream, allow_unicode=True, sort_keys=False)
    with (results_dir / "analysis.json").open("w", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
    print(f"Demod3 R 离线分析完成: {results_dir}")


if __name__ == "__main__":
    main()
