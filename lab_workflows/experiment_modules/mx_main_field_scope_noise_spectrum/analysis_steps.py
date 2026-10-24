"""原始输入、局部诊断与二维分析接线；科学算法保持原实现。"""
from __future__ import annotations
from dataclasses import fields
import hashlib
import json
import math
import os
import matplotlib
matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))
from pathlib import Path
from typing import Any
import numpy as np
import yaml
from scipy import signal as scipy_signal
from ...analysis.noise_spectrum_separation import fit_noise_separation
from ...experiment_runtime import check_cancelled
from ...plotting import set_plot_style
from .global_analysis import assess_global_noise_accuracy, fit_global_noise_separation, project_global_noise_separation
from .models import MxMainFieldScopeNoiseSpectrumParams
from .stages import ANALYSIS_KEYS
from . import run_format
from .analysis_plotting import (
    _plot_control_axis,
    _plot_psd_matrix,
    _plot_extracted_spectra,
    _plot_fit_parameters,
    _select_example_indices,
    _plot_fit_examples,
    _plot_global_2d_spectra,
    _plot_global_2d_background,
    _plot_global_2d_residual,
    _plot_global_2d_accuracy,
    _plot_global_2d_examples
)


def plain(value):
    """接口状态使用严格 JSON；原科学数组和科学报告仍沿用原格式。"""
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class AnalysisQualityError(ValueError):
    """原始数据不合格；拒绝破坏仍可查看的旧产物。"""


def contained(path, root):
    path = Path(path)
    if not path.resolve().is_relative_to(Path(root).resolve()) or path.resolve() != path.absolute():
        raise AnalysisQualityError(f"路径经过目录联接或越出运行目录: {path}")
    return path


def signature(path):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return [path.stat().st_size, digest.hexdigest()]


def resolve_options(params, options):
    if unknown := options.keys() - set(ANALYSIS_KEYS):
        raise ValueError("不支持的分析覆盖参数：" + "、".join(sorted(unknown)))
    resolved = type(params).from_external(params.to_external() | options)
    for field in fields(resolved):
        if field.metadata.get("external_name") not in ANALYSIS_KEYS:
            continue
        value = getattr(resolved, field.name)
        for bound, invalid in (("minimum", lambda v, limit: v < limit), ("maximum", lambda v, limit: v > limit)):
            limit = field.metadata.get(bound)
            if limit is not None and invalid(value, limit):
                raise ValueError(f"{field.metadata['external_name']} 超出 {bound}")
    if errors := resolved.validate_model():
        raise ValueError("；".join(errors))
    return resolved


def fingerprint(run_dir):
    config, _ = load_config(run_dir)
    config = {key: value for key, value in config.items() if key not in ("analysis_status", "analysis_error")}
    raw = contained(run_dir / "raw", run_dir)
    return dict(config_sha256=hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        raw_files={p.relative_to(run_dir).as_posix(): signature(contained(p, run_dir))
                   for p in sorted(raw.rglob("*")) if p.is_file()}, analysis_version="main-field-scope-v1")


def load_config(run_dir):
    contained(run_dir / "experiment_config.yaml", run_dir)
    params, config = _load_params(run_dir)
    return config, params


def load_inputs(run_dir, params=None):
    config, saved_params = load_config(run_dir)
    params = params or saved_params
    records = _load_waveforms(contained(run_dir / "raw", run_dir))
    currents = np.asarray([r["main_field_current_ma"] for r in records])
    control = np.asarray([r["control_frequency_hz"] for r in records])
    larmor = np.asarray([r["larmor_frequency_hz"] for r in records])
    predicted = params.main_field_calibration_hz_per_ma * currents + params.main_field_calibration_intercept_hz
    if (not np.all(np.isfinite(predicted)) or not np.allclose(predicted, control, rtol=0, atol=1e-6)
            or not np.allclose(larmor, control, rtol=0, atol=1e-9) or np.any(np.diff(control) <= 0)
            or any(r["point_index"] >= params.control_frequency_points for r in records)):
        raise AnalysisQualityError("原始波形索引、主场标定或控制频率轴不一致")
    # 先检查完整 Welch 网格，坏输入不会先归档已有科学结果。
    frequency = None
    for record in records:
        n = min(params.welch_nperseg, len(record["voltage_v"]))
        axis = np.fft.rfftfreq(n, 1 / record["actual_rate_sa_s"])
        if frequency is not None and (axis.shape != frequency.shape or not np.allclose(axis, frequency, rtol=1e-8, atol=1e-8)):
            raise AnalysisQualityError("各主场点的真实采样率或 Welch 频率轴不一致")
        frequency = axis
    if np.count_nonzero((frequency > 0) & (frequency <= params.control_frequency_stop_hz)) < 3:
        raise AnalysisQualityError("拟合频率点不足 3 个")
    return dict(completed_points=len(records), raw_format_version=config.get("raw_format_version"),
                parameter_schema_version=config.get("schema_version"), acquisition_signal="SDS CH1 PD voltage")


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
) -> tuple[MxMainFieldScopeNoiseSpectrumParams, dict[str, Any]]:
    config_path = run_dir / "experiment_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"未找到实验配置: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    if reason := run_format.analysis_block_reason(config):
        raise AnalysisQualityError(reason)
    params = MxMainFieldScopeNoiseSpectrumParams.from_external(
        config.get("parameters", {}),
        schema_version=int(config.get("schema_version", 1)),
    )
    errors = params.validate_model()
    if errors:
        raise ValueError("；".join(errors))
    return params, config


def _load_waveforms(raw_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(raw_dir.glob("waveform_I*.npz")):
        check_cancelled()
        contained(path, raw_dir.parent)
        with np.load(path, allow_pickle=False) as data:
            time_s = np.asarray(data["time_s"], dtype=float)
            voltage_v = np.asarray(data["voltage_v"], dtype=float)
            if (time_s.ndim != 1 or voltage_v.ndim != 1 or time_s.size != voltage_v.size or time_s.size < 8
                    or not np.all(np.isfinite(time_s)) or not np.all(np.isfinite(voltage_v)) or np.any(np.diff(time_s) <= 0)):
                raise ValueError(f"示波器时间轴与波形长度无效: {path}")
            sample_interval_s = float(np.median(np.diff(time_s)))
            if not np.isfinite(sample_interval_s) or sample_interval_s <= 0.0:
                raise ValueError(f"示波器时间轴采样间隔无效: {path}")
            calculated_rate_sa_s = 1.0 / sample_interval_s
            saved_rate_sa_s = float(data["actual_rate_sa_s"])
            if not np.isclose(
                calculated_rate_sa_s,
                saved_rate_sa_s,
                rtol=1e-6,
                atol=1e-6,
            ):
                raise ValueError(f"保存采样率与真实时间轴不一致: {path}")
            records.append(
                {
                    "path": path,
                    "point_index": int(data["point_index"]),
                    "control_frequency_hz": float(data["control_frequency_hz"]),
                    "larmor_frequency_hz": float(data["larmor_frequency_hz"]),
                    "main_field_current_ma": float(data["main_field_current_ma"]),
                    "actual_rate_sa_s": calculated_rate_sa_s,
                    "scale_used_v_div": float(data["scale_used_v_div"]),
                    "voltage_v": voltage_v,
                }
            )
    if len(records) < 10:
        raise ValueError(
            f"至少需要 10 个完整主场示波器点，当前仅发现 {len(records)} 个"
        )
    records.sort(key=lambda item: int(item["point_index"]))
    indices = [r["point_index"] for r in records]
    if len(set(indices)) != len(indices) or min(indices) < 0:
        raise AnalysisQualityError("原始波形点索引重复或为负数")
    return records


def _compute_psd_matrix(
    records: list[dict[str, Any]], nperseg: int
) -> tuple[np.ndarray, np.ndarray]:
    """使用各波形真实采样率计算 Welch PSD，并要求频率网格一致。"""
    rows: list[np.ndarray] = []
    frequency_axis: np.ndarray | None = None
    for record in records:
        waveform = np.asarray(record["voltage_v"], dtype=float)
        waveform = waveform[np.isfinite(waveform)]
        if waveform.size < 8:
            raise ValueError(f"PD 有效样本不足 8: {record['path']}")
        point_nperseg = min(int(nperseg), waveform.size)
        frequency, psd = scipy_signal.welch(
            waveform,
            fs=float(record["actual_rate_sa_s"]),
            nperseg=point_nperseg,
            noverlap=None,
            scaling="density",
        )
        if frequency_axis is None:
            frequency_axis = frequency
        elif frequency.shape != frequency_axis.shape or not np.allclose(
            frequency, frequency_axis, rtol=1e-8, atol=1e-8
        ):
            raise ValueError("各主场点的真实采样率或 Welch 频率轴不一致")
        rows.append(psd)
    assert frequency_axis is not None
    return frequency_axis, np.asarray(rows, dtype=float)


def _fit_local(params, results_dir, frequency_axis_hz, psd_matrix, calibrated_control_hz, main_field_current_ma):
    fit_frequency_mask = (
        (frequency_axis_hz > 0.0)
        & (frequency_axis_hz <= params.control_frequency_stop_hz)
    )
    fit_frequency_axis_hz = frequency_axis_hz[fit_frequency_mask]
    fit_psd_matrix = psd_matrix[:, fit_frequency_mask]
    if fit_frequency_axis_hz.size < 3:
        raise ValueError("0 Hz 以上且不超过控制频率上限的 PSD 频率点不足 3 个")
    fit = fit_noise_separation(
        fit_psd_matrix,
        fit_frequency_axis_hz,
        calibrated_control_hz,
        peak_margin_hz=params.fit_peak_margin_hz,
        fit_half_width_hz=params.fit_half_width_hz,
        gamma_guess_hz=params.fit_gamma_guess_hz,
    )
    np.savez(
        results_dir / "popt_fit.npz",
        popt=fit.parameters,
        perr=fit.uncertainties,
        fit_mask=fit.fit_mask,
        interpolated_mask=fit.interpolated_mask,
        frequency_axis_hz=fit_frequency_axis_hz,
        param_names=np.asarray(["gamma", "Amp", "D", "dw"], dtype=str),
        fit_control_half_width_hz=np.float64(params.fit_half_width_hz),
    )
    np.savez(
        results_dir / "noise_spectra.npz",
        S_beta=fit.s_beta,
        N_S1=fit.n_s1,
        freq_axis=fit_frequency_axis_hz,
        omega_ctrl=calibrated_control_hz,
        main_field_current_ma=main_field_current_ma,
        fit_mask=fit.fit_mask,
        interpolated_mask=fit.interpolated_mask,
        fit_control_half_width_hz=np.float64(params.fit_half_width_hz),
    )
    np.savetxt(
        results_dir / "noise_spectra.csv",
        np.column_stack(
            [
                fit_frequency_axis_hz,
                fit.s_beta,
                fit.n_s1,
                fit.fit_mask.astype(int),
                fit.interpolated_mask.astype(int),
            ]
        ),
        delimiter=",",
        header=(
            "frequency_Hz,S_beta_controllable_V2_per_Hz,"
            "N_S1_uncontrollable_V2_per_Hz,direct_fit,interpolated"
        ),
        comments="",
    )
    np.savetxt(
        results_dir / "lorentzian_fit_parameters.csv",
        np.column_stack(
            [
                fit_frequency_axis_hz,
                fit.parameters,
                fit.uncertainties,
                fit.fit_mask.astype(int),
                fit.interpolated_mask.astype(int),
            ]
        ),
        delimiter=",",
        header=(
            "frequency_Hz,gamma_Hz,Amp,D,dw_Hz,gamma_err,Amp_err,D_err,"
            "dw_err,direct_fit,interpolated"
        ),
        comments="",
    )

    return fit_frequency_axis_hz, fit_psd_matrix, fit


def _fit_global(params, results_dir, records, frequency_axis_hz, psd_matrix, calibrated_control_hz):
    global_files: list[str] = []
    global_payload: dict[str, Any] = {
        "enabled": params.global_2d_analysis_enabled,
        "status": "disabled",
    }
    if params.global_2d_analysis_enabled:
        global_upper_hz = min(
            params.global_fit_frequency_max_hz,
            params.control_frequency_stop_hz,
            float(frequency_axis_hz[-1]),
        )
        global_frequency_mask = (
            (frequency_axis_hz >= params.global_fit_frequency_min_hz)
            & (frequency_axis_hz <= global_upper_hz)
        )
        global_frequency_hz = frequency_axis_hz[global_frequency_mask]
        global_psd_matrix = psd_matrix[:, global_frequency_mask]
        core_frequency_mask = (
            (frequency_axis_hz >= params.global_core_fit_frequency_min_hz)
            & (frequency_axis_hz <= global_upper_hz)
        )
        core_frequency_hz = frequency_axis_hz[core_frequency_mask]
        core_psd_matrix = psd_matrix[:, core_frequency_mask]
        if (
            global_frequency_hz.size >= 20
            and core_frequency_hz.size >= 20
            and calibrated_control_hz.size >= 20
        ):
            core_fit = fit_global_noise_separation(
                core_psd_matrix,
                core_frequency_hz,
                calibrated_control_hz,
                gamma_guess_hz=params.fit_gamma_guess_hz,
                background_margin_hz=params.global_background_margin_hz,
                cv_folds=params.global_cv_folds,
            )
            global_fit = project_global_noise_separation(
                global_psd_matrix,
                global_frequency_hz,
                calibrated_control_hz,
                core_fit,
                cv_folds=params.global_cv_folds,
            )
            global_accuracy = assess_global_noise_accuracy(
                core_psd_matrix,
                core_frequency_hz,
                calibrated_control_hz,
                core_fit,
                background_margin_hz=params.global_background_margin_hz,
                cv_folds=params.global_cv_folds,
                projection_psd_matrix=global_psd_matrix,
                projection_frequency_hz=global_frequency_hz,
                projection_result=global_fit,
            )
            valid = global_accuracy.quantitative_valid_mask
            valid_count = int(np.count_nonzero(valid))
            overlap_points = max(1, params.welch_nperseg // 2)
            welch_segment_counts = np.asarray(
                [
                    1 + max(0, (len(record["voltage_v"]) - params.welch_nperseg) // overlap_points)
                    for record in records
                ],
                dtype=int,
            )
            minimum_welch_segments = int(np.min(welch_segment_counts))
            expected_relative_sigma = 1.0 / np.sqrt(minimum_welch_segments)
            empirical_fractional_residual = np.abs(
                global_fit.residual_matrix
                / np.maximum(global_fit.model_matrix, np.finfo(float).tiny)
            )
            np.savez(
                results_dir / "global_2d_noise_separation.npz",
                frequency_hz=global_fit.frequency_hz,
                control_frequency_hz=global_fit.control_frequency_hz,
                gamma_hz=np.float64(global_fit.gamma_hz),
                control_offset_hz=np.float64(global_fit.control_offset_hz),
                N_S1=global_fit.n_s1,
                C_S_beta=global_fit.c_s_beta,
                peak_contribution_v2_hz=global_fit.peak_contribution,
                background_profile=global_fit.background_profile,
                model_matrix=global_fit.model_matrix,
                residual_matrix=global_fit.residual_matrix,
                median_absolute_fractional_residual=(
                    global_fit.median_absolute_fractional_residual
                ),
                cv_N_S1_relative_std=global_fit.cv_n_s1_relative_std,
                cv_C_S_beta_relative_std=global_fit.cv_c_s_beta_relative_std,
                N_S1_systematic_relative_std=(
                    global_accuracy.n_s1_systematic_relative_std
                ),
                C_S_beta_systematic_relative_std=(
                    global_accuracy.c_s_beta_systematic_relative_std
                ),
                N_S1_total_relative_uncertainty=(
                    global_accuracy.n_s1_total_relative_uncertainty
                ),
                C_S_beta_total_relative_uncertainty=(
                    global_accuracy.c_s_beta_total_relative_uncertainty
                ),
                cv_deviance=global_fit.cv_deviance,
                quantitative_valid_mask=valid,
                fold_gamma_hz=global_accuracy.fold_gamma_hz,
                fold_control_offset_hz=global_accuracy.fold_control_offset_hz,
                background_margin_hz=global_accuracy.background_margin_hz,
                margin_gamma_hz=global_accuracy.margin_gamma_hz,
                margin_control_offset_hz=(
                    global_accuracy.margin_control_offset_hz
                ),
            )
            np.savetxt(
                results_dir / "global_2d_noise_spectra.csv",
                np.column_stack(
                    [
                        global_fit.frequency_hz,
                        global_fit.c_s_beta,
                        global_fit.n_s1,
                        global_fit.peak_contribution,
                        global_fit.cv_c_s_beta_relative_std,
                        global_fit.cv_n_s1_relative_std,
                        global_accuracy.c_s_beta_systematic_relative_std,
                        global_accuracy.n_s1_systematic_relative_std,
                        global_accuracy.c_s_beta_total_relative_uncertainty,
                        global_accuracy.n_s1_total_relative_uncertainty,
                        global_fit.median_absolute_fractional_residual,
                        global_fit.cv_deviance,
                        valid.astype(int),
                    ]
                ),
                delimiter=",",
                header=(
                    "frequency_Hz,C_S_beta,N_S1_V2_per_Hz,"
                    "peak_contribution_V2_per_Hz,C_S_beta_cv_rel_std,"
                    "N_S1_cv_rel_std,C_S_beta_systematic_rel_std,"
                    "N_S1_systematic_rel_std,C_S_beta_total_rel_uncertainty,"
                    "N_S1_total_rel_uncertainty,median_abs_fractional_residual,"
                    "cv_deviance,quantitative_valid"
                ),
                comments="",
            )
            representative_points = []
            for target_hz in (
                500.0,
                1000.0,
                1500.0,
                2000.0,
                5500.0,
                11125.0,
                30000.0,
                40000.0,
            ):
                index = int(np.argmin(np.abs(global_fit.frequency_hz - target_hz)))
                representative_points.append(
                    {
                        "frequency_hz": float(global_fit.frequency_hz[index]),
                        "C_S_beta": float(global_fit.c_s_beta[index]),
                        "N_S1_v2_hz": float(global_fit.n_s1[index]),
                        "C_S_beta_total_relative_uncertainty": float(
                            global_accuracy.c_s_beta_total_relative_uncertainty[index]
                        ),
                        "N_S1_total_relative_uncertainty": float(
                            global_accuracy.n_s1_total_relative_uncertainty[index]
                        ),
                        "quantitative_valid": bool(valid[index]),
                    }
                )
            global_payload = {
                "enabled": True,
                "status": "completed",
                "preferred_quantitative_method": True,
                "separation_strategy": (
                    "fit global response and H(Omega) on the core band, then "
                    "project N_S1 and C_S_beta onto the reported band"
                ),
                "model": (
                    "P(Omega,w) = H(Omega)*N_S1(w) + "
                    "L(Omega,w;gamma,offset)*C_S_beta(w)"
                ),
                "C_S_beta_definition": "4*G^2*S_2^2*S_beta",
                "absolute_S_beta_available": False,
                "absolute_S_beta_limitation": (
                    "需要独立标定 4*G^2*S_2^2；当前结果只给出乘积 C_S_beta"
                ),
                "frequency_min_hz": float(global_fit.frequency_hz[0]),
                "frequency_max_hz": float(global_fit.frequency_hz[-1]),
                "frequency_points": int(global_fit.frequency_hz.size),
                "core_fit_frequency_min_hz": float(core_fit.frequency_hz[0]),
                "core_fit_frequency_max_hz": float(core_fit.frequency_hz[-1]),
                "core_fit_frequency_points": int(core_fit.frequency_hz.size),
                "gamma_hz": global_fit.gamma_hz,
                "control_offset_hz": global_fit.control_offset_hz,
                "optimizer_success": global_fit.optimizer_success,
                "optimizer_message": global_fit.optimizer_message,
                "background": {
                    "rank": 1,
                    "normalization": "median H above the 60th control-frequency percentile equals 1",
                    "margin_hz": params.global_background_margin_hz,
                    "H_at_control_start": float(global_fit.background_profile[0]),
                    "H_at_control_stop": float(global_fit.background_profile[-1]),
                },
                "accuracy": {
                    "interpretation": "内部精度，不包含 4*G^2*S_2^2 标定误差和模型外系统误差",
                    "cv_folds": params.global_cv_folds,
                    "fold_gamma_mean_hz": float(np.mean(global_accuracy.fold_gamma_hz)),
                    "fold_gamma_std_hz": float(np.std(global_accuracy.fold_gamma_hz, ddof=1)),
                    "fold_control_offset_mean_hz": float(
                        np.mean(global_accuracy.fold_control_offset_hz)
                    ),
                    "fold_control_offset_std_hz": float(
                        np.std(global_accuracy.fold_control_offset_hz, ddof=1)
                    ),
                    "background_margin_values_hz": global_accuracy.background_margin_hz,
                    "background_margin_gamma_hz": global_accuracy.margin_gamma_hz,
                    "whittle_deviance_mean": global_fit.whittle_deviance_mean,
                    "heldout_whittle_deviance_mean": global_fit.cv_deviance_mean,
                    "log_r_squared": global_fit.log_r_squared,
                    "core_whittle_deviance_mean": core_fit.whittle_deviance_mean,
                    "core_heldout_whittle_deviance_mean": core_fit.cv_deviance_mean,
                    "core_log_r_squared": core_fit.log_r_squared,
                    "constant_background_whittle_deviance_mean": (
                        global_accuracy.constant_background_whittle_deviance_mean
                    ),
                    "constant_background_log_r_squared": (
                        global_accuracy.constant_background_log_r_squared
                    ),
                    "minimum_welch_segment_count": minimum_welch_segments,
                    "independent_segment_relative_sigma_approx": float(
                        expected_relative_sigma
                    ),
                    "independent_segment_median_abs_residual_approx": float(
                        0.67448975 * expected_relative_sigma
                    ),
                    "empirical_pixel_median_abs_fractional_residual": float(
                        np.median(empirical_fractional_residual)
                    ),
                    "empirical_pixel_p90_abs_fractional_residual": float(
                        np.percentile(empirical_fractional_residual, 90.0)
                    ),
                    "valid_frequency_points": valid_count,
                    "total_frequency_points": int(valid.size),
                    "valid_frequency_min_hz": (
                        float(global_fit.frequency_hz[valid][0]) if valid_count else None
                    ),
                    "valid_frequency_max_hz": (
                        float(global_fit.frequency_hz[valid][-1]) if valid_count else None
                    ),
                    "C_S_beta_total_relative_uncertainty_median": float(
                        np.median(
                            global_accuracy.c_s_beta_total_relative_uncertainty[valid]
                        )
                    ) if valid_count else None,
                    "C_S_beta_total_relative_uncertainty_p90": float(
                        np.percentile(
                            global_accuracy.c_s_beta_total_relative_uncertainty[valid],
                            90.0,
                        )
                    ) if valid_count else None,
                    "N_S1_total_relative_uncertainty_median": float(
                        np.median(
                            global_accuracy.n_s1_total_relative_uncertainty[valid]
                        )
                    ) if valid_count else None,
                    "N_S1_total_relative_uncertainty_p90": float(
                        np.percentile(
                            global_accuracy.n_s1_total_relative_uncertainty[valid],
                            90.0,
                        )
                    ) if valid_count else None,
                },
                "representative_points": representative_points,
            }
            global_files = [
                "global_2d_noise_separation.npz",
                "global_2d_noise_spectra.csv",
                _plot_global_2d_spectra(results_dir, global_fit, global_accuracy),
                _plot_global_2d_background(results_dir, global_fit),
                _plot_global_2d_residual(results_dir, global_fit),
                _plot_global_2d_accuracy(results_dir, global_fit, global_accuracy),
                _plot_global_2d_examples(
                    results_dir, global_psd_matrix, global_fit
                ),
                "global_2d_analysis.yaml",
                "global_2d_analysis.json",
            ]
            with (results_dir / "global_2d_analysis.yaml").open(
                "w", encoding="utf-8"
            ) as stream:
                yaml.safe_dump(
                    _builtin(global_payload), stream, allow_unicode=True, sort_keys=False
                )
            with (results_dir / "global_2d_analysis.json").open(
                "w", encoding="utf-8"
            ) as stream:
                json.dump(
                    _builtin(global_payload), stream, ensure_ascii=False, indent=2
                )
        else:
            global_payload = {
                "enabled": True,
                "status": "skipped",
                "reason": "二维拟合频率范围内有效频率点不足 20 个",
            }

    return global_files, global_payload


def evaluate(run_dir: Path, params=None) -> dict[str, Any]:
    """只读取指定运行目录中的 PD 原始波形并写入 ``results/``。"""
    set_plot_style("paper")
    run_dir = Path(run_dir).resolve()
    saved_params, config = _load_params(run_dir)
    params = params or saved_params
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    records = _load_waveforms(raw_dir)

    point_indices = np.asarray([record["point_index"] for record in records], dtype=int)
    saved_control_hz = np.asarray(
        [record["control_frequency_hz"] for record in records], dtype=float
    )
    saved_larmor_hz = np.asarray(
        [record["larmor_frequency_hz"] for record in records], dtype=float
    )
    main_field_current_ma = np.asarray(
        [record["main_field_current_ma"] for record in records], dtype=float
    )
    actual_rate_sa_s = np.asarray(
        [record["actual_rate_sa_s"] for record in records], dtype=float
    )
    scale_used_v_div = np.asarray(
        [record["scale_used_v_div"] for record in records], dtype=float
    )
    calibrated_control_hz = (
        params.main_field_calibration_hz_per_ma * main_field_current_ma
        + params.main_field_calibration_intercept_hz
    )
    if not np.allclose(calibrated_control_hz, saved_control_hz, rtol=0.0, atol=1e-6):
        raise ValueError("原始数据中的主场电流不满足保存的外推标定式")
    if not np.allclose(saved_larmor_hz, saved_control_hz, rtol=0.0, atol=1e-9):
        raise ValueError("原始数据中的 Larmor 频率与控制频率不一致")
    if np.any(np.diff(calibrated_control_hz) <= 0.0):
        raise ValueError("主场控制频率轴必须严格递增")

    frequency_axis_hz, psd_matrix = _compute_psd_matrix(
        records, params.welch_nperseg
    )
    np.savez(
        results_dir / "psd_matrix.npz",
        psd_matrix=psd_matrix,
        frequency_axis_hz=frequency_axis_hz,
        control_frequency_hz=calibrated_control_hz,
        larmor_frequency_hz=calibrated_control_hz,
        main_field_current_ma=main_field_current_ma,
        point_index=point_indices,
        actual_rate_sa_s=actual_rate_sa_s,
        scale_used_v_div=scale_used_v_div,
        welch_nperseg=np.int64(params.welch_nperseg),
    )

    fit_frequency_axis_hz, fit_psd_matrix, fit = _fit_local(
        params, results_dir, frequency_axis_hz, psd_matrix, calibrated_control_hz, main_field_current_ma)
    global_files, global_payload = _fit_global(
        params, results_dir, records, frequency_axis_hz, psd_matrix, calibrated_control_hz)

    files = [
        "psd_matrix.npz",
        "popt_fit.npz",
        "noise_spectra.npz",
        "noise_spectra.csv",
        "lorentzian_fit_parameters.csv",
        _plot_control_axis(
            results_dir, main_field_current_ma, calibrated_control_hz
        ),
        _plot_psd_matrix(
            results_dir,
            frequency_axis_hz,
            calibrated_control_hz,
            psd_matrix,
            params.control_frequency_stop_hz,
        ),
        _plot_extracted_spectra(
            results_dir, fit_frequency_axis_hz, fit.s_beta, fit.n_s1
        ),
        _plot_fit_parameters(results_dir, fit_frequency_axis_hz, fit),
    ]
    files.extend(global_files)
    for stale_example in results_dir.glob("fit_example_*.png"):
        stale_example.unlink()
    example_files = _plot_fit_examples(
        results_dir,
        calibrated_control_hz,
        fit_frequency_axis_hz,
        fit_psd_matrix,
        fit,
        params.fit_example_plot_count,
        params.fit_half_width_hz,
    )
    files.extend(example_files)

    fitted_count = int(np.count_nonzero(fit.fit_mask))
    interpolated_count = int(np.count_nonzero(fit.interpolated_mask))
    payload = {
        "success": fitted_count > 0,
        "experiment_id": config.get(
            "experiment_id", "mx-main-field-scope-noise-spectrum"
        ),
        "run_dir": str(run_dir),
        "acquisition_signal": "SDS CH1 PD voltage",
        "control_model": (
            "control_Hz = Larmor_Hz = "
            "K_f_Hz_per_mA * current_mA + f_0mA_Hz"
        ),
        "main_field_calibration": {
            "source_run": params.main_field_calibration_source_run,
            "slope_hz_per_ma": params.main_field_calibration_hz_per_ma,
            "intercept_hz": params.main_field_calibration_intercept_hz,
            "extrapolation_enabled": True,
        },
        "scan": {
            "completed_points": len(records),
            "target_points": params.control_frequency_points,
            "control_start_hz": float(calibrated_control_hz[0]),
            "control_stop_hz": float(calibrated_control_hz[-1]),
            "main_field_current_start_ma": float(main_field_current_ma[0]),
            "main_field_current_stop_ma": float(main_field_current_ma[-1]),
        },
        "welch": {
            "nperseg": params.welch_nperseg,
            "actual_rate_min_sa_s": float(np.min(actual_rate_sa_s)),
            "actual_rate_max_sa_s": float(np.max(actual_rate_sa_s)),
            "frequency_start_hz": float(frequency_axis_hz[0]),
            "frequency_stop_hz": float(frequency_axis_hz[-1]),
        },
        "fit": {
            "model": (
                "D + A*(gamma^2+w^2) / "
                "((gamma^2-w^2+(Omega+dw)^2)^2+4*w^2*gamma^2)"
            ),
            "dc_excluded": True,
            "control_frequency_half_width_hz": params.fit_half_width_hz,
            "frequency_min_hz": float(fit_frequency_axis_hz[0]),
            "frequency_max_hz": float(fit_frequency_axis_hz[-1]),
            "fitted_frequency_points": fitted_count,
            "interpolated_frequency_points": interpolated_count,
            "eligible_frequency_points": int(fit_frequency_axis_hz.size),
            "example_plot_target_count": params.fit_example_plot_count,
            "example_plot_created_count": len(example_files),
        },
        "global_2d_fit": global_payload,
        "files": files,
    }
    payload["files"].extend(["analysis.yaml", "analysis.json"])
    serializable = _builtin(payload)
    with (results_dir / "analysis.yaml").open("w", encoding="utf-8") as stream:
        yaml.safe_dump(serializable, stream, allow_unicode=True, sort_keys=False)
    with (results_dir / "analysis.json").open("w", encoding="utf-8") as stream:
        json.dump(serializable, stream, ensure_ascii=False, indent=2)
    print(
        f"Mx 主场示波器噪声谱分析完成: {len(records)} 个主场点，"
        f"{fitted_count}/{fit_frequency_axis_hz.size} 个频率点直接拟合成功，"
        f"输出 {len(example_files)} 张示例拟合图"
    )
    return serializable
