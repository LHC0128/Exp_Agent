"""电流波形原算法：严格加载、周期对齐、相似度与报告导出。"""
import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...current_feedback import sense_voltage_to_current, spectral_nrmse
from ...experiment_runtime import check_cancelled
from ..z_aw_waveform_scope_check.analysis_steps import (
    _builtin, _load_yaml, _load_capture, _falling_edge_time,
    _periodic_alignment, _resample_for_plot,
)
from .run_format import EXPERIMENT_ID, analysis_block_reason

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
    if not np.all(np.isfinite(result["time_s"])) or not np.all(np.isfinite(result["voltage_v"])):
        raise ValueError("目标电流波形包含非有限值")
    if not np.all(np.diff(result["time_s"]) > 0):
        raise ValueError("目标电流时间轴必须严格递增")
    frequency = float(result["repeat_frequency_hz"])
    if not np.isfinite(frequency) or frequency <= 0:
        raise ValueError("目标电流重复频率必须为正")
    return result


def load_inputs(run_dir):
    config = _load_yaml(run_dir / "experiment_config.yaml")
    if reason := analysis_block_reason(config):
        raise ValueError(reason)
    parameters = config["parameters"]
    resistance = float(parameters.get("SENSE_RESISTOR_OHM", 0.0))
    if not np.isfinite(resistance) or resistance <= 0:
        raise ValueError("SENSE_RESISTOR_OHM 必须为有限正值")
    level = float(parameters.get("SCOPE_TRIGGER_LEVEL_V", 2.5))
    if not np.isfinite(level):
        raise ValueError("触发电平必须有限")
    if parameters.get("SCOPE_MEASURED_CHANNEL", 3) != 3 or parameters.get("SCOPE_TRIGGER_CHANNEL", 4) != 4:
        raise ValueError("电流验证要求 CH3 测量和 CH4 触发")
    expected = _load_expected(run_dir / "raw/applied_control_waveform.npz")
    paths = sorted((run_dir / "raw").glob("scope_capture_*.npz"))
    if not paths:
        raise FileNotFoundError("未找到电流波形采集文件")
    for path in paths:
        check_cancelled()
        frame = _load_capture(path)
        _falling_edge_time(frame["trigger_time_s"], frame["trigger_voltage_v"], level)
    return config, expected, paths


def evaluate(run_dir):
    """保持原延迟搜索、固定单周期重采样、频谱与重复统计。"""
    config, expected, capture_paths = load_inputs(run_dir)
    parameters = config["parameters"]
    resistance = float(parameters["SENSE_RESISTOR_OHM"])
    trigger_level = float(parameters.get("SCOPE_TRIGGER_LEVEL_V", 2.5))
    records: list[dict[str, Any]] = []
    for path in capture_paths:
        check_cancelled()
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
            scale_v_div=(capture["scale_used_v_div"] / resistance if "scale_used_v_div" in capture else None),
            offset_v=capture.get("offset_used_v", 0.0) / resistance,
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
    np.savez(
        results_dir / "current_waveforms.npz",
        time_s=grid,
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
    payload["status"] = "partial"
    (results_dir / "current_metrics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return payload


def write_report(run_dir):
    from .plotting import plot_current
    results = run_dir / "results"
    payload = json.loads((results / "current_metrics.json").read_text(encoding="utf-8"))
    with np.load(results / "current_waveforms.npz", allow_pickle=False) as data:
        plot_current(results, data["time_s"], data["measured_current_a"], data["target_current_a"])
    payload["status"] = "completed"
    payload["files"].insert(1, "current_waveform_similarity.pdf")
    (results / "current_similarity_report.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (results / "current_similarity_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return payload
