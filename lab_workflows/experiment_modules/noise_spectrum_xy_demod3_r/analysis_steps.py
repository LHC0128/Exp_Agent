"""矩阵资格、内容指纹和原科学结果写出；不连接硬件。"""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import numpy as np
import yaml
from ...experiment_runtime import check_cancelled
from . import run_format
from .models import NoiseSpectrumXYDemod3RParams
from .analysis_core import analyze_r_matrix

ANALYSIS_VERSION = "demod3-r-modular-v1"


def output_path(run_dir, name):
    path = Path(run_dir) / name
    if path.resolve() != path:
        raise ValueError("分析输出不能使用符号链接或目录联接")
    return path


def signature(path):
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            check_cancelled()
            digest.update(block)
    return [path.stat().st_size, digest.hexdigest()]


def load_config(run_dir):
    config = yaml.safe_load((Path(run_dir) / "experiment_config.yaml").read_text(encoding="utf-8"))
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    params = NoiseSpectrumXYDemod3RParams.from_external(config["parameters"], schema_version=config.get("schema_version", 1))
    return config, params


def fingerprint(run_dir):
    config, _ = load_config(run_dir)
    config = {k: v for k, v in config.items() if k not in ("analysis_status", "analysis_error")}
    files = {}
    for path in sorted((Path(run_dir) / "raw").glob("*")):
        if path.is_file():
            if path.resolve() != path:
                raise ValueError("原始数据不能通过链接引用其他文件")
            files[path.name] = signature(path)
    return dict(analysis_version=ANALYSIS_VERSION, raw_files=files,
        config_sha256=hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest())


def load_data(run_dir):
    config, params = load_config(run_dir)
    raw = Path(run_dir) / "raw"
    path = raw / "demod3_r_mean_matrix_recovered.npz"
    if not path.exists():
        path = raw / run_format.RAW_FILE
    if path.resolve() != path:
        raise ValueError("原始矩阵不能通过链接引用其他文件")
    with np.load(path, allow_pickle=False) as data:
        matrix = np.asarray(data["r_mean_V"], dtype=float)
        control = np.asarray(data["control_frequency_Hz"], dtype=float)
        envelope = np.asarray(data["xy_envelope_voltage_V"], dtype=float)
        frequencies = np.asarray(data["demod3_frequency_Hz"], dtype=float)
    for name, axis in (("control", control), ("envelope", envelope), ("demod3", frequencies)):
        if axis.ndim != 1 or len(axis) < 2 or not np.isfinite(axis).all() or not (np.diff(axis) > 0).all():
            raise ValueError(f"{name} 轴必须是一维、有限、严格递增且至少两点")
    if len(envelope) != len(control) or matrix.shape != (len(control), len(frequencies)):
        raise ValueError("R 矩阵形状与扫描轴不一致")
    if np.any(frequencies <= 0):
        raise ValueError("Demod3 频率必须大于零")
    return dict(config=config, params=params, matrix=matrix, control=control,
                envelope=envelope, frequencies=frequencies)


def write_inputs(run_dir, data):
    np.savez(output_path(run_dir, "results/demod3_r_matrix.npz"), r_mean_V=data["matrix"],
        control_frequency_configured_Hz=data["control"], xy_envelope_voltage_V=data["envelope"],
        demod3_frequency_Hz=data["frequencies"])
    return dict(matrix_shape=list(data["matrix"].shape), finite_points=int(np.isfinite(data["matrix"]).sum()))


def evaluate(run_dir, data):
    check_cancelled()
    analysis = analyze_r_matrix(data["matrix"], data["envelope"], data["frequencies"])
    calibration = analysis.calibration
    check_cancelled()
    np.savez(output_path(run_dir, "results/calibration.npz"),
        slope_Hz_per_V=np.float64(calibration.slope_hz_per_v), intercept_Hz=np.float64(calibration.intercept_hz),
        residual_sigma_Hz=np.float64(calibration.residual_sigma_hz), ridge_envelope_V=analysis.ridge_envelope_v,
        ridge_frequency_Hz=analysis.ridge_frequency_hz, inlier_mask=calibration.inlier_mask,
        calibrated_control_frequency_Hz=analysis.calibrated_control_frequency_hz,
        source=np.array("demod3_r_ridge_robust_fit"))
    np.savez(output_path(run_dir, "results/r_fit_params.npz"), parameters=analysis.fit_parameters,
        errors=analysis.fit_errors, fit_mask=analysis.fit_mask,
        parameter_names=np.array(["gamma_Hz", "fit_amplitude", "r_baseline_V", "frequency_offset_Hz"]),
        demod3_frequency_Hz=data["frequencies"], calibrated_control_frequency_Hz=analysis.calibrated_control_frequency_hz)
    np.savez(output_path(run_dir, "results/r_spectra.npz"), demod3_frequency_Hz=data["frequencies"],
        fit_amplitude=analysis.fit_parameters[:, 1], r_baseline_V=analysis.fit_parameters[:, 2],
        gamma_Hz=analysis.fit_parameters[:, 0], frequency_offset_Hz=analysis.fit_parameters[:, 3], fit_mask=analysis.fit_mask)
    return dict(experiment_id=data["config"].get("experiment_id"), analysis_source="mean Demod3 sample.r matrix",
        calibration_source="demod3_r_ridge_robust_fit",
        calibration_slope_Hz_per_V=float(calibration.slope_hz_per_v),
        calibration_intercept_Hz=float(calibration.intercept_hz),
        calibration_residual_sigma_Hz=float(calibration.residual_sigma_hz),
        fitted_frequency_points=int(np.count_nonzero(analysis.fit_mask)),
        total_frequency_points=int(len(data["frequencies"])),
        units_note="R is voltage amplitude, not PSD; no S_beta/N_S1 fields are emitted.")


def scientific_summary(run_dir, data):
    with np.load(Path(run_dir) / "results/calibration.npz", allow_pickle=False) as cal, np.load(
            Path(run_dir) / "results/r_fit_params.npz", allow_pickle=False) as fit:
        return dict(experiment_id=data["config"].get("experiment_id"), analysis_source="mean Demod3 sample.r matrix",
            calibration_source="demod3_r_ridge_robust_fit",
            calibration_slope_Hz_per_V=float(cal["slope_Hz_per_V"]),
            calibration_intercept_Hz=float(cal["intercept_Hz"]),
            calibration_residual_sigma_Hz=float(cal["residual_sigma_Hz"]),
            fitted_frequency_points=int(np.count_nonzero(fit["fit_mask"])),
            total_frequency_points=int(len(data["frequencies"])),
            units_note="R is voltage amplitude, not PSD; no S_beta/N_S1 fields are emitted.")


def write_report(run_dir, data):
    summary = scientific_summary(run_dir, data)
    output_path(run_dir, "results/analysis.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    output_path(run_dir, "results/analysis.yaml").write_text(yaml.safe_dump(summary, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return summary
