"""Mx Keithley 6221 主磁场频率标定离线分析。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import yaml

from ...experiment_runtime import runtime_run_dir
from ...plotting import (
    COLOR_GRAY,
    COLOR_OPTIMAL,
    COLOR_TRAD,
    PAPER_WIDE,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)
from ..mx_main_field_calibration.analysis import (
    _builtin,
    _discrete_peak_frequency,
    _weighted_linear_fit,
)
from ..mx_y_rf_sensitivity.analysis_core import (
    fit_lorentzian_response,
    lorentzian_response,
)
from .models import MxKeithley6221MainFieldCalibrationParams

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))


def _load_params(
    run_dir: Path,
) -> tuple[MxKeithley6221MainFieldCalibrationParams, dict[str, Any]]:
    config_path = run_dir / "experiment_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"未找到实验配置: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    params = MxKeithley6221MainFieldCalibrationParams.from_external(
        config.get("parameters", {}),
        schema_version=int(config.get("schema_version", 1)),
    )
    return params, config


def _load_scan_index(raw_dir: Path) -> list[dict[str, Any]]:
    path = raw_dir / "keithley_main_field_scan_index.npz"
    if path.exists():
        with np.load(path) as data:
            current = np.asarray(data["keithley_current_ma"], dtype=float)
            predicted = np.asarray(data["predicted_center_hz"], dtype=float)
            files = np.asarray(data["summary_file"], dtype=str)
        if not (current.size == predicted.size == files.size):
            raise ValueError("Keithley 6221 扫描索引字段长度不一致")
        return [
            {
                "keithley_current_ma": float(current_ma),
                "predicted_center_hz": float(center_hz),
                "summary_file": str(filename),
            }
            for current_ma, center_hz, filename in zip(
                current, predicted, files, strict=True
            )
        ]

    entries: list[dict[str, Any]] = []
    for summary_path in sorted(raw_dir.glob("current_*/frequency_scan.npz")):
        with np.load(summary_path) as data:
            entries.append(
                {
                    "keithley_current_ma": float(data["keithley_current_ma"]),
                    "predicted_center_hz": float(data["predicted_center_hz"]),
                    "summary_file": str(summary_path.relative_to(raw_dir.parent)),
                }
            )
    if entries:
        return entries
    raise FileNotFoundError(f"缺少 6221 扫描索引和已完成频扫汇总: {path}")


def _analyze_curve(run_dir: Path, entry: dict[str, Any]) -> dict[str, Any]:
    path = run_dir / str(entry["summary_file"])
    if not path.exists():
        raise FileNotFoundError(f"缺少频率扫描汇总: {path}")
    with np.load(path) as data:
        frequency_hz = np.asarray(data["frequency_hz"], dtype=float)
        r_mean_v = np.asarray(
            data["r_mean_v"]
            if "r_mean_v" in data.files
            else data["r_scalar_mean_v"],
            dtype=float,
        )
        r_std_v = np.asarray(data["r_std_v"], dtype=float)
    valid = np.isfinite(frequency_hz) & np.isfinite(r_mean_v) & np.isfinite(r_std_v)
    fit = fit_lorentzian_response(
        frequency_hz[valid],
        r_mean_v[valid],
        r_squared_min=None,
        relative_gamma_uncertainty_max=None,
    )
    reasons = list(fit.rejection_reasons)
    center_uncertainty_hz = float(fit.uncertainties[2])
    if fit.gamma > 0 and np.isfinite(fit.center) and np.any(valid):
        finite_frequency = frequency_hz[valid]
        edge_distance = min(
            fit.center - float(np.min(finite_frequency)),
            float(np.max(finite_frequency)) - fit.center,
        )
        if edge_distance < fit.gamma:
            reasons.append("拟合中心距扫描边缘不足一个 HWHM")
    return {
        **entry,
        "frequency_hz": frequency_hz,
        "r_mean_v": r_mean_v,
        "r_std_v": r_std_v,
        "discrete_peak_frequency_hz": _discrete_peak_frequency(
            frequency_hz, r_mean_v
        ),
        "fit": {
            **fit.to_dict(),
            "success": bool(fit.success and not reasons),
            "rejection_reasons": reasons,
            "center_uncertainty_hz": center_uncertainty_hz,
            "model": "C + A*gamma^2/((f-f0)^2+gamma^2)",
        },
    }


def _plot_frequency_responses(
    results_dir: Path, curves: list[dict[str, Any]]
) -> str:
    columns = 2
    rows = max(1, int(np.ceil(len(curves) / columns)))
    fig, axes = new_figure(
        figsize=(PAPER_WIDE[0], 2.7 * rows),
        nrows=rows,
        ncols=columns,
        squeeze=False,
    )
    for axis, curve in zip(axes.flat, curves, strict=False):
        frequency = np.asarray(curve["frequency_hz"], dtype=float)
        response = np.asarray(curve["r_mean_v"], dtype=float)
        fit = curve["fit"]
        axis.plot(
            frequency / 1e3,
            response,
            "o",
            color=COLOR_OPTIMAL,
            label="Measured R",
        )
        if np.all(np.isfinite(fit["parameters"])):
            dense = np.linspace(
                float(np.nanmin(frequency)), float(np.nanmax(frequency)), 800
            )
            axis.plot(
                dense / 1e3,
                lorentzian_response(dense, *fit["parameters"]),
                color=COLOR_TRAD,
                linestyle="--",
                label="Lorentzian fit",
            )
        status = "accepted" if fit["success"] else "excluded"
        axis.set_title(
            f"Keithley current = {curve['keithley_current_ma']:.3f} mA ({status})"
        )
        format_axis(axis, xlabel="Y RF frequency (kHz)", ylabel="Demod R (V)")
        style_legend(axis)
    for axis in axes.flat[len(curves) :]:
        axis.set_visible(False)
    filename = "frequency_response_fits.png"
    save_figure(fig, results_dir / filename)
    return filename


def _plot_calibration(
    results_dir: Path,
    curves: list[dict[str, Any]],
    linear: dict[str, Any],
) -> tuple[str, str]:
    current_all = np.asarray(
        [curve["keithley_current_ma"] for curve in curves], dtype=float
    )
    center_all = np.asarray(
        [curve["fit"]["parameters"][2] for curve in curves], dtype=float
    )
    sigma_all = np.asarray(
        [curve["fit"]["center_uncertainty_hz"] for curve in curves], dtype=float
    )
    accepted = np.asarray([curve["fit"]["success"] for curve in curves], dtype=bool)

    fig, axis = new_figure()
    if np.any(accepted):
        axis.errorbar(
            current_all[accepted],
            center_all[accepted] / 1e3,
            yerr=sigma_all[accepted] / 1e3,
            fmt="o",
            capsize=3,
            color=COLOR_OPTIMAL,
            label="Accepted centers",
        )
    if np.any(~accepted & np.isfinite(center_all)):
        axis.plot(
            current_all[~accepted],
            center_all[~accepted] / 1e3,
            "x",
            color=COLOR_GRAY,
            label="Excluded centers",
        )
    if linear["success"]:
        grid = np.linspace(float(current_all.min()), float(current_all.max()), 400)
        fitted = linear["slope_hz_per_ma"] * grid + linear["intercept_hz"]
        axis.plot(
            grid,
            fitted / 1e3,
            color=COLOR_TRAD,
            linestyle="--",
            label=(
                f"Linear fit: K={linear['slope_hz_per_ma']:.3f} Hz/mA, "
                f"f0={linear['intercept_hz']:.3f} Hz, "
                f"R2={linear['r_squared']:.5f}"
            ),
        )
    format_axis(
        axis,
        xlabel="Keithley 6221 current (mA)",
        ylabel="Resonance center (kHz)",
    )
    style_legend(axis)
    calibration_name = "keithley_main_field_frequency_calibration.png"
    save_figure(fig, results_dir / calibration_name)

    fig, axis = new_figure()
    if linear["success"] and np.any(accepted):
        axis.axhline(0.0, color=COLOR_GRAY, linestyle="--")
        axis.plot(
            current_all[accepted],
            np.asarray(linear["residual_hz"], dtype=float),
            "o-",
            color=COLOR_OPTIMAL,
        )
    format_axis(
        axis,
        xlabel="Keithley 6221 current (mA)",
        ylabel="Linear-fit residual (Hz)",
    )
    residual_name = "keithley_main_field_frequency_residuals.png"
    save_figure(fig, results_dir / residual_name)
    return calibration_name, residual_name


def analyze(run_dir: Path) -> dict[str, Any]:
    set_plot_style("paper")
    run_dir = Path(run_dir).resolve()
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    params, config = _load_params(run_dir)
    curves = [_analyze_curve(run_dir, entry) for entry in _load_scan_index(raw_dir)]

    accepted = np.asarray([curve["fit"]["success"] for curve in curves], dtype=bool)
    current_values = np.asarray(
        [curve["keithley_current_ma"] for curve in curves], dtype=float
    )
    centers = np.asarray(
        [curve["fit"]["parameters"][2] for curve in curves], dtype=float
    )
    center_uncertainties = np.asarray(
        [curve["fit"]["center_uncertainty_hz"] for curve in curves], dtype=float
    )
    linear = _weighted_linear_fit(
        current_values[accepted], centers[accepted], center_uncertainties[accepted]
    )
    reasons = list(linear["rejection_reasons"])
    if (
        not np.isfinite(linear["r_squared"])
        or linear["r_squared"] < params.linear_r_squared_min
    ):
        reasons.append(
            f"线性 R^2={linear['r_squared']:.6g} < {params.linear_r_squared_min:.6g}"
        )
    success = bool(linear["success"] and not reasons)

    response_plot = _plot_frequency_responses(results_dir, curves)
    calibration_plot, residual_plot = _plot_calibration(results_dir, curves, linear)
    uncertainties = np.asarray(linear["uncertainties"], dtype=float)
    payload = {
        "success": success,
        "experiment_id": config.get(
            "experiment_id", "mx-keithley-6221-main-field-calibration"
        ),
        "run_dir": str(run_dir),
        "model": "f_Hz = K_f_Hz_per_mA * current_mA + f_0mA_Hz",
        "K_f_Hz_per_mA": linear["slope_hz_per_ma"],
        "K_f_uncertainty_Hz_per_mA": uncertainties[0],
        "f_0mA_Hz": linear["intercept_hz"],
        "f_0mA_uncertainty_Hz": uncertainties[1],
        "frequency_linear_fit": linear,
        "rejection_reasons": reasons,
        "curves": [
            {
                "keithley_current_ma": curve["keithley_current_ma"],
                "predicted_center_hz": curve["predicted_center_hz"],
                "summary_file": curve["summary_file"],
                "discrete_peak_frequency_hz": curve[
                    "discrete_peak_frequency_hz"
                ],
                "fit": curve["fit"],
            }
            for curve in curves
        ],
        "files": [
            response_plot,
            calibration_plot,
            residual_plot,
            "calibration_results.npz",
        ],
    }
    serializable = _builtin(payload)
    with (results_dir / "analysis.yaml").open("w", encoding="utf-8") as stream:
        yaml.safe_dump(serializable, stream, allow_unicode=True, sort_keys=False)
    np.savez(
        results_dir / "calibration_results.npz",
        keithley_current_ma=current_values,
        predicted_center_hz=np.asarray(
            [curve["predicted_center_hz"] for curve in curves], dtype=float
        ),
        fitted_center_hz=centers,
        center_uncertainty_hz=center_uncertainties,
        hwhm_hz=np.asarray(
            [curve["fit"]["parameters"][1] for curve in curves], dtype=float
        ),
        curve_fit_r_squared=np.asarray(
            [curve["fit"]["r_squared"] for curve in curves], dtype=float
        ),
        curve_accepted=accepted,
        discrete_peak_frequency_hz=np.asarray(
            [curve["discrete_peak_frequency_hz"] for curve in curves], dtype=float
        ),
        K_f_Hz_per_mA=np.float64(linear["slope_hz_per_ma"]),
        f_0mA_Hz=np.float64(linear["intercept_hz"]),
        frequency_linear_covariance=np.asarray(linear["covariance"], dtype=float),
        frequency_linear_uncertainties=uncertainties,
        linear_r_squared=np.float64(linear["r_squared"]),
        linear_residual_hz=np.asarray(linear["residual_hz"], dtype=float),
        calibration_success=np.uint8(success),
    )
    print(
        f"Mx Keithley 6221 主场标定分析完成: success={success}, "
        f"K_f={linear['slope_hz_per_ma']:.6g} Hz/mA, "
        f"f0={linear['intercept_hz']:.6g} Hz"
    )
    if reasons:
        print("标定质量警告: " + "；".join(reasons))
    return serializable


def main() -> int:
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
