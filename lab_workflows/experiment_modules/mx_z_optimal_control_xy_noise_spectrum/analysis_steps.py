"""原 Welch、频段中位数、谱线峰值与排名；不连接仪器。"""
from pathlib import Path
from typing import Any
import json
import re
import numpy as np
import yaml
from scipy import signal as scipy_signal
from ...experiment_runtime import check_cancelled
from .models import MxZOptimalControlXYNoiseSpectrumParams
from . import run_format
from .analysis_plotting import _plot_heatmap, _plot_best_spectrum

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
    errors = params.validate_noise_parameters()
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
    if not np.all(np.isfinite(x_axis)) or not np.all(np.isfinite(y_axis)):
        raise ValueError("网格轴必须为有限数值")
    occupied = set()
    for key, point in points.items():
        if not isinstance(key, str) or not re.fullmatch(r"x_[0-9]+_y_[0-9]+", key) or not isinstance(point, dict):
            raise ValueError("点清单键或条目无效")
        x, y = point.get("x_index"), point.get("y_index")
        if type(x) is not int or type(y) is not int or not (0 <= x < x_axis.size and 0 <= y < y_axis.size):
            raise ValueError("点清单索引无效或越界")
        if key != f"x_{x:03d}_y_{y:03d}" or (x, y) in occupied:
            raise ValueError("点清单键与索引不一致或重复")
        occupied.add((x, y))
        if not np.isclose(point.get("x_field_v", x_axis[x]), x_axis[x], rtol=1e-12, atol=1e-12) or not np.isclose(
                point.get("y_field_v", y_axis[y]), y_axis[y], rtol=1e-12, atol=1e-12):
            raise ValueError("点清单坐标与网格轴不一致")
        point_path = (run_dir / "raw/points" / key).resolve()
        if (run_dir / "raw/points").resolve() not in point_path.parents:
            raise ValueError("点目录超出 raw/points")
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


def load_inputs(run_dir, params, *, allow_legacy=False):
    config = _load_yaml(run_dir / "experiment_config.yaml")
    if reason := run_format.analysis_block_reason(config, allow_legacy=allow_legacy):
        raise ValueError(reason)
    _, _, manifest = _load_manifest(run_dir)
    reference_frequency = None
    for key, point in manifest["points"].items():
        check_cancelled()
        if point.get("status", "completed") != "completed":
            continue
        frequency, _, rate = _average_psd(_noise_files(run_dir / "raw/points" / key, params.noise_n_avg))
        if reference_frequency is not None and (reference_frequency.shape != frequency.shape or not np.allclose(
                reference_frequency, frequency, rtol=1e-10, atol=1e-10)):
            raise ValueError("不同 XY 点的 Welch 频率轴不一致")
        reference_frequency = frequency
        if params.noise_band_max_hz >= rate / 2:
            raise ValueError("统计上限必须低于实际采样率的 Nyquist 频率")
        if not np.any((frequency >= params.low_freq_skip_hz) & (frequency <= params.noise_band_max_hz)):
            raise ValueError("统计频段没有有效频点")
    return config



def evaluate(run_dir, params, config, output_dir=None):
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
        check_cancelled()
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
    (results_dir / "noise_metrics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    _plot_heatmap(results_dir, x_axis, y_axis, matrix, best)
    _plot_best_spectrum(results_dir, best_frequency, best_spectrum, params, best)
    print(
        f"XY 噪声谱分析完成: best X={best['x_field_v']:+.6f} V, "
        f"Y={best['y_field_v']:+.6f} V, "
        f"median={best['noise_median_r_asd_v_per_sqrt_hz']:.6g} V/sqrt(Hz)"
    )
    return payload



def write_report(results_dir):
    payload = json.loads((results_dir / "noise_metrics.json").read_text(encoding="utf-8"))
    (results_dir / "analysis.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (results_dir / "analysis.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload
