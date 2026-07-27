"""Mx 主磁场频率标定离线分析。"""

from __future__ import annotations

import json
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
from ..mx_y_rf_sensitivity.analysis_core import (
    fit_lorentzian_response,
    lorentzian_response,
)
from .models import MxMainFieldCalibrationParams

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))


def _builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_builtin(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _load_params(
    run_dir: Path,
) -> tuple[MxMainFieldCalibrationParams, dict[str, Any]]:
    config_path = run_dir / "experiment_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"未找到实验配置: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    params = MxMainFieldCalibrationParams.from_external(
        config.get("parameters", {}),
        schema_version=int(config.get("schema_version", 1)),
    )
    return params, config


def _load_scan_index(raw_dir: Path) -> list[dict[str, Any]]:
    path = raw_dir / "main_field_scan_index.npz"
    if not path.exists():
        entries: list[dict[str, Any]] = []
        for summary_path in sorted(raw_dir.glob("current_*/frequency_scan.npz")):
            with np.load(summary_path) as data:
                entries.append(
                    {
                        "main_field_current_ma": float(data["main_field_current_ma"]),
                        "predicted_center_hz": float(data["predicted_center_hz"]),
                        "summary_file": str(summary_path.relative_to(raw_dir.parent)),
                    }
                )
        if entries:
            return entries
        raise FileNotFoundError(f"缺少主场扫描索引和已完成频扫汇总: {path}")
    with np.load(path) as data:
        current = np.asarray(data["main_field_current_ma"], dtype=float)
        predicted = np.asarray(data["predicted_center_hz"], dtype=float)
        files = np.asarray(data["summary_file"], dtype=str)
    if not (current.size == predicted.size == files.size):
        raise ValueError("主场扫描索引字段长度不一致")
    return [
        {
            "main_field_current_ma": float(current_ma),
            "predicted_center_hz": float(center),
            "summary_file": str(filename),
        }
        for current_ma, center, filename in zip(
            current, predicted, files, strict=True
        )
    ]


def _discrete_peak_frequency(
    frequency_hz: np.ndarray, response_v: np.ndarray
) -> float:
    valid = np.isfinite(frequency_hz) & np.isfinite(response_v)
    if not np.any(valid):
        return float("nan")
    frequency_hz = frequency_hz[valid]
    response_v = response_v[valid]
    edge_count = max(1, frequency_hz.size // 10)
    baseline = float(
        np.median(np.r_[response_v[:edge_count], response_v[-edge_count:]])
    )
    return float(frequency_hz[int(np.argmax(np.abs(response_v - baseline)))])


def _analyze_curve(
    params: MxMainFieldCalibrationParams,
    run_dir: Path,
    entry: dict[str, Any],
) -> dict[str, Any]:
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
    center_uncertainty = float(fit.uncertainties[2])
    if fit.gamma > 0 and np.isfinite(fit.center) and np.any(valid):
        finite_frequency = frequency_hz[valid]
        edge_distance = min(
            fit.center - float(np.min(finite_frequency)),
            float(np.max(finite_frequency)) - fit.center,
        )
        if edge_distance < fit.gamma:
            reasons.append("拟合中心距扫描边缘不足一个 HWHM")
    accepted = fit.success and not reasons
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
            "success": bool(accepted),
            "rejection_reasons": reasons,
            "center_uncertainty_hz": center_uncertainty,
            "model": "C + A*gamma^2/((f-f0)^2+gamma^2)",
        },
    }


def _failed_linear_fit(point_count: int, reason: str) -> dict[str, Any]:
    return {
        "success": False,
        "slope_hz_per_ma": np.nan,
        "intercept_hz": np.nan,
        "covariance": np.full((2, 2), np.nan),
        "uncertainties": np.full(2, np.nan),
        "r_squared": np.nan,
        "residual_hz": np.full(point_count, np.nan),
        "rejection_reasons": [reason],
    }


def _weighted_linear_fit(
    current_ma: np.ndarray, center_hz: np.ndarray, sigma_hz: np.ndarray
) -> dict[str, Any]:
    current_ma = np.asarray(current_ma, dtype=float)
    center_hz = np.asarray(center_hz, dtype=float)
    sigma_hz = np.asarray(sigma_hz, dtype=float)
    valid = np.isfinite(current_ma) & np.isfinite(center_hz)
    current_ma, center_hz, sigma_hz = (
        current_ma[valid],
        center_hz[valid],
        sigma_hz[valid],
    )
    if current_ma.size < 2:
        return _failed_linear_fit(current_ma.size, "有效中心不足 2 个")
    finite_positive_sigma = np.isfinite(sigma_hz) & (sigma_hz > 0)
    fallback = (
        float(np.median(sigma_hz[finite_positive_sigma]))
        if np.any(finite_positive_sigma)
        else 1.0
    )
    sigma_hz = np.where(finite_positive_sigma, sigma_hz, fallback)
    design = np.column_stack([current_ma, np.ones_like(current_ma)])
    weights = 1.0 / sigma_hz**2
    normal = design.T @ (weights[:, None] * design)
    try:
        normal_inverse = np.linalg.inv(normal)
    except np.linalg.LinAlgError:
        return _failed_linear_fit(current_ma.size, "线性拟合法方程奇异")
    beta = normal_inverse @ (design.T @ (weights * center_hz))
    predicted = design @ beta
    residual = center_hz - predicted
    dof = max(1, current_ma.size - 2)
    reduced_chi_square = float(np.sum((residual / sigma_hz) ** 2) / dof)
    covariance = normal_inverse * reduced_chi_square
    uncertainties = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((center_hz - np.mean(center_hz)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-30 else 0.0
    return {
        "success": True,
        "slope_hz_per_ma": float(beta[0]),
        "intercept_hz": float(beta[1]),
        "covariance": covariance,
        "uncertainties": uncertainties,
        "r_squared": float(r_squared),
        "residual_hz": residual,
        "reduced_chi_square": reduced_chi_square,
        "rejection_reasons": [],
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
            dense = np.linspace(float(np.nanmin(frequency)), float(np.nanmax(frequency)), 800)
            axis.plot(
                dense / 1e3,
                lorentzian_response(dense, *fit["parameters"]),
                color=COLOR_TRAD,
                linestyle="--",
                label="Lorentzian fit",
            )
        status = "accepted" if fit["success"] else "excluded"
        axis.set_title(
            f"Main-field current = {curve['main_field_current_ma']:.3f} mA ({status})"
        )
        format_axis(
            axis,
            xlabel="Y RF frequency (kHz)",
            ylabel="Demod R (V)",
        )
        style_legend(axis)
    for axis in axes.flat[len(curves) :]:
        axis.set_visible(False)
    filename = "frequency_response_fits.png"
    save_figure(fig, results_dir / filename)
    return filename


def _calibration_fit_label(
    linear: dict[str, Any], gyromagnetic_ratio_hz_per_nt: float
) -> str:
    """生成同时包含频率与磁场截距的英文拟合图例。"""
    slope_hz_per_ma = float(linear["slope_hz_per_ma"])
    intercept_hz = float(linear["intercept_hz"])
    return (
        f"Linear fit: Kf={slope_hz_per_ma:.3f} Hz/mA, "
        f"f0={intercept_hz:.3f} Hz\n"
        f"KB={slope_hz_per_ma / gyromagnetic_ratio_hz_per_nt:.3f} nT/mA, "
        f"B0={intercept_hz / gyromagnetic_ratio_hz_per_nt:.3f} nT, "
        f"R²={linear['r_squared']:.5f}"
    )


def _plot_calibration(
    results_dir: Path,
    curves: list[dict[str, Any]],
    linear: dict[str, Any],
    gyromagnetic_ratio_hz_per_nt: float,
) -> tuple[str, str]:
    current_all = np.asarray(
        [curve["main_field_current_ma"] for curve in curves], dtype=float
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
            label=_calibration_fit_label(linear, gyromagnetic_ratio_hz_per_nt),
        )
    format_axis(
        axis,
        xlabel="GS200 current (mA)",
        ylabel="Resonance center (kHz)",
    )
    style_legend(axis)
    secondary = axis.secondary_yaxis(
        "right",
        functions=(
            lambda frequency_khz: frequency_khz / gyromagnetic_ratio_hz_per_nt,
            lambda magnetic_field_ut: magnetic_field_ut * gyromagnetic_ratio_hz_per_nt,
        ),
    )
    secondary.set_ylabel("Magnetic field (µT)")
    calibration_name = "main_field_frequency_calibration.png"
    save_figure(fig, results_dir / calibration_name)

    fig, axis = new_figure()
    if linear["success"] and np.any(accepted):
        residual = np.asarray(linear["residual_hz"], dtype=float)
        axis.axhline(0.0, color=COLOR_GRAY, linestyle="--")
        axis.plot(
            current_all[accepted],
            residual,
            "o-",
            color=COLOR_OPTIMAL,
        )
    format_axis(
        axis,
        xlabel="GS200 current (mA)",
        ylabel="Linear-fit residual (Hz)",
    )
    residual_name = "main_field_frequency_residuals.png"
    save_figure(fig, results_dir / residual_name)
    return calibration_name, residual_name


def analyze(run_dir: Path) -> dict[str, Any]:
    set_plot_style("paper")
    run_dir = Path(run_dir).resolve()
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    params, config = _load_params(run_dir)
    entries = _load_scan_index(raw_dir)
    curves = [_analyze_curve(params, run_dir, entry) for entry in entries]

    accepted = np.asarray([curve["fit"]["success"] for curve in curves], dtype=bool)
    current_values = np.asarray(
        [curve["main_field_current_ma"] for curve in curves], dtype=float
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
    calibration_reasons = list(linear["rejection_reasons"])
    if (
        not np.isfinite(linear["r_squared"])
        or linear["r_squared"] < params.linear_r_squared_min
    ):
        calibration_reasons.append(
            f"线性 R^2={linear['r_squared']:.6g} < {params.linear_r_squared_min:.6g}"
        )
    calibration_success = linear["success"] and not calibration_reasons

    gamma = params.gyromagnetic_ratio_hz_per_nt
    slope_b = linear["slope_hz_per_ma"] / gamma
    intercept_b = linear["intercept_hz"] / gamma
    field_covariance = np.asarray(linear["covariance"], dtype=float) / gamma**2
    field_uncertainties = np.asarray(linear["uncertainties"], dtype=float) / gamma

    response_plot = _plot_frequency_responses(results_dir, curves)
    calibration_plot, residual_plot = _plot_calibration(
        results_dir, curves, linear, gamma
    )

    curve_payload = [
        {
            "main_field_current_ma": curve["main_field_current_ma"],
            "predicted_center_hz": curve["predicted_center_hz"],
            "summary_file": curve["summary_file"],
            "discrete_peak_frequency_hz": curve["discrete_peak_frequency_hz"],
            "fit": curve["fit"],
        }
        for curve in curves
    ]
    payload = {
        "success": bool(calibration_success),
        "experiment_id": config.get("experiment_id", "mx-main-field-calibration"),
        "run_dir": str(run_dir),
        "frequency_model": "f_Hz = K_f_Hz_per_mA * current_mA + f_0mA_Hz",
        "magnetic_field_model": "B_nT = K_B_nT_per_mA * current_mA + B_0mA_nT",
        "gyromagnetic_ratio_hz_per_nt": gamma,
        "K_f_Hz_per_mA": linear["slope_hz_per_ma"],
        "f_0mA_Hz": linear["intercept_hz"],
        "K_B_nT_per_mA": slope_b,
        "B_0mA_nT": intercept_b,
        "frequency_linear_fit": linear,
        "magnetic_field_covariance": field_covariance,
        "magnetic_field_uncertainties": field_uncertainties,
        "rejection_reasons": calibration_reasons,
        "curves": curve_payload,
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
    with (results_dir / "analysis.json").open("w", encoding="utf-8") as stream:
        json.dump(serializable, stream, ensure_ascii=False, indent=2)
    np.savez(
        results_dir / "calibration_results.npz",
        main_field_current_ma=current_values,
        predicted_center_hz=np.asarray(
            [curve["predicted_center_hz"] for curve in curves], dtype=float
        ),
        fitted_center_hz=centers,
        fitted_field_nt=centers / gamma,
        center_uncertainty_hz=center_uncertainties,
        field_uncertainty_nt=center_uncertainties / gamma,
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
        gyromagnetic_ratio_hz_per_nt=np.float64(gamma),
        K_f_Hz_per_mA=np.float64(linear["slope_hz_per_ma"]),
        f_0mA_Hz=np.float64(linear["intercept_hz"]),
        K_B_nT_per_mA=np.float64(slope_b),
        B_0mA_nT=np.float64(intercept_b),
        frequency_linear_covariance=np.asarray(linear["covariance"], dtype=float),
        frequency_linear_uncertainties=np.asarray(linear["uncertainties"], dtype=float),
        magnetic_field_linear_covariance=field_covariance,
        magnetic_field_linear_uncertainties=field_uncertainties,
        linear_r_squared=np.float64(linear["r_squared"]),
        linear_residual_hz=np.asarray(linear["residual_hz"], dtype=float),
        calibration_success=np.uint8(calibration_success),
    )
    print(
        f"Mx 主场标定分析完成: success={calibration_success}, "
        f"K_f={linear['slope_hz_per_ma']:.6g} Hz/mA, "
        f"K_B={slope_b:.6g} nT/mA"
    )
    if calibration_reasons:
        print("标定质量警告: " + "；".join(calibration_reasons))
    return serializable


def main() -> int:
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
