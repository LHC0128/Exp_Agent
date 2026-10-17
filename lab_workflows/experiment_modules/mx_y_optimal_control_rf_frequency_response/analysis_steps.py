"""Mx Y 最优控制 RF 频率响应离线分析（相位中位数与常数控制对照）。"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import yaml
from ...experiment_runtime import runtime_run_dir
from ...plotting import new_figure, save_figure, set_plot_style, format_axis
from . import run_format

EXPERIMENT_ID = "mx-y-optimal-control-rf-frequency-response"
OPTIMAL_CONTROL_NPZ = "optimal_control_phase_frequency_scan.npz"
CONSTANT_CONTROL_NPZ = "constant_control_frequency_scan.npz"


class AnalysisQualityError(ValueError):
    """原始数据不支持每频率的相位中位数；保留诊断。"""


def plain(value):
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [plain(item) for item in value]
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def load_inputs(run_dir, config):
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    raw = Path(run_dir) / "raw"
    def matrix(name, constant=False):
        with np.load(raw / name, allow_pickle=True) as data:
            required = {"frequency_hz", "phase_deg", "r_mean_v", "r_std_v", "accepted_attempt_index", "accepted_file"}
            if missing := required - set(data.files):
                raise ValueError(name + " 缺少字段：" + "、".join(sorted(missing)))
            result = {key: np.asarray(data[key]) for key in required}
        frequency, phase = result["frequency_hz"], result["phase_deg"]
        if (frequency.ndim != 1 or frequency.size < 2 or not np.all(np.isfinite(frequency))
                or np.any(frequency <= 0) or np.any(np.diff(frequency) <= 0)):
            raise ValueError(name + " 频率轴无效")
        if phase.ndim != 1 or not phase.size or not np.all(np.isfinite(phase)):
            raise ValueError(name + " 相位轴无效")
        shape = (frequency.size,) if constant else (frequency.size, phase.size)
        if any(result[key].shape != shape for key in ("r_mean_v", "r_std_v", "accepted_attempt_index", "accepted_file")):
            raise ValueError(name + " 矩阵形状与频率/相位轴不匹配")
        if np.any(result["r_std_v"][np.isfinite(result["r_std_v"])] < 0):
            raise ValueError(name + " R标准差不能为负数")
        if constant and (phase.shape != frequency.shape or np.any(phase != 0)):
            raise ValueError("常数对照必须每频率一个0°相位点")
        if not constant and (phase.size < 2 or np.any(np.diff(phase) <= 0)):
            raise ValueError("最优控制相位轴必须严格递增且至少2点")
        return result
    optimal = matrix(OPTIMAL_CONTROL_NPZ)
    constant = matrix(CONSTANT_CONTROL_NPZ, True) if config.get("comparison", {}).get("enabled") else None
    if constant is not None and not np.array_equal(optimal["frequency_hz"], constant["frequency_hz"]):
        raise ValueError("常数对照与最优控制频率轴不一致")
    return optimal, constant


def evaluate(run_dir, config):
    optimal, _ = load_inputs(run_dir, config)
    response, counts = _phase_median(optimal["frequency_hz"], optimal["r_mean_v"])
    diagnostics = {"valid_phase_count": counts.tolist(), "missing_frequency_indices": np.flatnonzero(counts == 0).tolist()}
    results = Path(run_dir) / "results"
    results.mkdir(exist_ok=True)
    (results / "median_diagnostics.yaml").write_text(yaml.safe_dump(diagnostics), encoding="utf-8")
    if np.any(counts == 0):
        raise AnalysisQualityError("部分频率没有有效相位结果，已保留中位数诊断")
    np.savez(results / "optimal_control_median_response.npz", frequency_hz=optimal["frequency_hz"], response_median_v=response, valid_phase_count=counts)
    return diagnostics

def _builtin(value):
    if isinstance(value, dict):
        return {str(k): _builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_builtin(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value

def _load_config(run_dir: Path) -> dict:
    path = run_dir / "experiment_config.yaml"
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}

def _phase_median(frequency_hz: np.ndarray, r_mean_v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """按频率取全部有效相位结果的中位数。"""
    response = np.full(frequency_hz.size, np.nan)
    counts = np.zeros(frequency_hz.size, dtype=int)
    for index in range(frequency_hz.size):
        finite = r_mean_v[index][np.isfinite(r_mean_v[index])]
        counts[index] = finite.size
        if finite.size:
            response[index] = float(np.median(finite))
    return response, counts

def _plot_heatmap(path: Path, phase_deg, frequency_hz, r_mean_v) -> None:
    set_plot_style("paper")
    figure, axis = new_figure(kind="wide")
    image = axis.pcolormesh(phase_deg, frequency_hz, np.ma.masked_invalid(r_mean_v), shading="nearest", cmap="viridis", rasterized=True)
    format_axis(axis, xlabel="Y RF burst phase (deg)", ylabel="Y RF frequency (Hz)")
    figure.colorbar(image, ax=axis, label="Demod0 R (V)")
    save_figure(figure, path)

def _plot_comparison(path: Path, frequency_hz, optimal_v, constant_v) -> None:
    set_plot_style("paper")
    figure, axis = new_figure(kind="wide")
    axis.plot(frequency_hz, optimal_v, "o-", markersize=3, label="Optimal control (phase median)")
    axis.plot(frequency_hz, constant_v, "s-", markersize=3, label="Constant control (phase 0 deg)")
    format_axis(axis, xlabel="Y RF frequency (Hz)", ylabel="Demod0 R (V)")
    axis.legend(loc="best")
    save_figure(figure, path)

def report(run_dir: str | Path) -> dict[str, object]:
    """生成相位中位数、常数控制对照曲线与相位-频率诊断热图。"""
    root = Path(run_dir); raw = root / "raw"; results = root / "results"; results.mkdir(parents=True, exist_ok=True)
    config = _load_config(root)
    with np.load(raw / OPTIMAL_CONTROL_NPZ, allow_pickle=True) as data:
        frequency = np.asarray(data["frequency_hz"], dtype=float)
        phase = np.asarray(data["phase_deg"], dtype=float)
        r_mean_v = np.asarray(data["r_mean_v"], dtype=float)
        r_std_v = np.asarray(data["r_std_v"], dtype=float)
        attempts = np.asarray(data["accepted_attempt_index"], dtype=int)
        files = np.asarray(data["accepted_file"], dtype=object)

    with np.load(results / "optimal_control_median_response.npz", allow_pickle=False) as data:
        optimal_response = data["response_median_v"]
        valid_phase_count = data["valid_phase_count"]
    _plot_heatmap(results / "r_phase_frequency.png", phase, frequency, r_mean_v)

    comparison_enabled = bool(config.get("comparison", {}).get("enabled"))
    constant_path = raw / CONSTANT_CONTROL_NPZ
    constant_response = None
    constant_frequency = None
    constant_phase = None
    constant_r_std = None
    constant_attempts = None
    constant_files = None
    comparison_file = None
    if comparison_enabled and constant_path.is_file():
        with np.load(constant_path, allow_pickle=True) as data:
            constant_frequency = np.asarray(data["frequency_hz"], dtype=float)
            constant_phase = np.asarray(data["phase_deg"], dtype=float)
            constant_response = np.asarray(data["r_mean_v"], dtype=float)
            constant_r_std = np.asarray(data["r_std_v"], dtype=float)
            constant_attempts = np.asarray(data["accepted_attempt_index"], dtype=int)
            constant_files = np.asarray(data["accepted_file"], dtype=object)
        _plot_comparison(results / "rf_frequency_response_comparison.png", frequency, optimal_response, constant_response)
        comparison_file = "rf_frequency_response_comparison.png"

    files_out = ["optimal_control_median_response.npz", "r_phase_frequency.png"]
    if comparison_file:
        files_out.append(comparison_file)
    analysis = _builtin({
        "success": True,
        "experiment_id": EXPERIMENT_ID,
        "run_dir": str(root),
        "response_definitions": {
            "optimal_control": "median of all finite Demod0 R over the phase axis at each RF frequency",
            "constant_control": "single accepted Demod0 R at Y RF burst phase 0 deg",
        },
        "comparison_enabled": comparison_enabled,
        "calibration": config.get("comparison"),
        "control_source": config.get("control_source", {}),
        "frequency_hz": frequency.tolist(),
        "phase_deg": phase.tolist(),
        "shape": list(r_mean_v.shape),
        "optimal_control": {
            "response_median_v": optimal_response.tolist(),
            "valid_phase_count": valid_phase_count.tolist(),
            "valid_points": int(np.count_nonzero(np.isfinite(r_mean_v))),
            "total_points": int(r_mean_v.size),
            "quality": {"accepted_attempt_index": attempts.tolist(), "accepted_file": files.tolist(), "r_std_v": r_std_v.tolist()},
        },
        "constant_control": (
            {
                "phase_deg": constant_phase.tolist(),
                "response_v": constant_response.tolist(),
                "valid_points": int(np.count_nonzero(np.isfinite(constant_response))),
                "total_points": int(constant_response.size),
                "quality": {"accepted_attempt_index": constant_attempts.tolist(), "accepted_file": constant_files.tolist(), "r_std_v": constant_r_std.tolist()},
            }
            if constant_response is not None
            else None
        ),
        "plot_profile": "paper",
        "files": files_out,
    })
    (results / "analysis.yaml").write_text(yaml.safe_dump(analysis, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (results / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    return analysis
