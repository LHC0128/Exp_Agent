"""Mx Y RF 单工作点离线评估。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .analysis_core import (
    adaptive_zero_point_absolute_linear_fit,
    amplitude_gamma_to_hz,
    average_welch_psd,
    central_absolute_linear_fit,
    fit_absolute_dispersive_response,
    fit_lorentzian_response,
    sensitivity_spectrum,
)
from .models import MxYRFParams


def _load_noise(
    raw_dir: Path,
    count: int,
) -> tuple[list[np.ndarray], list[float]]:
    waveforms: list[np.ndarray] = []
    rates: list[float] = []
    for index in range(count):
        path = raw_dir / f"noise_{index:03d}.npz"
        if not path.exists():
            raise FileNotFoundError(f"缺少噪声记录: {path}")
        with np.load(path) as data:
            waveforms.append(np.asarray(data["r_v"], dtype=float))
            rates.append(float(data["actual_rate_sa_s"]))
    return waveforms, rates


def evaluate_mx_y_rf_point(
    raw_dir: Path,
    params: MxYRFParams,
    *,
    include_rejected_fit_diagnostics: bool = False,
) -> dict[str, Any]:
    """评估一个完整的 Mx Y RF 灵敏度工作点，不写文件也不连接仪器。

    ``include_rejected_fit_diagnostics`` 只允许幅度等效线宽模式在响应拟合被
    质量门槛拒绝后继续计算诊断谱；该点仍保持无效，不参与最优值判断。
    """
    raw_dir = Path(raw_dir)
    amplitude_path = raw_dir / "amplitude_scan.npz"
    if not amplitude_path.exists():
        raise FileNotFoundError(f"缺少幅度扫描汇总: {amplitude_path}")
    with np.load(amplitude_path) as data:
        amplitude_vpp = np.asarray(data["signed_amplitude_vpp"], dtype=float)
        r_key = "r_mean_v" if "r_mean_v" in data.files else "r_scalar_mean_v"
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
        "excluded_amplitude_vpp": amplitude_vpp[bad_point_mask].tolist(),
    }
    result: dict[str, Any] = {
        "valid": False,
        "invalid_reasons": [],
        "warnings": [],
        "amplitude_vpp": amplitude_vpp,
        "r_mean_v": r_mean_v,
        "r_std_v": r_std_v,
        "bad_point_mask": bad_point_mask,
        "fit_mask": fit_mask,
        "response_fit": response_fit,
        "response_result": response_result,
    }
    response_rejection_reasons: list[str] = []
    if not response_fit.success:
        response_rejection_reasons = [
            "幅度色散拟合质量不合格: "
            + "；".join(response_fit.rejection_reasons)
        ]
        result["invalid_reasons"] = response_rejection_reasons
        if (
            not include_rejected_fit_diagnostics
            or params.linewidth_mode != "amplitude_equivalent"
        ):
            return result

    primary_slope = abs(response_fit.amplitude / response_fit.gamma**2)
    linear = central_absolute_linear_fit(
        amplitude_vpp[fit_mask],
        r_mean_v[fit_mask],
        center=response_fit.center,
        gamma=response_fit.gamma,
        gamma_fraction=params.linear_check_gamma_fraction,
    )
    zero_point_linear = adaptive_zero_point_absolute_linear_fit(
        amplitude_vpp[fit_mask],
        r_mean_v[fit_mask],
        center=response_fit.center,
    )
    zero_point_slope = (
        float(zero_point_linear["slope"])
        if zero_point_linear["success"]
        else float("nan")
    )
    slope_difference = (
        abs(abs(float(linear["slope"])) - primary_slope) / primary_slope
        if linear["success"] and primary_slope > 0
        else float("nan")
    )
    warnings: list[str] = []
    if (
        np.isfinite(slope_difference)
        and slope_difference > params.slope_agreement_tolerance
    ):
        warnings.append(
            f"中心线性斜率与色散零点导数相差 {slope_difference:.1%}，"
            f"超过 {params.slope_agreement_tolerance:.1%}"
        )
    if (
        zero_point_linear["success"]
        and float(zero_point_linear["r_squared"])
        < params.fit_r_squared_min
    ):
        warnings.append(
            "零点局部实测斜率回归的 "
            f"R^2={zero_point_linear['r_squared']:.4f}，"
            f"低于全局拟合参考门槛 {params.fit_r_squared_min:.4f}"
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
            frequency_response_v = np.asarray(
                data["r_scalar_mean_v"],
                dtype=float,
            )
        linewidth_fit = fit_lorentzian_response(
            frequency_scan_hz,
            frequency_response_v,
            r_squared_min=params.fit_r_squared_min,
            relative_gamma_uncertainty_max=params.fit_relative_gamma_uncertainty_max,
        )
        if not linewidth_fit.success:
            result["invalid_reasons"] = [
                "模式1 R-Lorentzian 拟合质量不合格: "
                + "；".join(linewidth_fit.rejection_reasons)
            ]
            result.update(
                {
                    "primary_slope": primary_slope,
                    "linear": linear,
                    "slope_difference": slope_difference,
                    "frequency_scan_hz": frequency_scan_hz,
                    "frequency_response_v": frequency_response_v,
                    "linewidth_fit": linewidth_fit,
                    "warnings": warnings,
                }
            )
            return result
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

    noise_waveforms, rates = _load_noise(raw_dir, params.noise_n_avg)
    frequency_hz, psd_r = average_welch_psd(noise_waveforms, rates)
    sensitivity = sensitivity_spectrum(
        psd_r,
        frequency_hz,
        slope_signal_v_per_vpp=primary_slope,
        hwhm_hz=hwhm_hz,
        low_freq_skip_hz=params.low_freq_skip_hz,
        y_rf_nt_per_vpp=params.y_rf_nt_per_vpp,
    )
    zero_point_sensitivity: dict[str, Any] | None = None
    if zero_point_linear["success"]:
        zero_point_sensitivity = sensitivity_spectrum(
            psd_r,
            frequency_hz,
            slope_signal_v_per_vpp=zero_point_slope,
            hwhm_hz=hwhm_hz,
            low_freq_skip_hz=params.low_freq_skip_hz,
            y_rf_nt_per_vpp=params.y_rf_nt_per_vpp,
        )
    flat_success = bool(sensitivity["flat_detection_success"])
    if hwhm_hz is not None and not flat_success:
        warnings.append(
            "自动平坦段识别失败，未报告灵敏度单值："
            f"{sensitivity['flat_detection_reason']}"
        )

    invalid_reasons = list(response_rejection_reasons)
    if hwhm_hz is None:
        invalid_reasons.append("缺少有效的幅度等效 HWHM")
    if not flat_success:
        invalid_reasons.append(
            "自动平坦段识别失败: "
            f"{sensitivity['flat_detection_reason']}"
        )
    zero_point_invalid_reasons = list(response_rejection_reasons)
    if not zero_point_linear["success"]:
        zero_point_invalid_reasons.append(
            "零点局部实测斜率计算失败: "
            + "；".join(zero_point_linear["rejection_reasons"])
        )
    if hwhm_hz is None:
        zero_point_invalid_reasons.append("缺少有效的幅度等效 HWHM")
    if zero_point_sensitivity is not None and not bool(
        zero_point_sensitivity["flat_detection_success"]
    ):
        zero_point_invalid_reasons.append(
            "零点局部斜率法自动平坦段识别失败: "
            f"{zero_point_sensitivity['flat_detection_reason']}"
        )
    result.update(
        {
            "valid": not invalid_reasons,
            "invalid_reasons": invalid_reasons,
            "warnings": warnings,
            "primary_slope": primary_slope,
            "linear": linear,
            "slope_difference": slope_difference,
            "zero_point_linear": zero_point_linear,
            "zero_point_slope": zero_point_slope,
            "zero_point_sensitivity": zero_point_sensitivity,
            "zero_point_valid": not zero_point_invalid_reasons,
            "zero_point_invalid_reasons": zero_point_invalid_reasons,
            "frequency_scan_hz": frequency_scan_hz,
            "frequency_response_v": frequency_response_v,
            "linewidth_fit": linewidth_fit,
            "hwhm_hz": hwhm_hz,
            "linewidth_result": linewidth_result,
            "noise_rates": rates,
            "frequency_hz": frequency_hz,
            "psd_r": psd_r,
            "sensitivity": sensitivity,
        }
    )
    return result
