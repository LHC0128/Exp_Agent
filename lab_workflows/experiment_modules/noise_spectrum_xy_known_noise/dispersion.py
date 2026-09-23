"""色散线形拟合：从整条线形取零交叉点斜率，作为被测侧增益换算基准。

采集工作流只负责步进 z 偏置并读回解调 Y；拟合与质量判定放在本模块，
便于离线回归测试，也避免 workflow 模块级脚本无法导入。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from sensitivity_analysis.fitting import fit_dispersive

# 四参数色散拟合再留出两端自由度，点数下限与其余色散拟合实验保持一致。
MIN_DISPERSION_FIT_POINTS = 7
# 仅当运行配置与原始数据都缺少 K_Z 时才使用的回退（与运行默认值一致）。
DEFAULT_K_Z_HZ_PER_V = 11703.677827729214


def fit_dispersion_curve(
    offsets_v: np.ndarray,
    y_mean: np.ndarray,
    *,
    skip_start_points: int,
    skip_end_points: int,
    k_z_hz_per_v: float,
) -> dict[str, Any]:
    """按色散线形拟合零交叉点斜率，起止两端各剔除指定点数。

    ``dY/dB`` 由 ``|A| / gamma²`` 在零交叉点给出，而不是对中心段做线性拟合。
    调用 ``fit_dispersive`` 时把 Hz 当作该函数的 ``nT/ft`` 单位传入 K_Z，
    因此派生量（斜率、半高宽、中心偏移）直接落在 Hz 域。
    """
    offsets = np.asarray(offsets_v, dtype=float).ravel()
    y = np.asarray(y_mean, dtype=float).ravel()
    if offsets.size != y.size:
        raise ValueError("色散扫描的偏置轴与 Y 数据长度不一致")
    if offsets.size == 0 or not np.isfinite(offsets).all() or not np.isfinite(y).all():
        raise ValueError("色散扫描数据包含非有限值")
    if np.any(np.diff(offsets) <= 0):
        raise ValueError("色散扫描偏置轴必须严格递增")
    if not isinstance(skip_start_points, int) or not isinstance(skip_end_points, int):
        raise ValueError("色散拟合起止剔除点数必须为整数")
    if skip_start_points < 0 or skip_end_points < 0:
        raise ValueError("色散拟合起止剔除点数不能为负")
    if not np.isfinite(k_z_hz_per_v) or k_z_hz_per_v <= 0:
        raise ValueError("K_Z 必须为有限正数，才能把色散斜率换算到 Hz 域")

    fit_mask = np.zeros(offsets.size, bool)
    stop = offsets.size - skip_end_points if skip_end_points else offsets.size
    if skip_start_points < stop:
        fit_mask[skip_start_points:stop] = True
    used_points = int(np.count_nonzero(fit_mask))
    if used_points < MIN_DISPERSION_FIT_POINTS:
        raise ValueError(
            f"色散线形剔除两端后仅剩 {used_points} 点，无法进行线形拟合"
            f"（至少需要 {MIN_DISPERSION_FIT_POINTS} 点）"
        )

    result = fit_dispersive(
        offsets[fit_mask],
        y[fit_mask],
        k_z_hz_per_v,
        k_z_hz_per_v,
    )
    return {
        "fit_mask": fit_mask,
        "popt": np.asarray(result.popt, float),
        "n_points": used_points,
        "slope_v_per_v": float(result.slope_VV),
        "slope_v_per_hz": float(result.slope_VV) / float(k_z_hz_per_v),
        "gamma_v": float(result.gamma_V),
        "gamma_hz": float(result.gamma_V) * float(k_z_hz_per_v),
        "center_v": float(result.V0),
        "center_hz": float(result.V0) * float(k_z_hz_per_v),
        "r_squared": float(result.r_squared),
        "rmse_v": float(result.rmse),
        "is_valid": bool(result.is_valid),
        "rejection_reasons": list(result.rejection_reasons),
    }


def resolve_dispersion(run_dir, config: dict[str, Any]) -> dict[str, Any]:
    """离线重新拟合色散斜率；原始数据缺失时回退到采集记录的斜率。

    与 ``noise_spectrum_xy`` 的脊线重标定同理：增益换算基准由原始数据重算，
    采集阶段写入的斜率只作为对照。剔除点数取运行配置的
    ``DISPERSION_FIT_SKIP_START`` / ``DISPERSION_FIT_SKIP_END``（历史运行缺省为 0）。
    """
    recorded = dict(config.get("dispersion") or {})
    raw_file = run_dir / "raw" / "dispersion_scan.npz"
    if not raw_file.is_file():
        return _recorded_fallback(recorded, "缺少 raw/dispersion_scan.npz")
    with np.load(raw_file, allow_pickle=False) as data:
        if not {"offsets_v", "y_mean"} <= set(data.files):
            return _recorded_fallback(recorded, "原始色散数据缺少 offsets_v/y_mean")
        offsets = np.asarray(data["offsets_v"], float)
        y_mean = np.asarray(data["y_mean"], float)
        k_z = float(data["k_z_hz_per_v"]) if "k_z_hz_per_v" in data.files else float("nan")
    if not np.isfinite(k_z) or k_z <= 0:
        k_z = float(recorded.get("k_z_hz_per_v", DEFAULT_K_Z_HZ_PER_V))
    params = config.get("parameters") or {}
    skip_start = int(params.get("DISPERSION_FIT_SKIP_START", 0))
    skip_end = int(params.get("DISPERSION_FIT_SKIP_END", 0))
    try:
        fit = fit_dispersion_curve(
            offsets, y_mean,
            skip_start_points=skip_start,
            skip_end_points=skip_end,
            k_z_hz_per_v=k_z,
        )
    except ValueError as exc:
        return _recorded_fallback(recorded, f"原始色散数据重拟合失败: {exc}")
    result = dict(fit, source="raw_refit", method="dispersion_line_fit", k_z_hz_per_v=k_z,
                  offsets_v=offsets, y_mean=y_mean,
                  skip_start_points=skip_start, skip_end_points=skip_end)
    recorded_slope = recorded.get("slope_v_per_hz")
    if isinstance(recorded_slope, (int, float)) and np.isfinite(recorded_slope) and recorded_slope > 0:
        result["recorded_slope_v_per_hz"] = float(recorded_slope)
        result["recorded_relative_difference"] = abs(fit["slope_v_per_hz"] - recorded_slope) / recorded_slope
    return result


def _recorded_fallback(recorded: dict[str, Any], reason: str) -> dict[str, Any]:
    """回退到采集记录的斜率；只用于原始数据缺失或不可用的历史目录。"""
    slope_v_per_hz = recorded.get("slope_v_per_hz")
    if not isinstance(slope_v_per_hz, (int, float)) or not np.isfinite(slope_v_per_hz) or slope_v_per_hz == 0:
        raise ValueError(f"无法确定色散斜率: {reason}")
    return {
        "source": "config",
        "method": str(recorded.get("method", "center_linear_fit")),
        "fallback_reason": reason,
        "slope_v_per_v": recorded.get("slope_v_per_v"),
        "slope_v_per_hz": float(slope_v_per_hz),
        "k_z_hz_per_v": recorded.get("k_z_hz_per_v"),
        "fit_mask": None,
        "popt": None,
        "n_points": recorded.get("fit_points"),
        "r_squared": recorded.get("r_squared"),
        "rmse_v": recorded.get("rmse_v"),
        "is_valid": recorded.get("fit_valid"),
        "rejection_reasons": list(recorded.get("fit_rejection_reasons") or []),
        "offsets_v": None,
        "y_mean": None,
        "intercept_v": recorded.get("intercept_v"),
    }
