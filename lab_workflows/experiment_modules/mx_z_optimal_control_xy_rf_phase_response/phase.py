"""采集期正式非线性色散判定与历史正弦诊断；保留原算法。"""
from typing import Any
import numpy as np
from .models import MxZOptimalControlXYRFPhaseResponseParams
from ..mx_keithley_6221_optimal_control_rf_sensitivity.phase import fit_dispersion_phase_scan, dispersion_phase_response
from ..mx_z_optimal_control_rf_sensitivity.phase import fit_phase_scan

NONLINEAR_FIT_MAX_STARTS = 48


def _fit_payload(
    phase_deg: np.ndarray,
    r_mean_v: np.ndarray,
    params: MxZOptimalControlXYRFPhaseResponseParams,
    *,
    r_std_v: np.ndarray | None = None,
    final_cross_phase_outliers: tuple[float, ...] = (),
) -> tuple[dict[str, Any], dict[str, float]]:
    noise = (
        np.zeros_like(r_mean_v, dtype=float)
        if r_std_v is None
        else np.asarray(r_std_v, dtype=float)
    )
    nonlinear = fit_dispersion_phase_scan(
        phase_deg,
        r_mean_v,
        noise,
        y_rf_amplitude_vpp=params.phase_cal_rf_amplitude_vpp,
        r_squared_min=params.phase_fit_r_squared_min,
        amplitude_sigma_min=params.phase_fit_amplitude_sigma_min,
        max_starts=NONLINEAR_FIT_MAX_STARTS,
    )
    legacy_primary, diagnostic = fit_phase_scan(
        phase_deg,
        r_mean_v,
        r_squared_min=params.phase_fit_r_squared_min,
        amplitude_sigma_min=params.phase_fit_amplitude_sigma_min,
    )
    rejection_reasons = list(nonlinear.rejection_reasons)
    if final_cross_phase_outliers:
        rejection_reasons.append(
            "单次跨相位重采后仍存在异常点: "
            + ", ".join(
                f"{float(value):.6g}°"
                for value in final_cross_phase_outliers
            )
        )
    primary_payload = nonlinear.to_dict()
    if rejection_reasons != list(nonlinear.rejection_reasons):
        primary_payload["success"] = False
        primary_payload["rejection_reasons"] = rejection_reasons
    payload = {
        "primary_fit": primary_payload,
        "diagnostic_fit": diagnostic.to_dict(),
        "legacy_primary_fit": legacy_primary.to_dict(),
        "fit_accepted": bool(nonlinear.success and not final_cross_phase_outliers),
    }
    summary = {
        # 这些字段保留旧 raw schema，实际正式判定来自非线性模型。
        "baseline_v": float(legacy_primary.parameters[0]),
        "amplitude_v": float(legacy_primary.parameters[1]),
        "phase_zero_deg": float(legacy_primary.parameters[2]),
        "baseline_uncertainty_v": float(legacy_primary.uncertainties[0]),
        "amplitude_uncertainty_v": float(legacy_primary.uncertainties[1]),
        "phase_zero_uncertainty_deg": float(legacy_primary.uncertainties[2]),
        "selected_phase_deg": float(legacy_primary.selected_phase_deg),
        "r_squared": float(legacy_primary.r_squared),
        "nonlinear_scale": float(nonlinear.parameters[0]),
        "nonlinear_resonance_amplitude_vpp": float(nonlinear.parameters[1]),
        "nonlinear_width_vpp": float(nonlinear.parameters[2]),
        "nonlinear_residual_amplitude_vpp": float(nonlinear.parameters[3]),
        "nonlinear_residual_phase_deg": float(nonlinear.parameters[4]),
        "nonlinear_scale_uncertainty": float(nonlinear.uncertainties[0]),
        "nonlinear_resonance_amplitude_vpp_uncertainty": float(nonlinear.uncertainties[1]),
        "nonlinear_width_vpp_uncertainty": float(nonlinear.uncertainties[2]),
        "nonlinear_residual_amplitude_vpp_uncertainty": float(nonlinear.uncertainties[3]),
        "nonlinear_residual_phase_deg_uncertainty": float(nonlinear.uncertainties[4]),
        "nonlinear_selected_phase_deg": float(nonlinear.selected_phase_deg),
        "nonlinear_observed_max_phase_deg": float(nonlinear.observed_max_phase_deg),
        "nonlinear_peak_to_peak_v": float(nonlinear.peak_to_peak_v),
        "nonlinear_noise_median_v": float(nonlinear.noise_median_v),
        "nonlinear_signal_to_noise": float(nonlinear.signal_to_noise),
        "nonlinear_r_squared": float(nonlinear.r_squared),
        "nonlinear_rmse_v": float(
            np.sqrt(
                np.mean(
                    (
                        np.asarray(r_mean_v, dtype=float)
                        - dispersion_phase_response(
                            phase_deg,
                            *nonlinear.parameters,
                            params.phase_cal_rf_amplitude_vpp,
                        )
                    )
                    ** 2
                )
            )
        ),
        "fit_accepted": bool(nonlinear.success and not final_cross_phase_outliers),
    }
    return payload, summary
