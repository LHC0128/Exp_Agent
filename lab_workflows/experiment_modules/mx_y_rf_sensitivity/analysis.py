"""Mx Y 向 RF 场灵敏度离线分析。"""

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
    COLOR_GREEN,
    COLOR_OPTIMAL,
    COLOR_ORANGE,
    COLOR_PURPLE,
    COLOR_TRAD,
    PAPER_STANDARD,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)
from .analysis_core import (
    absolute_dispersive_response,
    lorentzian_response,
)
from .models import MxYRFParams
from .point_analysis import evaluate_mx_y_rf_point

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
    params_type: type[MxYRFParams] = MxYRFParams,
) -> tuple[MxYRFParams, dict[str, Any]]:
    config_path = run_dir / "experiment_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"未找到实验配置: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    params = params_type.from_external(
        config.get("parameters", {}),
        schema_version=int(config.get("schema_version", 1)),
    )
    return params, config


def _plot_full_analysis(
    *,
    results_dir: Path,
    params: MxYRFParams,
    amplitude_vpp: np.ndarray,
    r_mean_v: np.ndarray,
    fit_mask: np.ndarray,
    bad_point_mask: np.ndarray,
    response_fit: Any,
    primary_slope: float,
    frequency_hz: np.ndarray,
    sensitivity: dict[str, Any],
    hwhm_hz: float | None,
    filename: str = "full_analysis.png",
    title: str | None = None,
    slope_method_label: str | None = None,
    local_slope_fit: dict[str, Any] | None = None,
) -> str | None:
    """按静磁场完整分析图版式绘制磁场灵敏度。"""
    if "corrected_ft_per_sqrt_hz" not in sensitivity:
        return None

    set_plot_style("paper")
    dense_amplitude = np.linspace(
        float(amplitude_vpp.min()),
        float(amplitude_vpp.max()),
        1000,
    )
    calibration = params.y_rf_nt_per_vpp
    field_nt = amplitude_vpp * calibration
    dense_field_nt = dense_amplitude * calibration
    slope_v_per_ft = primary_slope / (calibration * 1e6)

    fig, (ax1, ax2) = new_figure(
        (PAPER_STANDARD[0], PAPER_STANDARD[1] * 2.25),
        2,
        1,
        height_ratios=[1.0, 1.0],
    )
    ax1.plot(
        field_nt[fit_mask],
        r_mean_v[fit_mask],
        "o",
        color=COLOR_OPTIMAL,
        label="Data",
    )
    if np.any(bad_point_mask):
        ax1.plot(
            field_nt[bad_point_mask],
            r_mean_v[bad_point_mask],
            "x",
            color=COLOR_TRAD,
            label="Excluded",
        )
    ax1.plot(
        dense_field_nt,
        absolute_dispersive_response(
            dense_amplitude,
            *response_fit.parameters,
        ),
        "--",
        color=COLOR_TRAD,
        label=f"Fit ($R^2$={response_fit.r_squared:.3f})",
    )
    if local_slope_fit is not None and local_slope_fit["success"]:
        local_mask = np.asarray(local_slope_fit["mask"], dtype=bool)
        fit_amplitude = amplitude_vpp[fit_mask]
        local_amplitude = fit_amplitude[local_mask]
        local_order = np.argsort(local_amplitude)
        local_amplitude = local_amplitude[local_order]
        local_response = (
            local_slope_fit["slope"]
            * np.abs(local_amplitude - response_fit.center)
            + local_slope_fit["intercept"]
        )
        ax1.plot(
            local_amplitude * calibration,
            local_response,
            "-.",
            color=COLOR_PURPLE,
            marker="o",
            markerfacecolor="none",
            label=(
                "Local data "
                f"($R^2$={local_slope_fit['r_squared']:.3f})"
            ),
        )
    ax1.axvline(
        response_fit.center * calibration,
        color=COLOR_GRAY,
        ls=":",
        alpha=0.5,
    )
    format_axis(
        ax1,
        xlabel="Signed Y RF field amplitude (nT)",
        ylabel="Demod R (V)",
    )
    ax1.text(
        0.03,
        0.08,
        (
            f"{slope_method_label}: {slope_v_per_ft:.2e} V/fT"
            if slope_method_label
            else f"Slope = {slope_v_per_ft:.2e} V/fT"
        ),
        transform=ax1.transAxes,
    )
    if title:
        ax1.set_title(title)
    style_legend(ax1, loc="upper right", fontsize=7.5)

    positive = frequency_hz > 0
    corrected_ft = sensitivity["corrected_ft_per_sqrt_hz"]
    raw_ft = sensitivity["raw_ft_per_sqrt_hz"]
    flat_mask = np.asarray(sensitivity["flat_mask"], dtype=bool) & positive
    ax2.plot(
        frequency_hz[positive],
        corrected_ft[positive],
        color=COLOR_OPTIMAL,
        label="Corrected",
    )
    if np.any(flat_mask):
        flat_min_hz = float(frequency_hz[flat_mask].min())
        flat_max_hz = float(frequency_hz[flat_mask].max())
        flat_median_ft = float(
            sensitivity["flat_median_ft_per_sqrt_hz"]
        )
        ax2.plot(
            frequency_hz[flat_mask],
            corrected_ft[flat_mask],
            color=COLOR_GREEN,
            label=f"Flat: {flat_min_hz:.0f}-{flat_max_hz:.0f} Hz",
        )
        ax2.axvline(flat_max_hz, color=COLOR_GRAY, ls=":", alpha=0.5)
        if np.isfinite(flat_median_ft) and flat_median_ft > 0:
            ax2.axhline(
                flat_median_ft,
                color=COLOR_PURPLE,
                ls="--",
                label=(
                    f"Sensitivity (flat median): {flat_median_ft:.1f} "
                    "fT/√Hz"
                ),
            )
    ax2.plot(
        frequency_hz[positive],
        raw_ft[positive],
        color=COLOR_GRAY,
        lw=0.7,
        alpha=0.45,
        label="Raw",
    )
    if hwhm_hz is not None:
        ax2.axvline(
            hwhm_hz,
            color=COLOR_ORANGE,
            ls=":",
            alpha=0.5,
            label=f"HWHM: {hwhm_hz:.0f} Hz",
        )
    format_axis(
        ax2,
        xlabel="Frequency (Hz)",
        ylabel="Sensitivity (fT/√Hz)",
    )
    ax2.set_xlim(0.5, max((hwhm_hz or 0.0) * 2, 1000.0))
    ax2.set_yscale("log")
    ax2.grid(True, which="both")
    style_legend(
        ax2,
        loc="upper right",
        fontsize=6.5,
        ncol=2,
        columnspacing=0.8,
        handlelength=2.2,
        labelspacing=0.25,
    )

    save_figure(fig, results_dir / filename)
    return filename


def _plot_measured_amplitude_response(
    *,
    results_dir: Path,
    amplitude_vpp: np.ndarray,
    r_mean_v: np.ndarray,
    r_std_v: np.ndarray,
    bad_point_mask: np.ndarray,
    filename: str = "amplitude_response.png",
) -> str:
    """拟合失败时仅绘制幅度扫描的实际测量数据。"""
    amplitude = np.asarray(amplitude_vpp, dtype=float).reshape(-1)
    response = np.asarray(r_mean_v, dtype=float).reshape(-1)
    response_std = np.asarray(r_std_v, dtype=float).reshape(-1)
    excluded = np.asarray(bad_point_mask, dtype=bool).reshape(-1)
    if not (
        amplitude.shape
        == response.shape
        == response_std.shape
        == excluded.shape
    ):
        raise ValueError("幅度扫描绘图数组长度不一致")

    finite = np.isfinite(amplitude) & np.isfinite(response)
    set_plot_style("paper")
    fig, ax = new_figure()
    if np.any(finite):
        order = np.argsort(amplitude[finite])
        x_values = amplitude[finite][order]
        y_values = response[finite][order]
        yerr_values = response_std[finite][order]
        use_errorbars = np.all(
            np.isfinite(yerr_values) & (yerr_values >= 0.0)
        )
        if use_errorbars:
            ax.errorbar(
                x_values,
                y_values,
                yerr=yerr_values,
                fmt="o-",
                color=COLOR_OPTIMAL,
                markersize=3.5,
                linewidth=0.9,
                capsize=2.0,
                label="Measured R",
            )
        else:
            ax.plot(
                x_values,
                y_values,
                "o-",
                color=COLOR_OPTIMAL,
                markersize=3.5,
                linewidth=0.9,
                label="Measured R",
            )

        excluded_finite = finite & excluded
        if np.any(excluded_finite):
            excluded_order = np.argsort(amplitude[excluded_finite])
            ax.plot(
                amplitude[excluded_finite][excluded_order],
                response[excluded_finite][excluded_order],
                "x",
                color=COLOR_TRAD,
                markersize=6,
                mew=1.2,
                label="Excluded from fit",
            )
    else:
        ax.text(
            0.5,
            0.5,
            "No finite measured points",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
    format_axis(
        ax,
        xlabel="Signed Y RF amplitude (Vpp)",
        ylabel="Demod R (V)",
    )
    ax.set_title("Measured dispersive response (fit failed)")
    if np.any(finite):
        style_legend(ax)
    save_figure(fig, results_dir / filename)
    return filename


def analyze(
    run_dir: Path,
    *,
    params_type: type[MxYRFParams] = MxYRFParams,
    ignore_relative_gamma_uncertainty: bool = False,
    initial_center: float | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    params, config = _load_params(run_dir, params_type)
    return analyze_core(
        run_dir,
        params,
        config,
        ignore_relative_gamma_uncertainty=ignore_relative_gamma_uncertainty,
        initial_center=initial_center,
    )


def analyze_core(
    run_dir: Path,
    params: MxYRFParams,
    config: dict[str, Any],
    *,
    ignore_relative_gamma_uncertainty: bool,
    initial_center: float | None,
) -> dict[str, Any]:
    """执行 RF 灵敏度分析核心；参数和配置由调用方读取并校验。"""
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    evaluation = evaluate_mx_y_rf_point(
        raw_dir,
        params,
        ignore_relative_gamma_uncertainty=ignore_relative_gamma_uncertainty,
        initial_center=initial_center,
    )
    amplitude_vpp = evaluation["amplitude_vpp"]
    r_mean_v = evaluation["r_mean_v"]
    r_std_v = evaluation["r_std_v"]
    bad_point_mask = evaluation["bad_point_mask"]
    fit_mask = evaluation["fit_mask"]
    response_fit = evaluation["response_fit"]
    response_result = evaluation["response_result"]
    if not response_fit.success:
        measured_plot = _plot_measured_amplitude_response(
            results_dir=results_dir,
            amplitude_vpp=amplitude_vpp,
            r_mean_v=r_mean_v,
            r_std_v=r_std_v,
            bad_point_mask=bad_point_mask,
        )
        payload = {
            "success": False,
            "response_fit": response_result,
            "plot_profile": "paper",
            "warnings": [
                "幅度色散拟合失败；已输出实际测量数据图，未绘制拟合曲线。"
            ],
            "files": [measured_plot],
        }
        (results_dir / "analysis.yaml").write_text(
            yaml.safe_dump(_builtin(payload), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        raise RuntimeError(
            "幅度色散拟合质量不合格: " + "；".join(response_fit.rejection_reasons)
        )
    if evaluation["invalid_reasons"] and params.linewidth_mode == "frequency_sweep":
        raise RuntimeError("；".join(evaluation["invalid_reasons"]))
    primary_slope = evaluation["primary_slope"]
    linear = evaluation["linear"]
    slope_difference = evaluation["slope_difference"]
    warnings = evaluation["warnings"]
    frequency_scan_hz = evaluation["frequency_scan_hz"]
    frequency_response_v = evaluation["frequency_response_v"]
    linewidth_fit = evaluation["linewidth_fit"]
    hwhm_hz = evaluation["hwhm_hz"]
    linewidth_result = evaluation["linewidth_result"]
    rates = evaluation["noise_rates"]
    frequency_hz = evaluation["frequency_hz"]
    psd_r = evaluation["psd_r"]
    sensitivity = evaluation["sensitivity"]
    zero_point_linear = evaluation["zero_point_linear"]
    zero_point_slope = evaluation["zero_point_slope"]
    zero_point_sensitivity = evaluation["zero_point_sensitivity"]

    np.savez(
        results_dir / "response_fit.npz",
        signed_amplitude_vpp=amplitude_vpp,
        r_mean_v=r_mean_v,
        r_std_v=r_std_v,
        bad_point_mask=bad_point_mask,
        fitted_r_v=absolute_dispersive_response(
            amplitude_vpp,
            *response_fit.parameters,
        ),
        fit_parameters=np.asarray(response_fit.parameters),
        fit_uncertainties=np.asarray(response_fit.uncertainties),
        primary_slope_v_per_vpp=np.float64(primary_slope),
        zero_point_slope_v_per_vpp=np.float64(zero_point_slope),
        zero_point_fit_mask=np.asarray(
            zero_point_linear["mask"],
            dtype=bool,
        ),
    )
    np.savez(
        results_dir / "noise_psd.npz",
        frequency_hz=frequency_hz,
        psd_r_v2_per_hz=psd_r,
        actual_rates_sa_s=np.asarray(rates),
    )
    np.savez(
        results_dir / "sensitivity.npz",
        frequency_hz=frequency_hz,
        **sensitivity,
    )
    if zero_point_sensitivity is not None:
        np.savez(
            results_dir / "sensitivity_zero_point.npz",
            frequency_hz=frequency_hz,
            **zero_point_sensitivity,
        )

    dense_amplitude = np.linspace(
        float(amplitude_vpp.min()),
        float(amplitude_vpp.max()),
        1000,
    )
    set_plot_style("paper")
    fig, ax = new_figure()
    ax.plot(
        amplitude_vpp[fit_mask],
        r_mean_v[fit_mask],
        "o",
        color=COLOR_OPTIMAL,
        label="Data",
    )
    if np.any(bad_point_mask):
        ax.plot(
            amplitude_vpp[bad_point_mask],
            r_mean_v[bad_point_mask],
            "x",
            color=COLOR_TRAD,
            label="Excluded",
        )
    ax.plot(
        dense_amplitude,
        absolute_dispersive_response(
            dense_amplitude,
            *response_fit.parameters,
        ),
        "--",
        color=COLOR_TRAD,
        label="Absolute dispersive fit",
    )
    if linear["success"]:
        mask = np.asarray(linear["mask"], dtype=bool)
        fit_x = amplitude_vpp[fit_mask]
        x_local = fit_x[mask]
        ax.plot(
            x_local,
            linear["slope"]
            * np.abs(x_local - response_fit.center)
            + linear["intercept"],
            ":",
            color=COLOR_GREEN,
            label="Central |V-V0| fit",
        )
    if zero_point_linear["success"]:
        zero_mask = np.asarray(zero_point_linear["mask"], dtype=bool)
        fit_x = amplitude_vpp[fit_mask]
        x_local = fit_x[zero_mask]
        ax.plot(
            x_local,
            zero_point_linear["slope"]
            * np.abs(x_local - response_fit.center)
            + zero_point_linear["intercept"],
            "-.",
            color=COLOR_PURPLE,
            label="Adaptive zero-point data slope",
        )
    format_axis(
        ax,
        xlabel="Signed Y RF amplitude (Vpp)",
        ylabel="Demod R (V)",
    )
    style_legend(ax)
    save_figure(fig, results_dir / "amplitude_response.png")

    if (
        frequency_scan_hz is not None
        and frequency_response_v is not None
        and linewidth_fit is not None
    ):
        dense_frequency = np.linspace(
            float(frequency_scan_hz.min()),
            float(frequency_scan_hz.max()),
            1000,
        )
        fig, ax = new_figure()
        ax.plot(
            frequency_scan_hz,
            frequency_response_v,
            "o",
            color=COLOR_OPTIMAL,
            label="Measured R",
        )
        ax.plot(
            dense_frequency,
            lorentzian_response(
                dense_frequency,
                *linewidth_fit.parameters,
            ),
            "--",
            color=COLOR_TRAD,
            label="R-Lorentzian fit",
        )
        format_axis(
            ax,
            xlabel="Y RF frequency (Hz)",
            ylabel="Demod R (V)",
        )
        style_legend(ax)
        save_figure(fig, results_dir / "frequency_linewidth.png")

    positive = frequency_hz > 0
    fig, ax = new_figure()
    ax.loglog(
        frequency_hz[positive],
        np.sqrt(psd_r[positive]),
        color=COLOR_OPTIMAL,
        label="R ASD",
    )
    format_axis(
        ax,
        xlabel="Frequency (Hz)",
        ylabel="ASD (V/√Hz)",
    )
    ax.grid(which="both")
    style_legend(ax)
    save_figure(fig, results_dir / "noise_psd.png")

    full_analysis_file = _plot_full_analysis(
        results_dir=results_dir,
        params=params,
        amplitude_vpp=amplitude_vpp,
        r_mean_v=r_mean_v,
        fit_mask=fit_mask,
        bad_point_mask=bad_point_mask,
        response_fit=response_fit,
        primary_slope=primary_slope,
        frequency_hz=frequency_hz,
        sensitivity=sensitivity,
        hwhm_hz=hwhm_hz,
    )
    zero_point_full_analysis_file = None
    if zero_point_sensitivity is not None:
        zero_point_full_analysis_file = _plot_full_analysis(
            results_dir=results_dir,
            params=params,
            amplitude_vpp=amplitude_vpp,
            r_mean_v=r_mean_v,
            fit_mask=fit_mask,
            bad_point_mask=bad_point_mask,
            response_fit=response_fit,
            primary_slope=zero_point_slope,
            frequency_hz=frequency_hz,
            sensitivity=zero_point_sensitivity,
            hwhm_hz=hwhm_hz,
            filename="full_analysis_zero_point.png",
            title="Zero-point measured-data slope method",
            slope_method_label="Zero-point slope",
            local_slope_fit=zero_point_linear,
        )

    result = {
        "success": True,
        "experiment_id": config.get("experiment_id", "mx-y-rf-sensitivity"),
        "run_dir": str(run_dir),
        "linewidth_mode": params.linewidth_mode,
        "response_fit": response_result,
        "response_signal": "R",
        "noise_signal": "R",
        "sensitivity_noise_source": "PSD_R",
        "bad_point_mask": bad_point_mask.tolist(),
        "primary_slope_v_per_vpp": primary_slope,
        "central_absolute_linear_fit": {
            key: value for key, value in linear.items() if key != "mask"
        },
        "relative_slope_difference": slope_difference,
        "zero_point_method": {
            "name": "adaptive_zero_point_absolute_linear",
            "description": (
                "Global dispersive fit locates V0; the slope is regressed "
                "from at least five nearest measured R-versus-|V-V0| points "
                "with at least two points on each side."
            ),
            "valid": evaluation["zero_point_valid"],
            "invalid_reasons": evaluation["zero_point_invalid_reasons"],
            "slope_v_per_vpp": zero_point_slope,
            "linear_fit": {
                key: value
                for key, value in zero_point_linear.items()
                if key != "mask"
            },
            "flat_median_vpp_per_sqrt_hz": (
                zero_point_sensitivity[
                    "flat_median_vpp_per_sqrt_hz"
                ]
                if zero_point_sensitivity is not None
                else None
            ),
            "flat_median_ft_per_sqrt_hz": (
                zero_point_sensitivity.get(
                    "flat_median_ft_per_sqrt_hz"
                )
                if zero_point_sensitivity is not None
                else None
            ),
            "flat_detection": (
                {
                    "success": zero_point_sensitivity[
                        "flat_detection_success"
                    ],
                    "reason": zero_point_sensitivity[
                        "flat_detection_reason"
                    ],
                    "flat_band_hz": (
                        zero_point_sensitivity["flat_band_hz"].tolist()
                        if bool(
                            zero_point_sensitivity[
                                "flat_detection_success"
                            ]
                        )
                        else None
                    ),
                }
                if zero_point_sensitivity is not None
                else None
            ),
        },
        "linewidth": linewidth_result,
        "actual_noise_rates_sa_s": rates,
        "flat_band_hz": (
            sensitivity["flat_band_hz"].tolist()
            if bool(sensitivity["flat_detection_success"])
            else None
        ),
        "flat_detection": {
            "success": sensitivity["flat_detection_success"],
            "reason": sensitivity["flat_detection_reason"],
            "candidate_count": sensitivity[
                "flat_detection_candidate_count"
            ],
            "median_cv": sensitivity["flat_detection_median_cv"],
            "median_p10_p90_span": sensitivity[
                "flat_detection_median_p10_p90_span"
            ],
            "drift": sensitivity["flat_detection_drift"],
            "relative_mad": sensitivity[
                "flat_detection_relative_mad"
            ],
            "rise_sigma": sensitivity["flat_detection_rise_sigma"],
            "low_boundary_p10_p90_hz": sensitivity[
                "flat_detection_low_boundary_p10_p90_hz"
            ],
            "high_boundary_p10_p90_hz": sensitivity[
                "flat_detection_high_boundary_p10_p90_hz"
            ],
        },
        "flat_median_vpp_per_sqrt_hz": sensitivity[
            "flat_median_vpp_per_sqrt_hz"
        ],
        "flat_median_ft_per_sqrt_hz": sensitivity.get(
            "flat_median_ft_per_sqrt_hz"
        ),
        "plot_profile": "paper",
        "warnings": warnings,
        "files": [
            "response_fit.npz",
            "noise_psd.npz",
            "sensitivity.npz",
            *(
                ["sensitivity_zero_point.npz"]
                if zero_point_sensitivity is not None
                else []
            ),
            "amplitude_response.png",
            "noise_psd.png",
            *([full_analysis_file] if full_analysis_file else []),
            *(
                [zero_point_full_analysis_file]
                if zero_point_full_analysis_file
                else []
            ),
        ],
    }
    (results_dir / "analysis.yaml").write_text(
        yaml.safe_dump(_builtin(result), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (results_dir / "analysis.json").write_text(
        json.dumps(_builtin(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> int:
    run_dir = runtime_run_dir()
    if run_dir is None:
        raise RuntimeError("未设置分析运行目录")
    result = analyze(run_dir)
    print(f"Mx Y RF 灵敏度分析完成: {result['run_dir']}")
    for warning in result["warnings"]:
        print(f"[WARN] {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
