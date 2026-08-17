"""Mx Y 向 RF 频率响应离线分析。

只读取 run_dir/raw/frequency_response.npz 与实验配置，用统一布洛赫稳态
线形拟合每条幅度曲线，报告弱驱动 HWHM 或强驱动 Rabi 劈裂双峰，并输出
频率响应叠加图与饱和度-幅度平方诊断图。
"""

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
    COLOR_OPTIMAL,
    COLOR_TRAD,
    PAPER_WIDE,
    blues_gradient,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)
from .analysis_core import (
    BlochFitResult,
    bloch_response,
    fit_bloch_response,
    fit_saturation_scaling,
)
from .models import MxYRFFrequencyResponseParams

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))


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
    params_type: type[MxYRFFrequencyResponseParams] = MxYRFFrequencyResponseParams,
) -> tuple[MxYRFFrequencyResponseParams, dict[str, Any]]:
    config_path = run_dir / "experiment_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"未找到实验配置: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    params = params_type.from_external(
        config.get("parameters", {}),
        schema_version=int(config.get("schema_version", 1)),
    )
    return params, config


def _plot_frequency_response(
    *,
    results_dir: Path,
    frequency_hz: np.ndarray,
    amplitude_vpp: np.ndarray,
    r_mean_v: np.ndarray,
    fit_mask: np.ndarray,
    fits: list[BlochFitResult],
) -> None:
    """叠加绘制各幅度曲线及其布洛赫拟合。"""
    set_plot_style("paper")
    fig, ax = new_figure(figsize=PAPER_WIDE)
    colors = blues_gradient(int(amplitude_vpp.size))
    dense_frequency = np.linspace(
        float(np.nanmin(frequency_hz)),
        float(np.nanmax(frequency_hz)),
        1000,
    )
    for index, (amplitude, fit) in enumerate(zip(amplitude_vpp, fits)):
        mask = fit_mask[index]
        color = colors[index] if amplitude_vpp.size > 1 else COLOR_OPTIMAL
        label = f"{amplitude:.3g} Vpp"
        ax.plot(
            frequency_hz[mask],
            r_mean_v[index][mask],
            "o",
            color=color,
            markersize=3,
            label=label if amplitude_vpp.size > 1 else None,
        )
        if fit is not None and np.all(np.isfinite(fit.parameters)):
            ax.plot(
                dense_frequency,
                bloch_response(dense_frequency, *fit.parameters),
                "-",
                color=color,
                lw=1.2,
            )
    if amplitude_vpp.size == 1 and fits[0] is not None:
        fit = fits[0]
        summary = (
            f"f$_0$={fit.center:.0f} Hz, $\\gamma$={fit.gamma:.0f} Hz, "
            f"S={fit.saturation:.2f}"
            + (" (split)" if fit.split else "")
        )
        ax.text(
            0.03,
            0.95,
            summary,
            transform=ax.transAxes,
            va="top",
            fontsize=8,
        )
    format_axis(
        ax,
        xlabel="Y RF frequency (Hz)",
        ylabel="Demod R (V)",
    )
    if amplitude_vpp.size > 1:
        style_legend(ax, fontsize=7, ncol=2)
    save_figure(fig, results_dir / "frequency_response.png")


def _plot_saturation_scaling(
    *,
    results_dir: Path,
    amplitude_vpp: np.ndarray,
    saturation: np.ndarray,
    scaling: dict[str, Any],
) -> None:
    """绘制 S 与幅度平方的线性诊断图。"""
    set_plot_style("paper")
    fig, ax = new_figure()
    squared = amplitude_vpp**2
    ax.plot(
        squared,
        saturation,
        "o",
        color=COLOR_OPTIMAL,
        label="S vs amplitude$^2$",
    )
    if scaling["success"]:
        slope = scaling["slope_per_vpp2"]
        intercept = scaling["intercept"]
        dense = np.linspace(float(np.min(squared)), float(np.max(squared)), 100)
        ax.plot(
            dense,
            slope * dense + intercept,
            "--",
            color=COLOR_TRAD,
            label=(
                "Linear fit "
                f"(slope={slope:.3g}, $R^2$={scaling['r_squared']:.3f})"
            ),
        )
        ax.axhline(
            1.0,
            color=COLOR_GRAY,
            ls=":",
            alpha=0.5,
            label="Split threshold S=1",
        )
    format_axis(
        ax,
        xlabel="Amplitude squared (V$_{pp}^2$)",
        ylabel="Saturation S",
    )
    style_legend(ax, fontsize=7.5)
    save_figure(fig, results_dir / "s_vs_amplitude.png")


def analyze(
    run_dir: Path,
    *,
    params_type: type[MxYRFFrequencyResponseParams] = MxYRFFrequencyResponseParams,
) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    params, config = _load_params(run_dir, params_type)

    response_path = raw_dir / "frequency_response.npz"
    if not response_path.exists():
        raise FileNotFoundError(f"缺少频率响应汇总: {response_path}")
    with np.load(response_path) as data:
        frequency_hz = np.asarray(data["frequency_hz"], dtype=float)
        amplitude_vpp = np.asarray(data["amplitude_vpp"], dtype=float)
        r_key = (
            "r_mean_v"
            if "r_mean_v" in data.files
            else "r_scalar_mean_v"
        )
        r_mean = np.asarray(data[r_key], dtype=float)
        r_std = np.asarray(data["r_std_v"], dtype=float)
    if r_mean.ndim == 1:
        r_mean = r_mean[np.newaxis, :]
    if r_std.ndim == 1:
        r_std = r_std[np.newaxis, :]
    if amplitude_vpp.ndim == 1 and amplitude_vpp.size != r_mean.shape[0]:
        raise ValueError("幅度轴与响应矩阵行数不一致")

    bad_point_mask = (
        ~np.isfinite(r_mean)
        | ~np.isfinite(r_std)
        | (r_std > params.r_bad_point_std_threshold_v)
    )
    fit_mask = ~bad_point_mask
    fits: list[BlochFitResult | None] = []
    for index in range(int(amplitude_vpp.size)):
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
    if not valid_fits:
        payload = {
            "success": False,
            "reason": "没有任何幅度曲线通过布洛赫线形拟合质量门槛",
        }
        (results_dir / "analysis.yaml").write_text(
            yaml.safe_dump(_builtin(payload), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        raise RuntimeError(payload["reason"])

    fit_parameters = np.full(
        (int(amplitude_vpp.size), 5),
        np.nan,
        dtype=float,
    )
    fit_uncertainties = np.full(
        (int(amplitude_vpp.size), 5),
        np.nan,
        dtype=float,
    )
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

    _plot_frequency_response(
        results_dir=results_dir,
        frequency_hz=frequency_hz,
        amplitude_vpp=amplitude_vpp,
        r_mean_v=r_mean,
        fit_mask=fit_mask,
        fits=fits,
    )
    if int(amplitude_vpp.size) >= 2:
        _plot_saturation_scaling(
            results_dir=results_dir,
            amplitude_vpp=amplitude_vpp[fit_success],
            saturation=saturation[fit_success],
            scaling=scaling,
        )

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

    result = {
        "success": True,
        "experiment_id": config.get(
            "experiment_id", "mx-y-rf-frequency-response"
        ),
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
            *(
                ["s_vs_amplitude.png"]
                if int(amplitude_vpp.size) >= 2
                else []
            ),
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
    print(f"Mx Y RF 频率响应分析完成: {result['run_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
