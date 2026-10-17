"""只读静磁场原始格式 3 曲线；检测增益使用本轮 K_Z。"""
import hashlib
from pathlib import Path
import math
import numpy as np
import yaml
from sensitivity_analysis.fitting import fit_dispersive
from ..static_sensitivity.analysis_steps import load_scan, QUALITY_THRESHOLDS
from ..static_sensitivity.run_format import format_block_reason

SOURCE_TYPE = "Static_Magnetic_Field_Sensitivity"


def source_directory(root, run_id):
    if not isinstance(run_id, str) or not run_id or run_id in (".", "..") or any(c in run_id for c in "/\\:"):
        raise ValueError("请选择有效的静磁场标定运行目录")
    base = (Path(root) / "data" / SOURCE_TYPE).resolve()
    path = (base / run_id).resolve()
    if path.parent != base or not path.is_dir():
        raise ValueError("静磁场标定目录不存在或越界")
    return path


def file_fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fingerprint(root, run_id):
    path = source_directory(root, run_id)
    return {name: file_fingerprint(path / name) for name in ("experiment_config.yaml", "raw/dispersion_scan.npz")}


def load_source(root, run_id, k_z_hz_per_v, target_config=None):
    path = source_directory(root, run_id)
    config = yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8")) or {}
    if config.get("experiment_id", "static-sensitivity") != "static-sensitivity" or config.get("experiment_type") != SOURCE_TYPE:
        raise ValueError("来源不是静磁场灵敏度实验")
    if reason := format_block_reason(config):
        raise ValueError(reason)
    if config.get("completion_status") == "running":
        raise ValueError("来源仍在采集，请等待结束")
    scan = load_scan(path)
    if not scan["completed_mask"].all() or scan["offsets_v"].size < 7:
        raise ValueError("来源色散曲线未全部完成或点数不足")
    for key in ("x_median", "y_median", "r_median", "sample_counts", "actual_rate_sa_s", "actual_duration_s"):
        values = scan[key]
        if values.shape != scan["offsets_v"].shape or not np.isfinite(values).all():
            raise ValueError("来源色散数据不完整：" + key)
        if key in ("sample_counts", "actual_rate_sa_s", "actual_duration_s") and np.any(values <= 0):
            raise ValueError("来源色散采样记录无效：" + key)
    if not math.isfinite(k_z_hz_per_v) or k_z_hz_per_v <= 0:
        raise ValueError("本轮 K_Z 必须为有限正数")
    fit = fit_dispersive(scan["offsets_v"], scan["y_median"], k_z_hz_per_v, k_z_hz_per_v,
                        quality_thresholds=QUALITY_THRESHOLDS)
    if not fit.is_valid or not np.isfinite(fit.popt).all() or not math.isfinite(fit.slope_VV) or fit.slope_VV <= 0 \
            or not math.isfinite(fit.relative_gamma_uncertainty):
        raise ValueError("来源色散拟合质量不通过：" + "；".join(fit.rejection_reasons or ["拟合或不确定度无效"]))
    source_parameters = config.get("parameters", {})
    target = (target_config or {}).get("parameters", {})
    differences = []
    for source_key, target_key in (
        ("temperature", "FIXED_PARAMS.temperature"), ("pump_laser_power", "FIXED_PARAMS.Pump_laser_power"),
        ("probe_laser_power", "FIXED_PARAMS.Probe_laser_power"), ("main_magnetic_field", "FIXED_PARAMS.main_magnetic_field"),
        ("pump_mod_freq", "PUMP_MOD_FREQ"), ("pump_mod_amplitude", "PUMP_MOD_AMPLITUDE"),
        ("hf2_signal_range", "HF2_SIGNAL_RANGE"), ("hf2_demod_tc", "HF2_DEMOD_TC")):
        current = target.get(target_key, (target_config or {}).get("fixed_params", {}).get(source_key))
        original = source_parameters.get(source_key)
        if original != current:
            differences.append(dict(parameter=source_key, source=original, current=current))
    return dict(source="static_sensitivity_v3", source_run=run_id, method="dispersion_line_fit",
        k_z_hz_per_v=k_z_hz_per_v, slope_v_per_v=fit.slope_VV, slope_v_per_hz=fit.slope_VV / k_z_hz_per_v,
        gamma_v=fit.gamma_V, gamma_hz=fit.gamma_V * k_z_hz_per_v, center_v=fit.V0, center_hz=fit.V0*k_z_hz_per_v,
        r_squared=fit.r_squared, rmse_v=fit.rmse, n_points=int(scan["offsets_v"].size), is_valid=True,
        relative_gamma_uncertainty=fit.relative_gamma_uncertainty, quality_thresholds=QUALITY_THRESHOLDS,
        rejection_reasons=fit.rejection_reasons, source_parameters=source_parameters, condition_differences=differences,
        fingerprints=source_fingerprint(root, run_id), offsets_v=scan["offsets_v"], y_mean=scan["y_median"],
        x_median=scan["x_median"], r_median=scan["r_median"], popt=fit.popt,
        pcov=fit.pcov)


def source_choices(root):
    """下拉只列出可用曲线，不要求来源噪声分析完成。"""
    choices = []
    for path in sorted((Path(root) / "data" / SOURCE_TYPE).glob("*"), reverse=True):
        if not path.is_dir():
            continue
        try:
            fit = load_source(root, path.name, 1.0)
            conditions = fit["source_parameters"]
            label = f"{path.name} · {conditions.get('temperature', '—')} °C · R²={fit['r_squared']:.4f}"
            choices.append(dict(value=path.name, label=label))
        except (ValueError, OSError, KeyError, TypeError):
            continue
    return choices
