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
    amplitude_gamma_to_hz,
    average_welch_psd,
    central_absolute_linear_fit,
    fit_absolute_dispersive_response,
    fit_lorentzian_response,
    lorentzian_response,
    sensitivity_spectrum,
)
from .models import MxYRFParams

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))


SENSITIVITY_REFERENCE_FT_PER_SQRT_HZ = 150.0


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


def _load_params(run_dir: Path) -> tuple[MxYRFParams, dict[str, Any]]:
    config_path = run_dir / "experiment_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"未找到实验配置: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    params = MxYRFParams.from_external(
        config.get("parameters", {}),
        schema_version=int(config.get("schema_version", 1)),
    )
    return params, config


def _load_noise(raw_dir: Path, count: int) -> tuple[list[dict[str, np.ndarray]], list[float]]:
    records: list[dict[str, np.ndarray]] = []
    rates: list[float] = []
    for index in range(count):
        path = raw_dir / f"noise_{index:03d}.npz"
        if not path.exists():
            raise FileNotFoundError(f"缺少噪声记录: {path}")
        with np.load(path) as data:
            records.append({
                "r_v": np.asarray(data["r_v"], dtype=float)
            })
            rates.append(float(data["actual_rate_sa_s"]))
    return records, rates


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
) -> str | None:
    """按静磁场完整分析图版式绘制磁场灵敏度。"""
    if "corrected_ft_per_sqrt_hz" not in sensitivity:
        return None

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
        f"Slope = {slope_v_per_ft:.2e} V/fT",
        transform=ax1.transAxes,
    )
    style_legend(ax1, loc="best")

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
        ax2.plot(
            frequency_hz[flat_mask],
            corrected_ft[flat_mask],
            color=COLOR_GREEN,
            label=f"Flat: {flat_min_hz:.0f}-{flat_max_hz:.0f} Hz",
        )
        ax2.axvline(flat_max_hz, color=COLOR_GRAY, ls=":", alpha=0.5)
    ax2.plot(
        frequency_hz[positive],
        raw_ft[positive],
        color=COLOR_GRAY,
        lw=0.7,
        alpha=0.45,
        label="Raw",
    )
    ax2.axhline(
        SENSITIVITY_REFERENCE_FT_PER_SQRT_HZ,
        color=COLOR_PURPLE,
        ls="--",
        label=(
            f"Reference: {SENSITIVITY_REFERENCE_FT_PER_SQRT_HZ:.0f} "
            "fT/√Hz"
        ),
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

    filename = "full_analysis.png"
    save_figure(fig, results_dir / filename)
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    params, config = _load_params(run_dir)

    amplitude_path = raw_dir / "amplitude_scan.npz"
    if not amplitude_path.exists():
        raise FileNotFoundError(f"缺少幅度扫描汇总: {amplitude_path}")
    with np.load(amplitude_path) as data:
        amplitude_vpp = np.asarray(data["signed_amplitude_vpp"], dtype=float)
        r_key = (
            "r_mean_v"
            if "r_mean_v" in data.files
            else "r_scalar_mean_v"
        )
        r_mean_v = np.asarray(data[r_key], dtype=float)
        r_std_v = np.asarray(data["r_std_v"], dtype=float)

    bad_point_mask = (
        ~np.isfinite(amplitude_vpp)
        | ~np.isfinite(r_mean_v)
        | ~np.isfinite(r_std_v)
        | (r_std_v > params.r_bad_point_std_threshold_v)
    )
    fit_mask = ~bad_point_mask
    response_fit = fit_absolute_dispersive_response(
        amplitude_vpp[fit_mask],
        r_mean_v[fit_mask],
        r_squared_min=params.fit_r_squared_min,
        relative_gamma_uncertainty_max=params.fit_relative_gamma_uncertainty_max,
    )
    response_result = {
        **response_fit.to_dict(),
        "linewidth_kind": "amplitude_domain",
        "response_signal": "R",
        "model": "abs(A*(V-V0)/((V-V0)^2+gamma^2)+C)",
        "bad_point_criterion": "std(R) > threshold",
        "bad_point_std_threshold_v": params.r_bad_point_std_threshold_v,
        "excluded_point_count": int(np.count_nonzero(bad_point_mask)),
        "excluded_amplitude_vpp": amplitude_vpp[
            bad_point_mask
        ].tolist(),
    }
    if not response_fit.success:
        payload = {"success": False, "response_fit": response_result}
        (results_dir / "analysis.yaml").write_text(
            yaml.safe_dump(_builtin(payload), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        raise RuntimeError(
            "幅度色散拟合质量不合格: " + "；".join(response_fit.rejection_reasons)
        )

    primary_slope = abs(response_fit.amplitude / response_fit.gamma**2)
    linear = central_absolute_linear_fit(
        amplitude_vpp[fit_mask],
        r_mean_v[fit_mask],
        center=response_fit.center,
        gamma=response_fit.gamma,
        gamma_fraction=params.linear_check_gamma_fraction,
    )
    slope_difference = (
        abs(abs(float(linear["slope"])) - primary_slope) / primary_slope
        if linear["success"] and primary_slope > 0
        else float("nan")
    )
    warnings: list[str] = []
    if np.isfinite(slope_difference) and slope_difference > params.slope_agreement_tolerance:
        warnings.append(
            f"中心线性斜率与色散零点导数相差 {slope_difference:.1%}，"
            f"超过 {params.slope_agreement_tolerance:.1%}"
        )

    frequency_scan_hz: np.ndarray | None = None
    frequency_response_v: np.ndarray | None = None
    linewidth_fit = None
    if params.linewidth_mode == "frequency_sweep":
        frequency_path = raw_dir / "frequency_scan.npz"
        if not frequency_path.exists():
            raise FileNotFoundError(f"缺少模式1扫频汇总: {frequency_path}")
        with np.load(frequency_path) as data:
            frequency_scan_hz = np.asarray(data["frequency_hz"], dtype=float)
            frequency_response_v = np.asarray(data["r_scalar_mean_v"], dtype=float)
        linewidth_fit = fit_lorentzian_response(
            frequency_scan_hz,
            frequency_response_v,
            r_squared_min=params.fit_r_squared_min,
            relative_gamma_uncertainty_max=params.fit_relative_gamma_uncertainty_max,
        )
        if not linewidth_fit.success:
            raise RuntimeError(
                "模式1 R-Lorentzian 拟合质量不合格: "
                + "；".join(linewidth_fit.rejection_reasons)
            )
        hwhm_hz: float | None = linewidth_fit.gamma
        linewidth_result = {
            **linewidth_fit.to_dict(),
            "linewidth_kind": "frequency_sweep_true_hwhm",
            "hwhm_hz": hwhm_hz,
        }
    else:
        hwhm_hz = amplitude_gamma_to_hz(
            response_fit.gamma,
            params.y_rf_nt_per_vpp,
        )
        linewidth_result = {
            "success": hwhm_hz is not None,
            "linewidth_kind": "amplitude_equivalent",
            "gamma_vpp": response_fit.gamma,
            "hwhm_hz": hwhm_hz,
            "note": (
                "Rabi/amplitude-equivalent HWHM; "
                "not a swept-frequency resonance linewidth"
            ),
        }
        if hwhm_hz is None:
            warnings.append(
                "Y_RF_NT_PER_VPP 未填写：不计算模式2等效 HWHM 和频段中位数"
            )

    noise, rates = _load_noise(raw_dir, params.noise_n_avg)
    frequency_hz, psd_r = average_welch_psd(
        [item["r_v"] for item in noise],
        rates,
    )

    sensitivity = sensitivity_spectrum(
        psd_r,
        frequency_hz,
        slope_signal_v_per_vpp=primary_slope,
        hwhm_hz=hwhm_hz,
        low_freq_skip_hz=params.low_freq_skip_hz,
        y_rf_nt_per_vpp=params.y_rf_nt_per_vpp,
    )
    if hwhm_hz is not None and not bool(
        sensitivity["flat_detection_success"]
    ):
        warnings.append(
            "自动平坦段识别失败，未报告灵敏度单值："
            f"{sensitivity['flat_detection_reason']}"
        )

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
        "sensitivity_reference_ft_per_sqrt_hz": (
            SENSITIVITY_REFERENCE_FT_PER_SQRT_HZ
        ),
        "warnings": warnings,
        "files": [
            "response_fit.npz",
            "noise_psd.npz",
            "sensitivity.npz",
            "amplitude_response.png",
            "noise_psd.png",
            *([full_analysis_file] if full_analysis_file else []),
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
