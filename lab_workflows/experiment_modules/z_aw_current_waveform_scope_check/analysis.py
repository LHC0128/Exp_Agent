"""Z 任意波实际电流波形验证分析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...current_feedback import sense_voltage_to_current, spectral_nrmse
from ...plotting import format_axis, new_figure, save_figure, set_plot_style
from ..z_aw_waveform_scope_check.analysis import (
    _falling_edge_time,
    _load_capture,
    _periodic_alignment,
    _resample_for_plot,
    _zscore_rows,
)


EXPERIMENT_ID = "z-aw-current-waveform-scope-check"


def _builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_builtin(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise TypeError(f"YAML 顶层必须是映射: {path}")
    return payload


def _load_expected(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as data:
        required = {"time_s", "target_current_a", "repeat_frequency_hz"}
        missing = sorted(required.difference(data.files))
        if missing:
            raise ValueError(f"电流目标波形缺少字段: {', '.join(missing)}")
        result = {
            "time_s": np.asarray(data["time_s"], dtype=float).reshape(-1),
            "voltage_v": np.asarray(data["target_current_a"], dtype=float).reshape(-1),
            "repeat_frequency_hz": np.asarray(data["repeat_frequency_hz"], dtype=float).reshape(()),
        }
    if result["time_s"].size != result["voltage_v"].size or result["time_s"].size < 2:
        raise ValueError("目标电流时间轴和波形长度不一致")
    return result


def analyze(run_dir: Path) -> dict[str, Any]:
    """将 CH3 采样电阻电压转换为电流并完成周期相似度分析。"""
    run_dir = Path(run_dir).resolve()
    config = _load_yaml(run_dir / "experiment_config.yaml")
    parameters = config.get("parameters", {})
    resistance = float(parameters.get("SENSE_RESISTOR_OHM", 0.0))
    trigger_level = float(parameters.get("SCOPE_TRIGGER_LEVEL_V", 2.5))
    if resistance <= 0.0:
        raise ValueError("SENSE_RESISTOR_OHM 必须为正")
    expected = _load_expected(run_dir / "raw" / "applied_control_waveform.npz")
    records: list[dict[str, Any]] = []
    capture_paths = sorted((run_dir / "raw").glob("scope_capture_*.npz"))
    for path in capture_paths:
        capture = _load_capture(path)
        edge_time = _falling_edge_time(
            capture["trigger_time_s"], capture["trigger_voltage_v"], trigger_level
        )
        time_s = capture["time_s"] - edge_time
        current_a = sense_voltage_to_current(capture["measured_voltage_v"], resistance)
        aligned = _periodic_alignment(
            time_s,
            current_a,
            expected,
            scale_v_div=capture.get("scale_used_v_div"),
            offset_v=capture.get("offset_used_v", 0.0),
        )
        aligned["spectral_nrmse"] = spectral_nrmse(
            aligned["aligned_expected_voltage_v"], aligned["measured_voltage_v"]
        )
        aligned["file"] = str(path.relative_to(run_dir))
        aligned["trigger_edge_time_s"] = edge_time
        records.append(aligned)
    if not records:
        raise FileNotFoundError("未找到电流波形采集文件")
    metric_names = (
        "shape_correlation",
        "shape_nrmse",
        "best_delay_s",
        "affine_gain",
        "affine_offset_v",
        "raw_affine_rmse_v",
        "measured_peak_to_peak_v",
        "expected_peak_to_peak_v",
        "range_ratio",
        "spectral_nrmse",
        "saturation_fraction",
        "scope_edge_fraction",
    )
    summary: dict[str, Any] = {}
    for name in metric_names:
        values = np.asarray([record[name] for record in records], dtype=float)
        summary[name] = {
            "mean": float(np.nanmean(values)),
            "std": float(np.nanstd(values)),
            "minimum": float(np.nanmin(values)),
            "maximum": float(np.nanmax(values)),
        }
    results_dir = run_dir / "results"
    results_dir.mkdir(exist_ok=True)
    grid = np.linspace(0.0, 1.0 / float(expected["repeat_frequency_hz"]), 2048, endpoint=False)
    measured = []
    reference = []
    for record in records:
        expected_record = {
            "time_s": expected["time_s"],
            "voltage_v": expected["voltage_v"],
            "repeat_frequency_hz": expected["repeat_frequency_hz"],
        }
        local_grid, local_measured, local_reference = _resample_for_plot(
            record,
            expected_record,
            float(1.0 / expected["repeat_frequency_hz"]),
            cycles=1,
            time_window_s=(0.0, float(1.0 / expected["repeat_frequency_hz"])),
            points=grid.size,
        )
        measured.append(local_measured)
        reference.append(local_reference)
    measured_array = np.vstack(measured)
    reference_array = np.vstack(reference)
    figure, axes = new_figure(nrows=2, ncols=1, kind="wide", constrained_layout=True)
    time_ms = grid * 1e3
    axes[0].plot(time_ms, reference_array[0], label="Target current")
    axes[0].plot(time_ms, np.mean(measured_array, axis=0), label="Measured current")
    format_axis(axes[0], xlabel="Time (ms)", ylabel="Current (A)")
    axes[0].grid(True, alpha=0.25)
    axes[0].legend(loc="best")
    normalized_measured = _zscore_rows(measured_array)
    normalized_reference = _zscore_rows(reference_array)
    axes[1].plot(time_ms, np.mean(normalized_measured, axis=0), label="Measured normalized")
    axes[1].plot(time_ms, normalized_reference[0], label="Target normalized")
    format_axis(axes[1], xlabel="Time (ms)", ylabel="Z-score current")
    axes[1].grid(True, alpha=0.25)
    axes[1].legend(loc="best")
    save_figure(figure, results_dir / "current_waveform_similarity.png")
    np.savez(
        results_dir / "current_waveforms.npz",
        measured_current_a=measured_array,
        target_current_a=reference_array,
        shape_correlation=np.asarray([r["shape_correlation"] for r in records]),
        spectral_nrmse=np.asarray([r["spectral_nrmse"] for r in records]),
    )
    payload = _builtin(
        {
            "experiment_id": EXPERIMENT_ID,
            "run_dir": str(run_dir),
            "sense_resistor_ohm": resistance,
            "capture_count": len(records),
            "saturation_detected": any(
                bool(record["saturation_detected"]) for record in records
            ),
            "similarity_summary": summary,
            "captures": [
                {name: record[name] for name in metric_names} | {"file": record["file"]}
                for record in records
            ],
            "files": [
                "current_waveform_similarity.png",
                "current_waveforms.npz",
                "current_similarity_report.yaml",
                "current_similarity_report.json",
            ],
        }
    )
    (results_dir / "current_similarity_report.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (results_dir / "current_similarity_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def main() -> int:
    from ...experiment_runtime import runtime_run_dir

    analyze(runtime_run_dir())
    return 0
