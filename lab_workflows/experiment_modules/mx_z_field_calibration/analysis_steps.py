"""原始频扫资格、Lorentzian 中心、加权线性标定与报告业务。"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import numpy as np
import yaml
from ..mx_y_rf_sensitivity.analysis_core import fit_lorentzian_response
from .models import MxZFieldCalibrationParams

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
) -> tuple[MxZFieldCalibrationParams, dict[str, Any]]:
    config_path = run_dir / "experiment_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"未找到实验配置: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    params = MxZFieldCalibrationParams.from_external(
        config.get("parameters", {}),
        schema_version=int(config.get("schema_version", 1)),
    )
    return params, config


def _load_scan_index(raw_dir: Path) -> list[dict[str, Any]]:
    path = raw_dir / "z_scan_index.npz"
    if not path.exists():
        # 采集被中断时，总索引可能尚未来得及写入，但已经完成的单个
        # Z 频扫汇总仍然是完整且可独立分析的。这里只发现汇总文件，
        # 不读取未完成频点，也不改写原始数据。
        entries: list[dict[str, Any]] = []
        for summary_path in sorted(raw_dir.glob("z_*/frequency_scan.npz")):
            with np.load(summary_path) as data:
                entries.append(
                    {
                        "z_bias_v": float(data["z_bias_v"]),
                        "predicted_center_hz": float(data["predicted_center_hz"]),
                        "summary_file": str(summary_path.relative_to(raw_dir.parent)),
                    }
                )
        if entries:
            return entries
        raise FileNotFoundError(f"缺少 Z 扫描索引和已完成频扫汇总: {path}")
    with np.load(path) as data:
        z_bias = np.asarray(data["z_bias_v"], dtype=float)
        predicted = np.asarray(data["predicted_center_hz"], dtype=float)
        files = np.asarray(data["summary_file"], dtype=str)
    if not (z_bias.size == predicted.size == files.size):
        raise ValueError("Z 扫描索引字段长度不一致")
    return [
        {
            "z_bias_v": float(z_value),
            "predicted_center_hz": float(center),
            "summary_file": str(filename),
        }
        for z_value, center, filename in zip(z_bias, predicted, files, strict=True)
    ]


def _discrete_peak_frequency(
    frequency_hz: np.ndarray, response_v: np.ndarray
) -> float:
    edge_count = max(1, frequency_hz.size // 10)
    baseline = float(
        np.median(np.r_[response_v[:edge_count], response_v[-edge_count:]])
    )
    return float(frequency_hz[int(np.argmax(np.abs(response_v - baseline)))])


def _analyze_curve(
    params: MxZFieldCalibrationParams,
    run_dir: Path,
    entry: dict[str, Any],
) -> dict[str, Any]:
    return analyze_curve(run_dir, entry)


def analyze_curve(run_dir: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """共用的 Z 电压频扫拟合；不依赖实验硬件参数。"""
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
    if fit.gamma > 0 and np.isfinite(fit.center):
        edge_distance = min(
            fit.center - float(np.min(frequency_hz)),
            float(np.max(frequency_hz)) - fit.center,
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


def _weighted_linear_fit(
    x: np.ndarray, y: np.ndarray, sigma: np.ndarray
) -> dict[str, Any]:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y, sigma = x[valid], y[valid], sigma[valid]
    if x.size < 2:
        return {
            "success": False,
            "slope_hz_per_v": np.nan,
            "intercept_hz": np.nan,
            "covariance": np.full((2, 2), np.nan),
            "uncertainties": np.full(2, np.nan),
            "r_squared": np.nan,
            "residual_hz": np.full(x.size, np.nan),
            "rejection_reasons": ["有效中心不足 2 个"],
        }
    finite_positive_sigma = np.isfinite(sigma) & (sigma > 0)
    fallback = float(np.median(sigma[finite_positive_sigma])) if np.any(finite_positive_sigma) else 1.0
    sigma = np.where(finite_positive_sigma, sigma, fallback)
    design = np.column_stack([x, np.ones_like(x)])
    weights = 1.0 / sigma**2
    normal = design.T @ (weights[:, None] * design)
    try:
        normal_inverse = np.linalg.inv(normal)
    except np.linalg.LinAlgError:
        return {
            "success": False,
            "slope_hz_per_v": np.nan,
            "intercept_hz": np.nan,
            "covariance": np.full((2, 2), np.nan),
            "uncertainties": np.full(2, np.nan),
            "r_squared": np.nan,
            "residual_hz": np.full(x.size, np.nan),
            "rejection_reasons": ["线性拟合法方程奇异"],
        }
    beta = normal_inverse @ (design.T @ (weights * y))
    predicted = design @ beta
    residual = y - predicted
    dof = max(1, x.size - 2)
    reduced_chi_square = float(np.sum((residual / sigma) ** 2) / dof)
    covariance = normal_inverse * reduced_chi_square
    uncertainties = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-30 else 0.0
    return {
        "success": True,
        "slope_hz_per_v": float(beta[0]),
        "intercept_hz": float(beta[1]),
        "covariance": covariance,
        "uncertainties": uncertainties,
        "r_squared": r_squared,
        "residual_hz": residual,
        "rejection_reasons": [],
    }


def load_inputs(run_dir, config, params):
    from .run_format import analysis_block_reason
    if reason := analysis_block_reason(config):
        raise ValueError(reason)
    entries = _load_scan_index(Path(run_dir) / "raw")
    if not entries:
        raise ValueError("没有已完成的 Z 频率扫描")
    for entry in entries:
        path = (Path(run_dir) / entry["summary_file"]).resolve()
        if Path(run_dir).resolve() not in path.parents or not path.is_file():
            raise ValueError("扫描索引引用了无效的原始数据路径")
        with np.load(path) as data:
            for key in ("frequency_hz", "r_std_v"):
                if key not in data:
                    raise ValueError("原始频扫缺少字段：" + key)
            if "r_mean_v" not in data and "r_scalar_mean_v" not in data:
                raise ValueError("原始频扫缺少 R 响应")
    return entries


def fit_curves(run_dir, params, config):
    curves = [analyze_curve(run_dir, entry) for entry in load_inputs(run_dir, config, params)]
    (run_dir / "results/curve_fits.json").write_text(json.dumps(_builtin(curves), ensure_ascii=False), encoding="utf-8")
    return {"curve_count": len(curves), "accepted_curve_count": sum(curve["fit"]["success"] for curve in curves)}


def evaluate_curves(run_dir, config, curves, linear_r_squared_min):
    accepted = np.asarray([curve["fit"]["success"] for curve in curves], dtype=bool)
    z_values = np.asarray([curve["z_bias_v"] for curve in curves], dtype=float)
    centers = np.asarray([curve["fit"]["parameters"][2] for curve in curves], dtype=float)
    center_uncertainties = np.asarray(
        [curve["fit"]["center_uncertainty_hz"] for curve in curves], dtype=float
    )
    linear = _weighted_linear_fit(
        z_values[accepted], centers[accepted], center_uncertainties[accepted]
    )
    calibration_reasons = list(linear["rejection_reasons"])
    if not np.isfinite(linear["r_squared"]) or linear["r_squared"] < linear_r_squared_min:
        calibration_reasons.append(
            f"线性 R^2={linear['r_squared']:.6g} < {linear_r_squared_min:.6g}"
        )
    calibration_success = linear["success"] and not calibration_reasons
    curve_payload = []
    for curve in curves:
        curve_payload.append(
            {
                "z_bias_v": curve["z_bias_v"],
                "predicted_center_hz": curve["predicted_center_hz"],
                "summary_file": curve["summary_file"],
                "discrete_peak_frequency_hz": curve["discrete_peak_frequency_hz"],
                "fit": curve["fit"],
            }
        )
    payload = {
        "success": bool(calibration_success),
        "experiment_id": config.get("experiment_id", "mx-z-field-calibration"),
        "run_dir": str(run_dir),
        "model": "f0_Hz = K_Z_Hz_per_V * Z_bias_V + f_0V_Hz",
        "K_Z_Hz_per_V": linear["slope_hz_per_v"],
        "f_0V_Hz": linear["intercept_hz"],
        "linear_fit": linear,
        "rejection_reasons": calibration_reasons,
        "curves": curve_payload,
        "files": ["frequency_response_fits.png", "z_frequency_calibration.png", "z_frequency_residuals.png", "calibration_results.npz"],
    }
    return payload


def evaluate(run_dir, params, config):
    curves = json.loads((run_dir / "results/curve_fits.json").read_text(encoding="utf-8"))
    payload = evaluate_curves(run_dir, config, curves, params.linear_r_squared_min)
    (run_dir / "results/calibration_evaluation.json").write_text(json.dumps(_builtin(payload), ensure_ascii=False), encoding="utf-8")
    return {key: payload[key] for key in ("success", "K_Z_Hz_per_V", "f_0V_Hz", "rejection_reasons")}


def write_report(run_dir, curves, payload, frequency_label="Y RF frequency (kHz)"):
    from .analysis_plotting import _plot_frequency_responses, _plot_calibration, set_plot_style
    set_plot_style("paper")
    results_dir = Path(run_dir) / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    for curve in curves:
        for key in ("frequency_hz", "r_mean_v", "r_std_v"):
            curve[key] = np.asarray(curve[key], dtype=float)
    linear = payload["linear_fit"]
    calibration_success, calibration_reasons = payload["success"], payload["rejection_reasons"]
    accepted = np.asarray([curve["fit"]["success"] for curve in curves], dtype=bool)
    z_values = np.asarray([curve["z_bias_v"] for curve in curves], dtype=float)
    centers = np.asarray([curve["fit"]["parameters"][2] for curve in curves], dtype=float)
    center_uncertainties = np.asarray(
        [curve["fit"]["center_uncertainty_hz"] for curve in curves], dtype=float
    )
    _plot_frequency_responses(results_dir, curves, frequency_label)
    _plot_calibration(results_dir, curves, linear)
    serializable = _builtin(payload)
    with (results_dir / "analysis.yaml").open("w", encoding="utf-8") as stream:
        yaml.safe_dump(serializable, stream, allow_unicode=True, sort_keys=False)
    with (results_dir / "analysis.json").open("w", encoding="utf-8") as stream:
        json.dump(serializable, stream, ensure_ascii=False, indent=2)
    np.savez(
        results_dir / "calibration_results.npz",
        z_bias_v=z_values,
        predicted_center_hz=np.asarray(
            [curve["predicted_center_hz"] for curve in curves], dtype=float
        ),
        fitted_center_hz=centers,
        center_uncertainty_hz=center_uncertainties,
        hwhm_hz=np.asarray([curve["fit"]["parameters"][1] for curve in curves], dtype=float),
        curve_fit_r_squared=np.asarray([curve["fit"]["r_squared"] for curve in curves], dtype=float),
        curve_accepted=accepted,
        discrete_peak_frequency_hz=np.asarray(
            [curve["discrete_peak_frequency_hz"] for curve in curves], dtype=float
        ),
        K_Z_Hz_per_V=np.float64(linear["slope_hz_per_v"]),
        f_0V_Hz=np.float64(linear["intercept_hz"]),
        linear_covariance=np.asarray(linear["covariance"], dtype=float),
        linear_uncertainties=np.asarray(linear["uncertainties"], dtype=float),
        linear_r_squared=np.float64(linear["r_squared"]),
        linear_residual_hz=np.asarray(linear["residual_hz"], dtype=float),
        calibration_success=np.uint8(calibration_success),
    )
    print(
        f"Z 标定分析完成: success={calibration_success}, "
        f"K={linear['slope_hz_per_v']:.6g} Hz/V, "
        f"f0={linear['intercept_hz']:.6g} Hz"
    )
    if calibration_reasons:
        print("标定质量警告: " + "；".join(calibration_reasons))
    return serializable



def report(run_dir, params, config):
    curves = json.loads((run_dir / "results/curve_fits.json").read_text(encoding="utf-8"))
    payload = json.loads((run_dir / "results/calibration_evaluation.json").read_text(encoding="utf-8"))
    return write_report(run_dir, curves, payload)


def plain(value):
    """会话 JSON 不允许 NaN；原分析文件保留历史 NaN 约定。"""
    import math
    value = _builtin(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
