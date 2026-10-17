"""分阶段分析的输入校验、布洛赫拟合与结果报告；数值算法与完整分析共用。"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...experiment_runtime import check_cancelled
from . import run_format
from .analysis_core import fit_bloch_response, fit_saturation_scaling
from .models import MxYRFFrequencyResponseParams


class AnalysisQualityError(ValueError):
    """没有任何幅度曲线通过布洛赫线形质量门槛，诊断已经保留。"""

    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def plain(value):
    """会话 JSON 不允许 NaN；科学数组在数值缓存中原样保存。"""
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [plain(item) for item in value]
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


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


def load_params(run_dir, params_type=MxYRFFrequencyResponseParams):
    config_path = Path(run_dir) / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    # 原始格式已独立验证；只读取分析所需的稳定参数键，不以参数版本拒绝同格式数据。
    return params_type.from_external(config["parameters"]), config


def load_inputs(run_dir, config, params: MxYRFFrequencyResponseParams) -> dict[str, Any]:
    """B01：校验运行身份、原始格式与 frequency_response.npz 必需字段。"""
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    response_path = Path(run_dir) / "raw/frequency_response.npz"
    if not response_path.exists():
        raise FileNotFoundError(f"缺少频率响应汇总: {response_path}")
    with np.load(response_path, allow_pickle=False) as data:
        required = ("frequency_hz", "amplitude_vpp", "r_std_v")
        if missing := set(required) - set(data.files):
            raise ValueError(f"frequency_response.npz 缺少字段：" + "、".join(sorted(missing)))
        if not {"r_mean_v", "r_scalar_mean_v"}.intersection(data.files):
            raise ValueError("频率响应汇总缺少 R 均值")
        frequency_hz = np.asarray(data["frequency_hz"], dtype=float)
        amplitude_vpp = np.asarray(data["amplitude_vpp"], dtype=float)
        r_key = "r_mean_v" if "r_mean_v" in data.files else "r_scalar_mean_v"
        r_mean = np.asarray(data[r_key], dtype=float)
        r_std = np.asarray(data["r_std_v"], dtype=float)
    if r_mean.ndim == 1:
        r_mean = r_mean[np.newaxis, :]
    if r_std.ndim == 1:
        r_std = r_std[np.newaxis, :]
    if (frequency_hz.ndim != 1 or frequency_hz.size < 6
            or not np.all(np.isfinite(frequency_hz)) or np.any(frequency_hz <= 0)
            or np.any(np.diff(frequency_hz) <= 0)):
        raise ValueError("频率轴必须包含至少 6 个有限、正值且严格递增的频点")
    if (amplitude_vpp.ndim != 1 or not amplitude_vpp.size
            or not np.all(np.isfinite(amplitude_vpp)) or np.any(amplitude_vpp < 0)
            or np.any(np.diff(amplitude_vpp) <= 0)):
        raise ValueError("幅度轴必须是有限非负且严格递增的一维数组")
    if r_mean.shape != (amplitude_vpp.size, frequency_hz.size) or r_std.shape != r_mean.shape:
        raise ValueError("幅度轴、频率轴与 R 均值/标准差矩阵形状不一致")
    if np.any(r_std[np.isfinite(r_std)] < 0):
        raise ValueError("R 标准差不能是负数")
    valid = np.isfinite(r_mean) & np.isfinite(r_std) & (r_std <= params.r_bad_point_std_threshold_v)
    if not np.any(np.count_nonzero(valid, axis=1) >= 6):
        raise ValueError("没有包含至少 6 个有效频点的幅度曲线")
    return {
        "frequency_hz": frequency_hz,
        "amplitude_vpp": amplitude_vpp,
        "r_mean_v": r_mean,
        "r_std_v": r_std,
    }


def evaluate(run_dir, config, params: MxYRFFrequencyResponseParams) -> dict[str, Any]:
    """B02：逐幅度曲线拟合统一布洛赫线形，保存拟合缓存与诊断。"""
    inputs = load_inputs(run_dir, config, params)
    frequency_hz = inputs["frequency_hz"]
    amplitude_vpp = inputs["amplitude_vpp"]
    r_mean = inputs["r_mean_v"]
    r_std = inputs["r_std_v"]

    bad_point_mask = (
        ~np.isfinite(r_mean)
        | ~np.isfinite(r_std)
        | (r_std > params.r_bad_point_std_threshold_v)
    )
    fit_mask = ~bad_point_mask
    fits = []
    for index in range(int(amplitude_vpp.size)):
        check_cancelled()
        mask = fit_mask[index]
        if np.count_nonzero(mask) < 6:
            fits.append(None)
            continue
        fits.append(
            fit_bloch_response(
                frequency_hz[mask],
                r_mean[index][mask],
                r_squared_min=params.fit_r_squared_min,
                relative_gamma_uncertainty_max=params.fit_relative_gamma_uncertainty_max,
            )
        )

    valid_fits = [fit for fit in fits if fit is not None and fit.success]
    per_amplitude = []
    for index, fit in enumerate(fits):
        entry: dict[str, Any] = {
            "amplitude_vpp": float(amplitude_vpp[index]),
            "excluded_point_count": int(np.count_nonzero(bad_point_mask[index])),
        }
        if fit is not None:
            entry.update(fit.to_dict())
        else:
            entry["success"] = False
            entry["rejection_reasons"] = ["有效点不足 6，未拟合"]
        per_amplitude.append(entry)

    fit_parameters = np.full((int(amplitude_vpp.size), 5), np.nan, dtype=float)
    fit_uncertainties = np.full((int(amplitude_vpp.size), 5), np.nan, dtype=float)
    r_squared = np.full(int(amplitude_vpp.size), np.nan, dtype=float)
    saturation = np.full(int(amplitude_vpp.size), np.nan, dtype=float)
    center_hz = np.full(int(amplitude_vpp.size), np.nan, dtype=float)
    gamma_hz = np.full(int(amplitude_vpp.size), np.nan, dtype=float)
    split_flags = np.zeros(int(amplitude_vpp.size), dtype=bool)
    peak_positions = np.full((int(amplitude_vpp.size), 2), np.nan, dtype=float)
    fit_success = np.zeros(int(amplitude_vpp.size), dtype=bool)
    for index, fit in enumerate(fits):
        if fit is None or not fit.success:
            continue
        fit_success[index] = True
        fit_parameters[index] = fit.parameters
        fit_uncertainties[index] = fit.uncertainties
        r_squared[index] = fit.r_squared
        saturation[index] = fit.saturation
        center_hz[index] = fit.center
        gamma_hz[index] = fit.gamma
        split_flags[index] = fit.split
        peaks = fit.peak_positions_hz
        if peaks is not None:
            peak_positions[index] = peaks

    scaling: dict[str, Any] = {
        "success": False,
        "slope_per_vpp2": float("nan"),
        "intercept": float("nan"),
        "r_squared": float("nan"),
        "n_points": 0,
    }
    if int(amplitude_vpp.size) >= 2:
        scaling = fit_saturation_scaling(
            amplitude_vpp[fit_success],
            saturation[fit_success],
        )

    results_dir = Path(run_dir) / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    np.savez(
        results_dir / "bloch_fit.npz",
        frequency_hz=frequency_hz,
        amplitude_vpp=amplitude_vpp,
        r_mean_v=r_mean,
        r_std_v=r_std,
        bad_point_mask=bad_point_mask,
        fit_success=fit_success,
        fit_parameters=fit_parameters,
        fit_uncertainties=fit_uncertainties,
        r_squared=r_squared,
        saturation=saturation,
        center_hz=center_hz,
        gamma_hz=gamma_hz,
        split=split_flags,
        peak_positions_hz=peak_positions,
    )
    diagnostics = plain({
        "fit_thresholds": {
            "fit_r_squared_min": params.fit_r_squared_min,
            "fit_relative_gamma_uncertainty_max": params.fit_relative_gamma_uncertainty_max,
            "r_bad_point_std_threshold_v": params.r_bad_point_std_threshold_v,
        },
        "valid_curve_count": len(valid_fits),
        "curve_count": int(amplitude_vpp.size),
        "per_amplitude": per_amplitude,
        "saturation_scaling": scaling,
    })
    (results_dir / "fit_diagnostics.yaml").write_text(
        yaml.safe_dump(diagnostics, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    if not valid_fits:
        raise AnalysisQualityError("没有任何幅度曲线通过布洛赫线形拟合质量门槛", diagnostics)
    return diagnostics


def report(run_dir, config, params: MxYRFFrequencyResponseParams) -> dict[str, Any]:
    """B03：从拟合缓存生成响应叠加图、饱和度诊断图与汇总报告。"""
    from .analysis_plotting import _plot_frequency_response, _plot_saturation_scaling

    run_dir = Path(run_dir)
    results_dir = run_dir / "results"
    with np.load(results_dir / "bloch_fit.npz", allow_pickle=False) as data:
        frequency_hz = np.asarray(data["frequency_hz"], dtype=float)
        amplitude_vpp = np.asarray(data["amplitude_vpp"], dtype=float)
        r_mean = np.asarray(data["r_mean_v"], dtype=float)
        bad_point_mask = np.asarray(data["bad_point_mask"], dtype=bool)
        fit_mask = ~bad_point_mask
        fit_parameters = np.asarray(data["fit_parameters"], dtype=float)
    diagnostics = yaml.safe_load(
        (results_dir / "fit_diagnostics.yaml").read_text(encoding="utf-8")
    )
    per_amplitude = diagnostics["per_amplitude"]
    scaling = diagnostics["saturation_scaling"]

    _plot_frequency_response(
        results_dir=results_dir,
        frequency_hz=frequency_hz,
        amplitude_vpp=amplitude_vpp,
        r_mean_v=r_mean,
        fit_mask=fit_mask,
        fit_parameters=fit_parameters,
    )
    files = ["frequency_response.png"]
    if int(amplitude_vpp.size) >= 2:
        _plot_saturation_scaling(
            results_dir=results_dir,
            amplitude_vpp=np.asarray(
                [entry["amplitude_vpp"] for entry in per_amplitude
                 if entry.get("success")], dtype=float
            ),
            saturation=np.asarray(
                [entry["parameters"]["saturation"] for entry in per_amplitude
                 if entry.get("success")], dtype=float
            ),
            scaling=scaling,
        )
        files.append("s_vs_amplitude.png")

    result = {
        "success": True,
        "experiment_id": config.get("experiment_id", "mx-y-rf-frequency-response"),
        "run_dir": str(run_dir),
        "response_signal": "R",
        "model": (
            "R(f) = C + A*sqrt(1+((f-f0)/gamma)^2)"
            " / (1+((f-f0)/gamma)^2+S)"
        ),
        "model_notes": {
            "gamma_hz": "1/(2*pi*T2)，弱驱动极限下为 Lorentzian 型 HWHM",
            "saturation": "S=(gamma_g*B1/2)^2*T1*T2，S>1 时 Rabi 劈裂双峰",
            "split_peak_positions_hz": "f0 ± gamma*sqrt(S-1)",
        },
        "per_amplitude": per_amplitude,
        "saturation_scaling": scaling,
        "plot_profile": "paper",
        "files": [
            "bloch_fit.npz",
            "frequency_response.png",
            *(["s_vs_amplitude.png"] if int(amplitude_vpp.size) >= 2 else []),
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
    return plain(result)
