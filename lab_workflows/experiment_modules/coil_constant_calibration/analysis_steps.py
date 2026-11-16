"""电流常数标定的输入资格、逐曲线拟合、过零标定与报告导出；不连接仪器，不改写 raw。"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.optimize import least_squares

from ...experiment_runtime import check_cancelled
from ...workflow_state import write_state
from ..mx_y_rf_sensitivity.analysis_core import fit_lorentzian_response
from . import run_format
from .stages import ANALYSIS_KEYS


class AnalysisQualityError(ValueError):
    """输入或拟合不满足分析条件；诊断仍保存。"""


def output_path(run_dir, name):
    root = Path(run_dir).resolve()
    path = (root / name).resolve()
    if path != root and root not in path.parents:
        raise ValueError("输入或产物路径越出运行目录")
    return path


def load_config(run_dir):
    config = run_format.read_config(output_path(run_dir, "experiment_config.yaml"))
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    return config, run_format.parameters(config)


def resolve_options(base, options):
    if unknown := options.keys() - set(ANALYSIS_KEYS):
        raise ValueError("不支持的分析覆盖值：" + "、".join(sorted(unknown)))
    merged = base.to_external() | dict(options)
    params = type(base).from_external(merged)
    if not 0.0 <= params.linear_r_squared_min <= 1.0:
        raise ValueError("LINEAR_R_SQUARED_MIN 必须在 0～1 之间")
    if params.gyromagnetic_ratio_hz_per_nt <= 0 or not math.isfinite(params.gyromagnetic_ratio_hz_per_nt):
        raise ValueError("GYROMAGNETIC_RATIO_HZ_PER_NT 必须为有限正数")
    if params.min_accepted_points < 2:
        raise ValueError("MIN_ACCEPTED_POINTS 至少为 2")
    if params.min_branch_points < 1:
        raise ValueError("MIN_BRANCH_POINTS 至少为 1")
    if not 0 < params.branch_slope_tolerance <= 0.5:
        raise ValueError("BRANCH_SLOPE_TOLERANCE 必须在 (0, 0.5] 内")
    if params.near_zero_fit_floor_hz < 0 or not math.isfinite(params.near_zero_fit_floor_hz):
        raise ValueError("NEAR_ZERO_FIT_FLOOR_HZ 必须是有限非负数")
    return params


def signature(path):
    if not path.is_file():
        return None
    return [path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()]


def fingerprint(run_dir):
    root = Path(run_dir)
    raw = output_path(root, "raw")
    paths = [output_path(root, "experiment_config.yaml")]
    if raw.is_dir():
        paths.extend(sorted(raw.rglob("*.npz")))
    return {path.relative_to(root).as_posix(): signature(path) for path in paths}


def plain(value):
    """JSON 与会话使用 null，科学计算与 NPZ 继续保留 NaN。"""
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


# ---- B01 输入资格 ----
def _load_scan_entry(run_dir, path):
    with np.load(path, allow_pickle=False) as data:
        required = {"frequency_hz", "r_mean_v", "r_std_v", "current_ma", "current_a", "drive_v",
                    "scan_direction", "predicted_center_hz", "near_zero_skipped"}
        if missing := required - set(data.files):
            raise AnalysisQualityError(f"{path.name} 缺少字段：" + "、".join(sorted(missing)))
        frequency = np.asarray(data["frequency_hz"], dtype=float).reshape(-1)
        r_mean = np.asarray(data["r_mean_v"], dtype=float).reshape(-1)
        r_std = np.asarray(data["r_std_v"], dtype=float).reshape(-1)
        entry = dict(
            summary_file=str(path.relative_to(Path(run_dir).resolve())).replace("\\", "/"),
            frequency_hz=frequency, r_mean_v=r_mean, r_std_v=r_std,
            current_ma=float(np.asarray(data["current_ma"]).reshape(())),
            current_a=float(np.asarray(data["current_a"]).reshape(())),
            drive_v=float(np.asarray(data["drive_v"]).reshape(())),
            scan_direction=str(np.asarray(data["scan_direction"]).reshape(())),
            predicted_center_hz=float(np.asarray(data["predicted_center_hz"]).reshape(())),
            near_zero_skipped=bool(np.asarray(data["near_zero_skipped"]).reshape(())),
        )
    if entry["near_zero_skipped"] or frequency.size == 0:
        entry["scan_empty"] = bool(frequency.size == 0 and not entry["near_zero_skipped"])
        return entry
    if frequency.size < 5 or not (frequency.size == r_mean.size == r_std.size):
        raise AnalysisQualityError(f"{path.name} 频扫数组长度不一致或点太少")
    if not (np.isfinite(frequency).all() and np.isfinite(r_mean).all() and np.isfinite(r_std).all()):
        raise AnalysisQualityError(f"{path.name} 频扫包含非有限值")
    if np.any(np.diff(frequency) <= 0):
        raise AnalysisQualityError(f"{path.name} 频率轴不是严格递增")
    return entry


def load_inputs(run_dir, config, params):
    if reason := run_format.analysis_block_reason(config):
        raise AnalysisQualityError(reason)
    index_path = output_path(run_dir, "raw/current_scan_index.npz")
    if not index_path.is_file():
        raise AnalysisQualityError("缺少 raw/current_scan_index.npz（A09 主扫描未完成）")
    with np.load(index_path, allow_pickle=False) as index:
        required = {"summary_file", "target_current_ma", "current_ma", "current_a", "drive_v",
                    "scan_direction", "predicted_center_hz", "near_zero_skipped"}
        if missing := required - set(index.files):
            raise AnalysisQualityError("扫描索引缺少字段：" + "、".join(sorted(missing)))
        summary_files = [str(item) for item in index["summary_file"].tolist()]
    entries = []
    seen: set[Path] = set()
    for name in summary_files:
        check_cancelled()
        path = output_path(run_dir, name)
        if not path.is_file() or path in seen:
            raise AnalysisQualityError(f"扫描索引引用了无效或重复的原始数据路径：{name}")
        seen.add(path)
        entries.append(_load_scan_entry(run_dir, path))
    if not entries:
        raise AnalysisQualityError("扫描索引为空")
    dc_check = config.get("dc_check")
    if not isinstance(dc_check, dict) or "k_a_per_v" not in dc_check:
        raise AnalysisQualityError("运行配置缺少 A04 直流 I–V 核对结果")
    with np.load(index_path, allow_pickle=False) as index:
        anchor = float(np.asarray(index["anchor_center_hz"]).reshape(())) if "anchor_center_hz" in index.files \
            else float(params.main_field_center_hz)
    return dict(entries=entries, dc_check=dc_check, anchor_center_hz=anchor,
                geometry=config.get("geometry") if isinstance(config.get("geometry"), dict) else {},
                main_field=config.get("main_field") if isinstance(config.get("main_field"), dict) else {})


# ---- B02 逐曲线拟合 ----
def _analyze_curve(entry, params):
    base = dict(index=None, summary_file=entry["summary_file"], current_ma=entry["current_ma"],
                current_a=entry["current_a"], drive_v=entry["drive_v"],
                scan_direction=entry["scan_direction"], predicted_center_hz=entry["predicted_center_hz"],
                near_zero_skipped=entry["near_zero_skipped"],
                _frequency_hz=entry["frequency_hz"].tolist(), _r_mean_v=entry["r_mean_v"].tolist())
    if entry["near_zero_skipped"] or entry.get("scan_empty"):
        reason = "near_zero_skipped" if entry["near_zero_skipped"] else "粗扫未找到峰，本点未采频扫"
        base["fit"] = dict(success=False, parameters=[float("nan")] * 4, uncertainties=[float("nan")] * 4,
                           r_squared=float("nan"), rmse=float("nan"), center_uncertainty_hz=float("nan"),
                           rejection_reasons=[reason])
        base["center_hz"] = float("nan")
        base["gamma_hz"] = float("nan")
        return base
    result = fit_lorentzian_response(entry["frequency_hz"], entry["r_mean_v"],
                                     r_squared_min=None, relative_gamma_uncertainty_max=None)
    reasons = list(result.rejection_reasons)
    center = float(result.parameters[2]) if np.isfinite(result.parameters[2]) else float("nan")
    gamma = abs(float(result.parameters[1])) if np.isfinite(result.parameters[1]) else float("nan")
    amplitude = abs(float(result.parameters[0])) if np.isfinite(result.parameters[0]) else float("nan")
    noise = float(np.median(entry["r_std_v"])) if entry["r_std_v"].size else float("nan")
    if np.isfinite(amplitude) and np.isfinite(noise) and noise > 0 and amplitude < 5.0 * noise:
        reasons.append(f"拟合幅度 {amplitude:.4g} V 不高于 5 倍噪声 {noise:.4g} V")
    step = float(np.median(np.diff(entry["frequency_hz"])))
    if np.isfinite(center) and abs(center) < params.near_zero_fit_floor_hz:
        reasons.append(f"中心 |{center:.6g}| Hz 低于零场拟合下限 {params.near_zero_fit_floor_hz:g} Hz")
    if np.isfinite(center) and np.isfinite(gamma) and gamma > 0:
        edge_distance = min(center - float(entry["frequency_hz"][0]),
                            float(entry["frequency_hz"][-1]) - center)
        if edge_distance < gamma:
            reasons.append("中心距扫描窗口边小于一个 HWHM")
    if np.isfinite(center) and abs(center - entry["predicted_center_hz"]) > 0.5 * params.frequency_half_width_hz:
        reasons.append("中心与预测相差超过半个扫频窗，可能窗口错位")
    base["fit"] = dict(success=bool(result.success and not reasons),
                       parameters=[float(value) for value in result.parameters],
                       uncertainties=[float(value) for value in result.uncertainties],
                       r_squared=float(result.r_squared), rmse=float(result.rmse),
                       center_uncertainty_hz=float(result.uncertainties[2]),
                       model="C + A*gamma/sqrt((f-f0)^2+gamma^2)",
                       gamma_definition="power_hwhm_and_amplitude_3db_half_width",
                       rejection_reasons=reasons)
    base["center_hz"] = center
    base["gamma_hz"] = gamma
    base["frequency_step_hz"] = step
    return base


def fit_curves(run_dir, params, config):
    inputs = load_inputs(run_dir, config, params)
    curves = []
    try:
        for position, entry in enumerate(inputs["entries"]):
            check_cancelled()
            curve = _analyze_curve(entry, params)
            curve["index"] = position
            curves.append(curve)
    except BaseException:
        if curves:
            try:
                write_state(output_path(run_dir, "results/curve_fits.json"), plain(curves))
            except Exception:
                pass
        raise
    write_state(output_path(run_dir, "results/curve_fits.json"), plain(curves))
    return dict(total_curve_count=len(curves),
                accepted_curve_count=sum(curve["fit"]["success"] for curve in curves),
                near_zero_skipped_count=sum(curve["near_zero_skipped"] for curve in curves))


def read_curves(run_dir):
    curves = json.loads(output_path(run_dir, "results/curve_fits.json").read_text(encoding="utf-8"))
    for curve in curves:
        for key in ("center_hz", "gamma_hz", "frequency_step_hz"):
            curve[key] = float("nan") if curve.get(key) is None else float(curve[key])
        for key in ("_frequency_hz", "_r_mean_v"):
            curve[key] = np.asarray(curve.get(key) or [], dtype=float)
        curve["current_ma"] = float(curve["current_ma"])
        curve["drive_v"] = float(curve["drive_v"])
    return curves


# ---- B03 标定 ----
def _weighted_linear_fit(x, y, sigma):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y, sigma = x[valid], y[valid], sigma[valid]
    if x.size < 2:
        return dict(success=False, slope=float("nan"), intercept=float("nan"),
                    slope_uncertainty=float("nan"), intercept_uncertainty=float("nan"),
                    covariance=np.full((2, 2), np.nan), r_squared=float("nan"),
                    residual=[float("nan")] * int(x.size), rejection_reasons=["有效点不足 2 个"])
    finite_positive = np.isfinite(sigma) & (sigma > 0)
    fallback = float(np.median(sigma[finite_positive])) if np.any(finite_positive) else 1.0
    sigma = np.where(finite_positive, sigma, fallback)
    design = np.column_stack([x, np.ones_like(x)])
    weights = 1.0 / sigma**2
    normal = design.T @ (weights[:, None] * design)
    try:
        inverse = np.linalg.inv(normal)
    except np.linalg.LinAlgError:
        return dict(success=False, slope=float("nan"), intercept=float("nan"),
                    slope_uncertainty=float("nan"), intercept_uncertainty=float("nan"),
                    covariance=np.full((2, 2), np.nan), r_squared=float("nan"),
                    residual=[float("nan")] * int(x.size), rejection_reasons=["线性拟合法方程奇异"])
    beta = inverse @ (design.T @ (weights * y))
    predicted = design @ beta
    residual = y - predicted
    dof = max(1, x.size - 2)
    reduced = float(np.sum((residual / sigma) ** 2) / dof)
    covariance = inverse * reduced
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-30 else 0.0
    return dict(success=True, slope=float(beta[0]), intercept=float(beta[1]),
                slope_uncertainty=float(np.sqrt(max(covariance[0, 0], 0.0))),
                intercept_uncertainty=float(np.sqrt(max(covariance[1, 1], 0.0))),
                covariance=covariance, r_squared=float(r_squared),
                residual=residual, rejection_reasons=[])


def _absolute_model(parameters, x):
    slope, crossing = parameters
    return np.abs(slope * (x - crossing))


def _fit_absolute(x, y, sigma, initial):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y, sigma = x[valid], y[valid], sigma[valid]
    if x.size < 3:
        return dict(success=False, slope=float("nan"), crossing=float("nan"),
                    slope_uncertainty=float("nan"), crossing_uncertainty=float("nan"),
                    r_squared=float("nan"), residual=[float("nan")] * int(x.size),
                    rejection_reasons=["有效点不足 3 个"])
    finite_positive = np.isfinite(sigma) & (sigma > 0)
    fallback = float(np.median(sigma[finite_positive])) if np.any(finite_positive) else 1.0
    sigma = np.where(finite_positive, sigma, fallback)
    span = max(float(np.ptp(x)), 1.0)
    lower = [1e-9, float(np.min(x) - 5.0 * span)]
    upper = [np.inf, float(np.max(x) + 5.0 * span)]
    guesses: list[tuple[float, float]] = [(max(float(initial[0]), 1e-6), float(initial[1]))]
    if x.size >= 2:
        slope, intercept = np.polyfit(x, y, 1)
        if abs(slope) > 1e-9 and np.isfinite(intercept):
            guesses.append((abs(float(slope)), float(-intercept / slope)))
    for crossing in (float(np.min(x)), float(np.max(x)), float(np.min(x)) - span, float(np.max(x)) + span):
        guesses.append((max(abs(float(initial[0])), 1e-6), crossing))

    def residual(parameters):
        return (_absolute_model(parameters, x) - y) / sigma

    best = None
    for slope_guess, crossing_guess in guesses:
        start = [max(slope_guess, 1e-6),
                 min(max(crossing_guess, lower[1] + 1e-9), upper[1] - 1e-9)]
        try:
            solution = least_squares(residual, x0=start, bounds=(lower, upper), max_nfev=20000)
        except Exception as exc:  # 数值失败保留诊断
            if best is None:
                best = exc
            continue
        if best is None or isinstance(best, Exception) or solution.cost < best.cost:
            best = solution
    if best is None or isinstance(best, Exception):
        return dict(success=False, slope=float("nan"), crossing=float("nan"),
                    slope_uncertainty=float("nan"), crossing_uncertainty=float("nan"),
                    r_squared=float("nan"), residual=[float("nan")] * int(x.size),
                    rejection_reasons=[f"拟合异常: {best}"])
    solution = best
    slope, crossing = (float(solution.x[0]), float(solution.x[1]))
    predicted = _absolute_model(solution.x, x)
    ss_res = float(np.sum((y - predicted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-30 else 0.0
    dof = max(1, x.size - 2)
    try:
        covariance = np.linalg.inv(solution.jac.T @ solution.jac) * (2.0 * solution.cost / dof)
        uncertainties = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    except np.linalg.LinAlgError:
        uncertainties = np.full(2, np.nan)
    return dict(success=bool(solution.success), slope=slope, crossing=crossing,
                slope_uncertainty=float(uncertainties[0]),
                crossing_uncertainty=float(uncertainties[1]),
                r_squared=float(r_squared), residual=(y - predicted).tolist(),
                rejection_reasons=[] if solution.success else [str(solution.message)])


def evaluate(run_dir, params, config):
    curves = read_curves(run_dir)
    inputs = load_inputs(run_dir, config, params)
    accepted = np.asarray([curve["fit"]["success"] for curve in curves], dtype=bool)
    current = np.asarray([curve["current_ma"] for curve in curves], dtype=float)
    centers = np.asarray([curve["center_hz"] for curve in curves], dtype=float)
    sigmas = np.asarray([curve["fit"]["center_uncertainty_hz"] for curve in curves], dtype=float)
    drive = np.asarray([curve["drive_v"] for curve in curves], dtype=float)
    directions = np.asarray([curve["scan_direction"] for curve in curves], dtype=str)
    selected = accepted & np.isfinite(current) & np.isfinite(centers) & np.isfinite(sigmas)
    if np.count_nonzero(selected) < params.min_accepted_points:
        raise AnalysisQualityError(
            f"有效曲线 {int(np.count_nonzero(selected))} 条，少于 MIN_ACCEPTED_POINTS={params.min_accepted_points}")
    current, centers, sigmas = current[selected], centers[selected], sigmas[selected]
    drive, directions = drive[selected], directions[selected]
    anchor_hz = float(inputs["anchor_center_hz"])
    initial_slope = float(params.initial_hz_per_ma)
    initial_crossing = float(-params.polarity_sign * anchor_hz / initial_slope)
    global_fit = _fit_absolute(current, centers, sigmas, (initial_slope, initial_crossing))
    crossing = float(global_fit["crossing"])
    inside = bool(np.min(current) < crossing < np.max(current))
    order = np.argsort(current)
    step = float(np.median(np.diff(current[order]))) if current.size > 1 else float("nan")
    geometry = dict(crossing_inside=inside, linear_case=not inside,
                    current_span_ma=float(np.ptp(current)),
                    current_min_ma=float(np.min(current)), current_max_ma=float(np.max(current)),
                    crossing_current_ma=crossing,
                    crossing_bracketed_by_data=bool(
                        np.min(current) - 0.5 * step <= crossing <= np.max(current) + 0.5 * step)
                    if np.isfinite(step) else False,
                    branch_counts={
                        "below": int(np.count_nonzero(current < crossing)),
                        "above": int(np.count_nonzero(current > crossing))})
    below = current < crossing
    above = current > crossing
    branch_fits: dict[str, Any] = {}
    consistency = None
    if inside:
        for name, mask in (("below", below), ("above", above)):
            branch_fits[name] = _weighted_linear_fit(current[mask], centers[mask], sigmas[mask])
        if branch_fits["below"].get("success") and branch_fits["above"].get("success"):
            slope_below = float(branch_fits["below"]["slope"])
            slope_above = float(branch_fits["above"]["slope"])
            scale = max(abs(slope_below), abs(slope_above), 1e-15)
            consistency = abs(slope_below + slope_above) / scale
            branch_fits["slope_relative_difference"] = consistency
            denominator = slope_below - slope_above
            branch_fits["intersection_current_ma"] = (
                float((branch_fits["above"]["intercept"] - branch_fits["below"]["intercept"]) / denominator)
                if abs(denominator) > 1e-15 else float("nan"))
    if inside:
        voltage_fit = _weighted_linear_fit(drive[above], centers[above], sigmas[above])
        voltage_fit_below = _weighted_linear_fit(drive[below], centers[below], sigmas[below])
    else:
        voltage_fit = _weighted_linear_fit(drive, centers, sigmas)
        voltage_fit_below = None
    gamma = params.gyromagnetic_ratio_hz_per_nt
    k_nT_per_mA = float(global_fit["slope"]) / gamma
    s_f = float(voltage_fit["slope"])
    k_dc = float(inputs["dc_check"]["k_a_per_v"])
    closure_ratio = s_f / (1000.0 * float(global_fit["slope"]) * k_dc) if k_dc else float("nan")

    hysteresis: dict[str, Any] = {"available": False, "reason": "未做正反向扫描"}
    if params.bidirectional_scan and np.any(directions == "forward") and np.any(directions == "reverse"):
        forward = _fit_absolute(current[directions == "forward"], centers[directions == "forward"],
                                sigmas[directions == "forward"], (initial_slope, initial_crossing))
        reverse = _fit_absolute(current[directions == "reverse"], centers[directions == "reverse"],
                                sigmas[directions == "reverse"], (initial_slope, initial_crossing))
        if forward["success"] and reverse["success"]:
            scale = max(abs(forward["slope"]), abs(reverse["slope"]), 1e-15)
            hysteresis = dict(available=True, forward=forward, reverse=reverse,
                              slope_relative_difference=abs(forward["slope"] - reverse["slope"]) / scale,
                              crossing_difference_ma=abs(forward["crossing"] - reverse["crossing"]))
    reasons = list(global_fit["rejection_reasons"])
    if inside and (geometry["branch_counts"]["below"] < params.min_branch_points
                   or geometry["branch_counts"]["above"] < params.min_branch_points):
        reasons.append(f"过零两侧点数 {geometry['branch_counts']} 少于 MIN_BRANCH_POINTS="
                       f"{params.min_branch_points}；建议平移电流轴或抵消零电流场")
    if not np.isfinite(global_fit["r_squared"]) or global_fit["r_squared"] < params.linear_r_squared_min:
        reasons.append(f"R²={global_fit['r_squared']:.6g} < LINEAR_R_SQUARED_MIN={params.linear_r_squared_min:.6g}")
    if consistency is not None and consistency > params.branch_slope_tolerance:
        reasons.append(f"两支斜率一致性 {consistency:.4g} 超过容许 {params.branch_slope_tolerance:.4g}")
    if hysteresis.get("available") and hysteresis["slope_relative_difference"] > params.branch_slope_tolerance:
        reasons.append(f"正反向斜率差 {hysteresis['slope_relative_difference']:.4g} 超过容许 "
                       f"{params.branch_slope_tolerance:.4g}")
    for curve in curves:
        if not curve["fit"]["success"] and curve["near_zero_skipped"]:
            continue
        if not curve["fit"]["success"]:
            reasons.append(f"{curve['summary_file']}: " + "；".join(curve["fit"]["rejection_reasons"]))
    success = not reasons

    if inside and branch_fits.get("above", {}).get("success"):
        signed_slope_hz_per_ma = float(branch_fits["above"]["slope"])
    elif inside and branch_fits.get("below", {}).get("success"):
        signed_slope_hz_per_ma = -float(branch_fits["below"]["slope"])
    elif current.size >= 2:
        signed_slope_hz_per_ma = float(np.polyfit(current, centers, 1)[0])
    else:
        signed_slope_hz_per_ma = float("nan")
    b1_nt = 1.0 / (2.0 * params.pulse_width_s) / gamma
    i_pi_ma = b1_nt / k_nT_per_mA if k_nT_per_mA > 0 else float("nan")
    i_required = params.pulse_field_margin * i_pi_ma
    payload = plain({
        "success": bool(success),
        "experiment_id": run_format.EXPERIMENT_ID,
        "run_dir": str(run_dir),
        "model": "f_Hz = |K_Hz_per_mA * (I_ma - I0_ma)|",
        "K_Hz_per_mA": float(global_fit["slope"]),
        "K_uncertainty_Hz_per_mA": float(global_fit["slope_uncertainty"]),
        "K_B_nT_per_mA": k_nT_per_mA,
        "K_B_uncertainty_nT_per_mA": float(global_fit["slope_uncertainty"]) / gamma,
        "I0_ma": crossing,
        "I0_uncertainty_ma": float(global_fit["crossing_uncertainty"]),
        "B_res_nT": abs(k_nT_per_mA * crossing),
        "anchor_center_hz": anchor_hz,
        "K_signed_Hz_per_mA": signed_slope_hz_per_ma,
        "K_signed_nT_per_mA": signed_slope_hz_per_ma / gamma,
        "linearity_current": global_fit,
        "linearity_voltage": dict(voltage_fit,
                                  S_B_nT_per_V=float(voltage_fit["slope"]) / gamma,
                                  branch="above" if inside else "all",
                                  below_branch=(dict(voltage_fit_below,
                                                     S_f_Hz_per_V=float(voltage_fit_below["slope"]))
                                                if voltage_fit_below else None)),
        "S_f_Hz_per_V": s_f,
        "S_B_nT_per_V": s_f / gamma,
        "closure": dict(k_dc_a_per_v=k_dc, ratio=closure_ratio,
                        definition="S_f / (1000 * K_Hz_per_mA * k_dc)"),
        "geometry": geometry,
        "branch_fits": branch_fits,
        "hysteresis": hysteresis,
        "dc_check": inputs["dc_check"],
        "operating_point": dict(
            main_field=inputs["main_field"], gain_db=params.amplifier_gain_db,
            loop_resistance_ohm=params.loop_resistance_ohm,
            temperature_c=params.temperature_c, pump_laser_power_v=params.pump_laser_power_v,
            probe_laser_power_v=params.probe_laser_power_v, y_rf_amplitude_vpp=params.y_rf_amplitude_vpp,
            sense_resistor_ohm=params.sense_resistor_ohm,
            gyromagnetic_ratio_hz_per_nt=gamma),
        "pulse_design": dict(
            pulse_width_s=params.pulse_width_s, rabi_frequency_hz=1.0 / (2.0 * params.pulse_width_s),
            b1_nt=b1_nt, i_pi_ma=i_pi_ma, i_required_ma=i_required,
            current_covered=bool(params.current_start_ma <= i_required <= params.current_stop_ma),
            rwa_margin=abs(anchor_hz) * params.pulse_width_s,
            duty=params.pulse_width_s * params.pulse_repetition_hz),
        "accepted_curve_count": int(np.count_nonzero(selected)),
        "total_curve_count": len(curves),
        "rejection_reasons": reasons,
        "curves": [{key: curve[key] for key in ("index", "summary_file", "current_ma", "current_a", "drive_v",
                                                "scan_direction", "predicted_center_hz", "near_zero_skipped",
                                                "center_hz", "gamma_hz", "fit")}
                   for curve in curves],
        "files": ["frequency_response_fits.png", "coil_constant_calibration.png",
                  "coil_voltage_response.png", "coil_constant_residuals.png", "dc_iv_check.png",
                  "calibration_results.npz"],
    })
    write_state(output_path(run_dir, "results/calibration_evaluation.json"), payload)
    return {key: payload[key] for key in ("success", "K_Hz_per_mA", "K_B_nT_per_mA", "I0_ma",
                                          "rejection_reasons", "accepted_curve_count")}


# ---- B04 报告 ----
def report(run_dir, params, config):
    from . import analysis_plotting
    results = output_path(run_dir, "results")
    curves = read_curves(run_dir)
    payload = json.loads((results / "calibration_evaluation.json").read_text(encoding="utf-8"))
    analysis_plotting.plot_frequency_responses(results, curves)
    analysis_plotting.plot_calibration(results, curves, payload)
    analysis_plotting.plot_voltage_response(results, curves, payload)
    analysis_plotting.plot_residuals(results, curves, payload)
    analysis_plotting.plot_dc_check(results, payload.get("dc_check") or {})
    (results / "analysis.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    write_state(results / "analysis.json", payload)
    accepted = np.asarray([curve["fit"]["success"] for curve in curves], dtype=bool)
    np.savez(results / "calibration_results.npz",
             current_ma=np.asarray([curve["current_ma"] for curve in curves], dtype=float),
             current_a=np.asarray([curve["current_a"] for curve in curves], dtype=float),
             drive_v=np.asarray([curve["drive_v"] for curve in curves], dtype=float),
             fitted_center_hz=np.asarray([curve["center_hz"] for curve in curves], dtype=float),
             center_uncertainty_hz=np.asarray([curve["fit"]["center_uncertainty_hz"] for curve in curves],
                                              dtype=float),
             accepted=accepted,
             K_Hz_per_mA=np.float64(payload["K_Hz_per_mA"]),
             K_B_nT_per_mA=np.float64(payload["K_B_nT_per_mA"]),
             I0_ma=np.float64(payload["I0_ma"]),
             linear_r_squared=np.float64(payload["linearity_current"]["r_squared"]),
             S_f_Hz_per_V=np.float64(payload["S_f_Hz_per_V"]))
    print(f"Z 线圈常数标定分析完成: success={payload['success']}, "
          f"K={payload['K_Hz_per_mA']:.6g} Hz/mA ({payload['K_B_nT_per_mA']:.6g} nT/mA), "
          f"I0={payload['I0_ma']:.6g} mA, R²={payload['linearity_current']['r_squared']:.6g}")
    if payload["rejection_reasons"]:
        print("标定质量警告: " + "；".join(payload["rejection_reasons"]))
    return {key: payload[key] for key in ("success", "K_Hz_per_mA", "K_B_nT_per_mA", "I0_ma")}
