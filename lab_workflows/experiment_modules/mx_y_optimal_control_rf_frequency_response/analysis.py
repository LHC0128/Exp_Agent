"""Mx Y 最优控制 RF 频率响应离线分析（相位中位数与常数控制对照）。"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import yaml
from ...experiment_runtime import runtime_run_dir
from ...plotting import new_figure, save_figure, set_plot_style, format_axis

EXPERIMENT_ID = "mx-y-optimal-control-rf-frequency-response"
OPTIMAL_CONTROL_NPZ = "optimal_control_phase_frequency_scan.npz"
CONSTANT_CONTROL_NPZ = "constant_control_frequency_scan.npz"

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
    image = axis.pcolormesh(phase_deg, frequency_hz, np.ma.masked_invalid(r_mean_v), shading="nearest", cmap="viridis")
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

def analyze(run_dir: str | Path) -> dict[str, object]:
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

    optimal_response, valid_phase_count = _phase_median(frequency, r_mean_v)
    np.savez(results / "optimal_control_median_response.npz", frequency_hz=frequency, response_median_v=optimal_response, valid_phase_count=valid_phase_count)
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

def main() -> int:
    analyze(runtime_run_dir()); return 0

if __name__ == "__main__": raise SystemExit(main())
