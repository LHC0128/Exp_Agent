"""Mx Z 最优控制 XY 偏置噪声谱离线分析。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import yaml
from scipy import signal as scipy_signal

from ...experiment_runtime import runtime_run_dir
from ...plotting import (
    COLOR_OPTIMAL,
    COLOR_TRAD,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)
from .models import MxZOptimalControlXYNoiseSpectrumParams

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))


def _builtin(value: Any) -> Any:
    """将 NumPy 标量和数组转换为可序列化的内置类型。"""
    if isinstance(value, dict):
        return {str(key): _builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_builtin(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少分析输入: {path}")
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise TypeError(f"YAML 顶层必须是映射: {path}")
    return payload


def _load_params(
    run_dir: Path,
    *,
    low_freq_skip_hz: float | None = None,
    noise_band_max_hz: float | None = None,
) -> tuple[MxZOptimalControlXYNoiseSpectrumParams, dict[str, Any]]:
    config = _load_yaml(run_dir / "experiment_config.yaml")
    values = config.get("parameters", {})
    if not isinstance(values, dict):
        raise TypeError("experiment_config.yaml 的 parameters 必须是映射")
    if low_freq_skip_hz is not None:
        values["LOW_FREQ_SKIP_HZ"] = float(low_freq_skip_hz)
    if noise_band_max_hz is not None:
        values["NOISE_BAND_MAX_HZ"] = float(noise_band_max_hz)
    params = MxZOptimalControlXYNoiseSpectrumParams.from_external(
        values,
        schema_version=int(config.get("schema_version", 1)),
        strict=False,
    )
    errors = params.validate_model()
    if errors:
        raise ValueError("；".join(errors))
    return params, config


def _load_manifest(run_dir: Path) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    manifest_path = run_dir / "raw" / "point_manifest.yaml"
    manifest = _load_yaml(manifest_path)
    try:
        x_axis = np.asarray(manifest["x_field_v"], dtype=float).reshape(-1)
        y_axis = np.asarray(manifest["y_field_v"], dtype=float).reshape(-1)
        points = manifest["points"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"点清单结构无效: {manifest_path}") from exc
    if x_axis.size == 0 or y_axis.size == 0 or not isinstance(points, dict):
        raise ValueError(f"点清单为空或缺少 points: {manifest_path}")
    return x_axis, y_axis, manifest


def _noise_files(point_dir: Path, expected_count: int) -> list[Path]:
    files = [point_dir / f"noise_{index:03d}.npz" for index in range(expected_count)]
    missing = [path for path in files if not path.is_file()]
    if missing:
        names = ", ".join(path.name for path in missing)
        raise FileNotFoundError(f"缺少噪声记录 ({point_dir}): {names}")
    return files


def _average_psd(files: list[Path]) -> tuple[np.ndarray, np.ndarray, float]:
    """对一个点的多条 R(t) 记录平均 Welch PSD。"""
    if not files:
        raise ValueError("噪声记录为空")
    spectra: list[np.ndarray] = []
    frequency: np.ndarray | None = None
    sample_rate: float | None = None
    sample_count: int | None = None
    for path in files:
        with np.load(path, allow_pickle=False) as data:
            if "r_v" not in data.files or "actual_rate_sa_s" not in data.files:
                raise ValueError(f"噪声记录缺少 r_v 或 actual_rate_sa_s: {path}")
            values = np.asarray(data["r_v"], dtype=float).reshape(-1)
            rate = float(np.asarray(data["actual_rate_sa_s"]).reshape(()))
        if values.size < 8 or not np.all(np.isfinite(values)):
            raise ValueError(f"R 噪声记录无效或有效样本不足 8: {path}")
        if not np.isfinite(rate) or rate <= 0:
            raise ValueError(f"实际采样率无效: {path}")
        if sample_rate is None:
            sample_rate = rate
            sample_count = values.size
        elif not np.isclose(rate, sample_rate, rtol=1e-9, atol=1e-6):
            raise ValueError(
                f"同一点噪声记录采样率不一致: {path} ({rate:g} vs {sample_rate:g} Sa/s)"
            )
        elif values.size != sample_count:
            raise ValueError(f"同一点噪声记录样本数不一致: {path}")
        f_axis, psd = scipy_signal.welch(
            values,
            fs=rate,
            window="hann",
            nperseg=values.size,
            detrend="constant",
            scaling="density",
        )
        if frequency is None:
            frequency = f_axis
        elif frequency.shape != f_axis.shape or not np.allclose(
            frequency, f_axis, rtol=1e-10, atol=1e-10
        ):
            raise ValueError(f"噪声记录 Welch 频率轴不一致: {path}")
        spectra.append(np.asarray(psd, dtype=float))
    assert frequency is not None and sample_rate is not None
    return frequency, np.mean(np.asarray(spectra), axis=0), sample_rate


def _plot_heatmap(
    results_dir: Path,
    x_axis: np.ndarray,
    y_axis: np.ndarray,
    matrix: np.ndarray,
    best: dict[str, Any],
) -> str:
    set_plot_style("paper")
    figure, axis = new_figure(kind="square")
    image = axis.pcolormesh(x_axis, y_axis, matrix.T, shading="auto", cmap="viridis", rasterized=True)
    axis.plot(best["x_field_v"], best["y_field_v"], "x", color=COLOR_TRAD, ms=8, mew=1.8, label="Minimum")
    format_axis(axis, xlabel="X DC bias (V)", ylabel="Y DC bias (V)")
    axis.set_title("R ASD median in selected band")
    figure.colorbar(image, ax=axis, label="ASD median (V/$\\sqrt{\\mathrm{Hz}}$)")
    style_legend(axis)
    filename = "noise_median_heatmap.png"
    save_figure(figure, results_dir / filename)
    return filename


def _plot_best_spectrum(
    results_dir: Path,
    frequency: np.ndarray,
    asd: np.ndarray,
    params: MxZOptimalControlXYNoiseSpectrumParams,
    best: dict[str, Any],
) -> str:
    set_plot_style("paper")
    figure, axis = new_figure()
    positive = (frequency > 0) & np.isfinite(asd) & (asd > 0)
    axis.loglog(frequency[positive], asd[positive], color=COLOR_OPTIMAL, label="Best point")
    axis.axvspan(params.low_freq_skip_hz, params.noise_band_max_hz, color="0.8", alpha=0.35, label="Statistics band")
    format_axis(axis, xlabel="Frequency (Hz)", ylabel="R ASD (V/$\\sqrt{\\mathrm{Hz}}$)")
    axis.set_title(f"Best point: X={best['x_field_v']:+.4f} V, Y={best['y_field_v']:+.4f} V")
    style_legend(axis)
    filename = "noise_spectrum_best.png"
    save_figure(figure, results_dir / filename)
    return filename


def analyze(
    run_dir: Path | None = None,
    *,
    low_freq_skip_hz: float | None = None,
    noise_band_max_hz: float | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """分析新目录，也兼容旧 XY RF 灵敏度目录中的噪声子目录。"""
    run_dir = Path(run_dir) if run_dir is not None else runtime_run_dir()
    run_dir = run_dir.resolve()
    params, config = _load_params(
        run_dir,
        low_freq_skip_hz=low_freq_skip_hz,
        noise_band_max_hz=noise_band_max_hz,
    )
    x_axis, y_axis, manifest = _load_manifest(run_dir)
    results_dir = Path(output_dir).resolve() if output_dir is not None else run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    matrix = np.full((x_axis.size, y_axis.size), np.nan, dtype=float)
    peak_matrix = np.full_like(matrix, np.nan)
    peak_frequency_matrix = np.full_like(matrix, np.nan)
    point_results: list[dict[str, Any]] = []
    global_frequency: np.ndarray | None = None
    best_spectrum: np.ndarray | None = None
    best_frequency: np.ndarray | None = None

    for key, point in manifest["points"].items():
        if not isinstance(point, dict):
            raise ValueError(f"点清单条目无效: {key}")
        status = str(point.get("status", "completed"))
        # 取消后的 running/pending 点没有完整记录，保留其 NaN 结果，
        # 仅对已完成点执行严格的记录完整性检查。
        if status != "completed":
            continue
        x_index = int(point["x_index"])
        y_index = int(point["y_index"])
        point_dir = run_dir / "raw" / "points" / str(key)
        files = _noise_files(point_dir, params.noise_n_avg)
        frequency, psd, actual_rate = _average_psd(files)
        if params.noise_band_max_hz >= actual_rate / 2.0:
            raise ValueError(
                f"NOISE_BAND_MAX_HZ={params.noise_band_max_hz:g} 不低于实际采样率 "
                f"{actual_rate:g} Sa/s 的 Nyquist 频率"
            )
        if global_frequency is None:
            global_frequency = frequency
        elif global_frequency.shape != frequency.shape or not np.allclose(
            global_frequency, frequency, rtol=1e-10, atol=1e-10
        ):
            raise ValueError(f"不同 XY 点的 Welch 频率轴不一致: {key}")
        asd = np.sqrt(np.maximum(psd, 0.0))
        mask = (
            (frequency >= params.low_freq_skip_hz)
            & (frequency <= params.noise_band_max_hz)
            & np.isfinite(asd)
        )
        if not np.any(mask):
            raise ValueError(f"统计频段没有有效频点: {key}")
        median_asd = float(np.median(asd[mask]))
        peak_index = int(np.argmax(asd[mask]))
        selected_frequency = frequency[mask]
        selected_asd = asd[mask]
        result = {
            "key": str(key),
            "x_index": x_index,
            "y_index": y_index,
            "x_field_v": float(point.get("x_field_v", x_axis[x_index])),
            "y_field_v": float(point.get("y_field_v", y_axis[y_index])),
            "actual_rate_sa_s": actual_rate,
            "noise_median_r_asd_v_per_sqrt_hz": median_asd,
            "noise_peak_r_asd_v_per_sqrt_hz": float(selected_asd[peak_index]),
            "noise_peak_frequency_hz": float(selected_frequency[peak_index]),
            "record_count": len(files),
            "frequency_point_count": int(np.count_nonzero(mask)),
        }
        matrix[x_index, y_index] = median_asd
        peak_matrix[x_index, y_index] = result["noise_peak_r_asd_v_per_sqrt_hz"]
        peak_frequency_matrix[x_index, y_index] = result["noise_peak_frequency_hz"]
        point_results.append(result)
        if best_spectrum is None or median_asd < min(item["noise_median_r_asd_v_per_sqrt_hz"] for item in point_results[:-1]):
            best_spectrum = asd
            best_frequency = frequency

    if not point_results or not np.any(np.isfinite(matrix)):
        raise ValueError("没有可分析的完整 XY 噪声点")
    best = min(point_results, key=lambda item: item["noise_median_r_asd_v_per_sqrt_hz"])
    assert best_frequency is not None and best_spectrum is not None
    np.savez(
        results_dir / "noise_median_matrix.npz",
        x_field_v=x_axis,
        y_field_v=y_axis,
        noise_median_matrix=matrix,
        noise_median_r_asd_v_per_sqrt_hz=matrix,
        noise_peak_r_asd_v_per_sqrt_hz=peak_matrix,
        noise_peak_frequency_hz=peak_frequency_matrix,
        frequency_axis_hz=best_frequency,
        frequency_hz=best_frequency,
        best_asd_v_per_sqrt_hz=best_spectrum,
    )
    summary = {
        "experiment_id": "mx-z-optimal-control-xy-noise-spectrum",
        "data_type": config.get("data_type", "Mx_Z_Optimal_Control_XY_Noise_Spectrum"),
        "run_dir": str(run_dir),
        "metric": "noise_median_r_asd_v_per_sqrt_hz",
        "units": "V/sqrt(Hz)",
        "psd_method": "Welch Hann detrend=constant scaling=density; mean PSD then sqrt ASD",
        "statistics_band_hz": [params.low_freq_skip_hz, params.noise_band_max_hz],
        "narrowband_lines_included": True,
        "point_count": len(point_results),
        "best_point": best,
        "points": sorted(point_results, key=lambda item: item["noise_median_r_asd_v_per_sqrt_hz"]),
        "warnings": [
            "XY 坐标为信号发生器电压；未进行 nT 换算。",
            "兼容旧 XY RF 灵敏度目录时，结果仅代表原始 Demod0 R 噪声。",
        ],
    }
    payload = _builtin(summary)
    (results_dir / "analysis.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (results_dir / "analysis.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _plot_heatmap(results_dir, x_axis, y_axis, matrix, best)
    _plot_best_spectrum(results_dir, best_frequency, best_spectrum, params, best)
    print(
        f"XY 噪声谱分析完成: best X={best['x_field_v']:+.6f} V, "
        f"Y={best['y_field_v']:+.6f} V, "
        f"median={best['noise_median_r_asd_v_per_sqrt_hz']:.6g} V/sqrt(Hz)"
    )
    return payload


def analyze_legacy_xy_rf_noise_run(
    old_run_dir: Path,
    output_dir: Path | None = None,
    *,
    low_freq_skip_hz: float = 3.0,
    noise_band_max_hz: float = 200.0,
) -> dict[str, Any]:
    """兼容旧目录的公开预分析入口；不改写旧 raw 数据。"""
    old_run_dir = Path(old_run_dir).resolve()
    return analyze(
        old_run_dir,
        low_freq_skip_hz=low_freq_skip_hz,
        noise_band_max_hz=noise_band_max_hz,
        output_dir=output_dir,
    )


def main() -> int:
    analyze()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
