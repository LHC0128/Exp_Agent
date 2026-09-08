"""Z 任意波线圈波形一致性离线分析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...experiment_runtime import runtime_run_dir
from ...plotting import format_axis, new_figure, save_figure, set_plot_style


EXPERIMENT_ID = "z-aw-waveform-scope-check"


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


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少分析输入: {path}")
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise TypeError(f"YAML 顶层必须是映射: {path}")
    return payload


def _load_expected(path: Path) -> dict[str, np.ndarray]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少理论波形快照: {path}")
    with np.load(path, allow_pickle=False) as data:
        required = {"time_s", "voltage_v", "repeat_frequency_hz"}
        missing = sorted(required.difference(data.files))
        if missing:
            raise ValueError(f"理论波形缺少字段: {', '.join(missing)}")
        result = {
            "time_s": np.asarray(data["time_s"], dtype=float).reshape(-1),
            "voltage_v": np.asarray(data["voltage_v"], dtype=float).reshape(-1),
            "repeat_frequency_hz": np.asarray(
                data["repeat_frequency_hz"], dtype=float
            ).reshape(()),
        }
    if result["time_s"].size < 2 or result["time_s"].size != result["voltage_v"].size:
        raise ValueError("理论波形时间轴和电压长度不一致")
    if not np.all(np.isfinite(result["time_s"])) or not np.all(
        np.isfinite(result["voltage_v"])
    ):
        raise ValueError("理论波形包含非有限值")
    if not np.all(np.diff(result["time_s"]) > 0):
        raise ValueError("理论波形时间轴必须严格递增")
    frequency = float(result["repeat_frequency_hz"])
    if not np.isfinite(frequency) or frequency <= 0:
        raise ValueError("理论波形重复频率无效")
    return result


def _load_capture(path: Path) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as data:
        required = {
            "time_s",
            "measured_voltage_v",
            "trigger_time_s",
            "trigger_voltage_v",
        }
        missing = sorted(required.difference(data.files))
        if missing:
            raise ValueError(f"{path.name} 缺少字段: {', '.join(missing)}")
        result = {
            "time_s": np.asarray(data["time_s"], dtype=float).reshape(-1),
            "measured_voltage_v": np.asarray(
                data["measured_voltage_v"], dtype=float
            ).reshape(-1),
            "trigger_time_s": np.asarray(data["trigger_time_s"], dtype=float).reshape(-1),
            "trigger_voltage_v": np.asarray(
                data["trigger_voltage_v"], dtype=float
            ).reshape(-1),
        }
    for key, values in result.items():
        if values.size < 2 or not np.all(np.isfinite(values)):
            raise ValueError(f"{path.name} 的 {key} 无有效数据")
    if result["time_s"].size != result["measured_voltage_v"].size:
        raise ValueError(f"{path.name} 的 C3 时间轴和电压长度不一致")
    if result["trigger_time_s"].size != result["trigger_voltage_v"].size:
        raise ValueError(f"{path.name} 的 C4 时间轴和电压长度不一致")
    if not np.all(np.diff(result["time_s"]) > 0):
        raise ValueError(f"{path.name} 的 C3 时间轴必须严格递增")
    if not np.all(np.diff(result["trigger_time_s"]) > 0):
        raise ValueError(f"{path.name} 的 C4 时间轴必须严格递增")
    with np.load(path, allow_pickle=False) as data:
        for key in (
            "scale_used_v_div",
            "offset_used_v",
            "actual_rate_sa_s",
            "requested_rate_sa_s",
            "requested_duration_s",
        ):
            if key in data.files:
                value = np.asarray(data[key], dtype=float).reshape(())
                result[key] = float(value)
    return result


def _periodic_interp(
    query_s: np.ndarray,
    source_s: np.ndarray,
    values: np.ndarray,
    period_s: float,
) -> np.ndarray:
    source = np.asarray(source_s, dtype=float)
    y = np.asarray(values, dtype=float)
    source_start = float(source[0])
    source_rel = source - source_start
    phase = np.mod(np.asarray(query_s, dtype=float) - source_start, period_s)
    # The source file has N points over N*dt, so append the first point at T.
    source_period = float(period_s)
    return np.interp(
        phase,
        np.r_[source_rel, source_period],
        np.r_[y, y[0]],
    )


def _falling_edge_time(time_s: np.ndarray, voltage_v: np.ndarray, level_v: float) -> float:
    """返回第一个穿过触发电平的下降沿时刻。"""
    before = voltage_v[:-1] >= level_v
    after = voltage_v[1:] < level_v
    indices = np.flatnonzero(before & after)
    if indices.size == 0:
        raise ValueError("CH4 未检测到下降沿触发")
    index = int(indices[0])
    v0 = float(voltage_v[index])
    v1 = float(voltage_v[index + 1])
    t0 = float(time_s[index])
    t1 = float(time_s[index + 1])
    if v1 == v0:
        return t0
    fraction = (level_v - v0) / (v1 - v0)
    return t0 + float(np.clip(fraction, 0.0, 1.0)) * (t1 - t0)


def _zscore(values: np.ndarray) -> np.ndarray:
    centered = np.asarray(values, dtype=float) - float(np.mean(values))
    scale = float(np.std(centered))
    if not np.isfinite(scale) or scale <= 0.0:
        raise ValueError("波形标准差为零，无法计算形状相似度")
    return centered / scale


def _zscore_rows(values: np.ndarray) -> np.ndarray:
    """对每次采集分别去均值并按标准差归一化。"""
    array = np.asarray(values, dtype=float)
    if array.ndim != 2:
        raise ValueError("归一化波形必须是二维数组")
    return np.vstack([_zscore(row) for row in array])


def _saturation_diagnostic(
    measured_v: np.ndarray,
    *,
    scale_v_div: float | None = None,
    offset_v: float = 0.0,
    vertical_divisions: int = 8,
) -> dict[str, Any]:
    """报告平顶/贴边样本，不将饱和诊断混入形状评分。"""
    values = np.asarray(measured_v, dtype=float)
    value_min = float(np.min(values))
    value_max = float(np.max(values))
    span = max(value_max - value_min, np.finfo(float).eps)
    tolerance = max(span * 1e-6, 1e-12)
    edge_mask = (np.abs(values - value_min) <= tolerance) | (
        np.abs(values - value_max) <= tolerance
    )
    edge_fraction = float(np.mean(edge_mask))
    max_run = 0
    current_run = 0
    for is_edge in edge_mask:
        if is_edge:
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 0

    scope_edge_fraction = float("nan")
    if scale_v_div is not None and np.isfinite(scale_v_div) and scale_v_div > 0:
        half_span = float(scale_v_div) * float(vertical_divisions) / 2.0
        display_center = -float(offset_v)
        scope_edge_mask = np.abs(np.abs(values - display_center) - half_span) <= max(
            half_span * 0.02, 1e-12
        )
        scope_edge_fraction = float(np.mean(scope_edge_mask))

    return {
        "saturation_detected": bool(max_run >= 3),
        "saturation_fraction": edge_fraction,
        "saturation_max_edge_run_samples": int(max_run),
        "scope_edge_fraction": scope_edge_fraction,
    }


def _periodic_alignment(
    time_s: np.ndarray,
    measured_v: np.ndarray,
    expected: dict[str, np.ndarray],
    *,
    scale_v_div: float | None = None,
    offset_v: float = 0.0,
    vertical_divisions: int = 8,
    max_delay_s: float | None = None,
) -> dict[str, Any]:
    """用周期相关搜索最佳时间平移并计算形状/幅值诊断。

    示波器的采样率可能远高于任意波的重复频率。直接在原始采样点上
    枚举整周期移位会产生 ``O(N * samples_per_period)`` 次插值，实际运行
    中很容易让离线分析长时间卡住。因此先在稀疏采样上粗搜，再在最佳
    候选附近用原始采样点精搜，最终的拟合和指标仍使用完整波形。
    """
    dt = float(np.median(np.diff(time_s)))
    if not np.isfinite(dt) or dt <= 0:
        raise ValueError("实测时间轴采样间隔无效")
    period_s = 1.0 / float(expected["repeat_frequency_hz"])
    reference = _periodic_interp(
        time_s,
        expected["time_s"],
        expected["voltage_v"],
        period_s,
    )
    samples_per_period = max(1, int(round(period_s / dt)))
    half_period = samples_per_period // 2
    if max_delay_s is not None:
        limit = int(np.floor(abs(float(max_delay_s)) / dt))
        if limit < 1:
            raise ValueError("max_delay_s 必须至少覆盖一个采样点")
        half_period = min(half_period, limit)
    measured_values = np.asarray(measured_v, dtype=float)
    measured_norm = _zscore(measured_values)

    # 限制粗搜候选点数量，避免高采样率示波器产生数十亿次插值。
    coarse_stride = max(1, int(np.ceil(samples_per_period / 512.0)))
    coarse_time = np.asarray(time_s, dtype=float)[::coarse_stride]
    coarse_norm = _zscore(measured_values[::coarse_stride])
    coarse_shifts = np.arange(
        -half_period, half_period + 1, coarse_stride, dtype=int
    )
    if coarse_shifts.size == 0 or coarse_shifts[-1] != half_period:
        coarse_shifts = np.append(coarse_shifts, half_period)

    def _score(candidate: int, query_time: np.ndarray, query_norm: np.ndarray) -> float:
        candidate_reference = _periodic_interp(
            query_time - float(candidate) * dt,
            expected["time_s"],
            expected["voltage_v"],
            period_s,
        )
        return float(
            np.dot(query_norm, _zscore(candidate_reference)) / query_norm.size
        )

    coarse_scores = [
        _score(int(candidate), coarse_time, coarse_norm) for candidate in coarse_shifts
    ]
    coarse_best = int(coarse_shifts[int(np.argmax(coarse_scores))])

    # 在粗搜结果附近恢复到原始采样分辨率，保证延迟指标与历史结果兼容。
    refine_start = max(-half_period, coarse_best - coarse_stride + 1)
    refine_stop = min(half_period, coarse_best + coarse_stride - 1)
    refine_shifts = np.arange(refine_start, refine_stop + 1, dtype=int)
    refine_scores = [
        _score(int(candidate), np.asarray(time_s, dtype=float), measured_norm)
        for candidate in refine_shifts
    ]
    shift_index = int(refine_shifts[int(np.argmax(refine_scores))])
    aligned_reference = _periodic_interp(
        np.asarray(time_s, dtype=float) - float(shift_index) * dt,
        expected["time_s"],
        expected["voltage_v"],
        period_s,
    )
    aligned_reference_norm = _zscore(aligned_reference)
    shape_corr = float(np.corrcoef(measured_norm, aligned_reference_norm)[0, 1])
    shape_nrmse = float(np.sqrt(np.mean((measured_norm - aligned_reference_norm) ** 2)))

    design = np.column_stack((aligned_reference, np.ones_like(aligned_reference)))
    gain, offset = np.linalg.lstsq(design, measured_v, rcond=None)[0]
    fitted = gain * aligned_reference + offset
    raw_rmse = float(np.sqrt(np.mean((measured_v - fitted) ** 2)))
    measured_range = float(np.max(measured_v) - np.min(measured_v))
    expected_range = float(np.max(aligned_reference) - np.min(aligned_reference))
    return {
        "time_s": np.asarray(time_s, dtype=float),
        "measured_voltage_v": np.asarray(measured_v, dtype=float),
        "expected_voltage_v": np.asarray(reference, dtype=float),
        "aligned_expected_voltage_v": np.asarray(aligned_reference, dtype=float),
        "best_shift_samples": shift_index,
        "best_delay_s": float(shift_index * dt),
        "shape_correlation": shape_corr,
        "shape_nrmse": shape_nrmse,
        "affine_gain": float(gain),
        "affine_offset_v": float(offset),
        "raw_affine_rmse_v": raw_rmse,
        "measured_peak_to_peak_v": measured_range,
        "expected_peak_to_peak_v": expected_range,
        "range_ratio": (
            measured_range / expected_range if expected_range > 0 else float("nan")
        ),
        "trigger_to_first_sample_s": float(time_s[0]),
        **_saturation_diagnostic(
            measured_v,
            scale_v_div=scale_v_div,
            offset_v=offset_v,
            vertical_divisions=vertical_divisions,
        ),
    }


def _resample_for_plot(
    result: dict[str, Any],
    expected: dict[str, np.ndarray],
    period_s: float,
    cycles: int,
    points: int = 4096,
    time_window_s: tuple[float, float] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    measured_time = np.asarray(result["time_s"], dtype=float)
    measured = np.asarray(result["measured_voltage_v"], dtype=float)
    if time_window_s is None:
        requested_start = -0.5 * float(cycles) * period_s
        requested_stop = 0.5 * float(cycles) * period_s
        start_s = max(requested_start, float(measured_time[0]))
        stop_s = min(requested_stop, float(measured_time[-1]))
    else:
        start_s, stop_s = map(float, time_window_s)
        start_s = max(start_s, float(measured_time[0]))
        stop_s = min(stop_s, float(measured_time[-1]))
    if not np.isfinite(start_s) or not np.isfinite(stop_s) or stop_s <= start_s:
        raise ValueError("波形实际时间窗口无效")
    grid = np.linspace(start_s, stop_s, points, endpoint=False)
    # 只在 C3 的真实采集窗口内插值，避免窗口外重复最后一个样本。
    measured_grid = np.interp(grid, measured_time, measured)
    expected_grid = _periodic_interp(
        grid - float(result["best_delay_s"]),
        expected["time_s"],
        expected["voltage_v"],
        period_s,
    )
    return grid, measured_grid, expected_grid


def _common_plot_window(
    records: list[dict[str, Any]], period_s: float, cycles: int
) -> tuple[float, float]:
    """返回所有采集共有的、以触发沿为零点的实际时间窗口。"""
    requested_start = -0.5 * float(cycles) * period_s
    requested_stop = 0.5 * float(cycles) * period_s
    start_s = max(requested_start, *(float(record["time_s"][0]) for record in records))
    stop_s = min(requested_stop, *(float(record["time_s"][-1]) for record in records))
    if not np.isfinite(start_s) or not np.isfinite(stop_s) or stop_s <= start_s:
        raise ValueError("多次采集没有共同的有效绘图时间窗口")
    return start_s, stop_s


def _plot_results(
    results_dir: Path,
    expected: dict[str, np.ndarray],
    records: list[dict[str, Any]],
    *,
    cycles: int,
    time_window_s: tuple[float, float],
) -> str:
    set_plot_style("paper")
    period_s = 1.0 / float(expected["repeat_frequency_hz"])
    grids = []
    measured = []
    references = []
    for record in records:
        grid, measured_grid, reference_grid = _resample_for_plot(
            record,
            expected,
            period_s,
            cycles,
            time_window_s=time_window_s,
        )
        grids.append(grid)
        measured.append(measured_grid)
        references.append(reference_grid)
    grid = grids[0]
    measured_array = np.vstack(measured)
    reference_array = np.vstack(references)
    mean_measured = np.mean(measured_array, axis=0)
    std_measured = np.std(measured_array, axis=0)
    normalized_measured = _zscore_rows(measured_array)
    normalized_reference = _zscore_rows(reference_array)
    mean_normalized_measured = np.mean(normalized_measured, axis=0)
    std_normalized_measured = np.std(normalized_measured, axis=0)
    mean_normalized_reference = np.mean(normalized_reference, axis=0)
    time_ms = grid * 1e3

    fig, axes = new_figure(nrows=3, ncols=1, kind="wide", constrained_layout=True)
    axes[0].plot(time_ms, reference_array[0], label="Expected Z coil voltage", lw=1.5)
    axes[0].plot(time_ms, mean_measured, label="Measured CH3 mean", lw=1.0)
    axes[0].fill_between(
        time_ms,
        mean_measured - std_measured,
        mean_measured + std_measured,
        color="C1",
        alpha=0.2,
        label="Measured +/- 1 std",
    )
    format_axis(axes[0], xlabel="Time from C4 falling edge (ms)", ylabel="Voltage (V)")
    axes[0].legend(loc="best")
    axes[0].grid(True, alpha=0.25)

    for index, row in enumerate(measured_array):
        axes[1].plot(time_ms, row, lw=0.8, alpha=0.7, label=f"Capture {index + 1}")
    axes[1].plot(time_ms, reference_array[0], color="black", lw=1.3, label="Expected")
    format_axis(axes[1], xlabel="Time from C4 falling edge (ms)", ylabel="Voltage (V)")
    axes[1].legend(loc="best", ncol=3)
    axes[1].grid(True, alpha=0.25)

    for index, row in enumerate(normalized_measured):
        axes[2].plot(
            time_ms,
            row,
            lw=0.8,
            alpha=0.45,
            label="Measured normalized" if index == 0 else None,
        )
    axes[2].plot(
        time_ms,
        mean_normalized_measured,
        color="C1",
        lw=1.2,
        label="Measured normalized mean",
    )
    axes[2].fill_between(
        time_ms,
        mean_normalized_measured - std_normalized_measured,
        mean_normalized_measured + std_normalized_measured,
        color="C1",
        alpha=0.2,
        label="Measured normalized +/- 1 std",
    )
    axes[2].plot(
        time_ms,
        mean_normalized_reference,
        color="black",
        lw=1.3,
        label="Expected normalized",
    )
    format_axis(
        axes[2],
        xlabel="Time from C4 falling edge (ms)",
        ylabel="Z-score amplitude",
    )
    axes[2].legend(loc="best", ncol=2)
    axes[2].grid(True, alpha=0.25)
    filename = "waveform_similarity.png"
    save_figure(fig, results_dir / filename)
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    """读取指定运行目录并生成波形相似度报告。"""
    run_dir = Path(run_dir).resolve()
    config = _load_yaml(run_dir / "experiment_config.yaml")
    expected = _load_expected(run_dir / "raw" / "applied_control_waveform.npz")
    capture_paths = sorted((run_dir / "raw").glob("scope_capture_*.npz"))
    if not capture_paths:
        raise FileNotFoundError(f"未找到 SDS 采集文件: {run_dir / 'raw'}")

    parameters = config.get("parameters", {})
    trigger_level = float(parameters.get("SCOPE_TRIGGER_LEVEL_V", 2.5))
    cycles = int(parameters.get("SCOPE_CYCLES", 3))
    period_s = 1.0 / float(expected["repeat_frequency_hz"])
    records: list[dict[str, Any]] = []
    aligned_arrays: list[np.ndarray] = []
    expected_arrays: list[np.ndarray] = []
    common_time: np.ndarray | None = None
    for path in capture_paths:
        capture = _load_capture(path)
        trigger_edge_time = _falling_edge_time(
            capture["trigger_time_s"],
            capture["trigger_voltage_v"],
            trigger_level,
        )
        time_relative = capture["time_s"] - trigger_edge_time
        alignment = _periodic_alignment(
            time_relative,
            capture["measured_voltage_v"],
            expected,
            scale_v_div=capture.get("scale_used_v_div"),
            offset_v=float(capture.get("offset_used_v", 0.0)),
            vertical_divisions=int(parameters.get("SCOPE_VERTICAL_DIVISIONS", 8)),
        )
        alignment["file"] = str(path.relative_to(run_dir))
        alignment["trigger_edge_time_s"] = trigger_edge_time
        alignment["trigger_to_first_sample_s"] = float(time_relative[0])
        alignment["ch4_to_ch3_delay_s"] = float(alignment["best_delay_s"])
        alignment["ch4_to_ch3_sample_origin_s"] = float(
            trigger_edge_time - capture["time_s"][0]
        )
        records.append(alignment)

    plot_window = _common_plot_window(records, period_s, cycles)
    for alignment in records:
        grid, measured_grid, expected_grid = _resample_for_plot(
            alignment,
            expected,
            period_s,
            cycles,
            time_window_s=plot_window,
        )
        common_time = grid
        aligned_arrays.append(measured_grid)
        expected_arrays.append(expected_grid)

    metrics = [
        {
            key: value
            for key, value in record.items()
            if key
            not in {
                "time_s",
                "measured_voltage_v",
                "expected_voltage_v",
                "aligned_expected_voltage_v",
            }
        }
        for record in records
    ]
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
        "saturation_fraction",
        "saturation_max_edge_run_samples",
        "scope_edge_fraction",
        "ch4_to_ch3_delay_s",
        "ch4_to_ch3_sample_origin_s",
    )
    summary: dict[str, dict[str, float]] = {}
    for name in metric_names:
        values = np.asarray([float(item[name]) for item in metrics], dtype=float)
        summary[name] = {
            "mean": float(np.nanmean(values)),
            "std": float(np.nanstd(values)),
            "minimum": float(np.nanmin(values)),
            "maximum": float(np.nanmax(values)),
        }

    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    plot_file = _plot_results(
        results_dir,
        expected,
        records,
        cycles=cycles,
        time_window_s=plot_window,
    )
    if common_time is None:
        raise RuntimeError("没有可绘制的对齐波形")
    measured_array = np.vstack(aligned_arrays)
    expected_array = np.vstack(expected_arrays)
    np.savez(
        results_dir / "aligned_waveforms.npz",
        time_relative_s=common_time,
        measured_voltage_v=measured_array,
        expected_voltage_v=expected_array,
        measured_voltage_normalized=_zscore_rows(measured_array),
        expected_voltage_normalized=_zscore_rows(expected_array),
        shape_correlation=np.asarray(
            [item["shape_correlation"] for item in records], dtype=float
        ),
        shape_nrmse=np.asarray(
            [item["shape_nrmse"] for item in records], dtype=float
        ),
        best_delay_s=np.asarray(
            [item["best_delay_s"] for item in records], dtype=float
        ),
    )
    np.savez(
        results_dir / "similarity_diagnostics.npz",
        shape_correlation=np.asarray(
            [item["shape_correlation"] for item in records], dtype=float
        ),
        shape_nrmse=np.asarray(
            [item["shape_nrmse"] for item in records], dtype=float
        ),
        best_delay_s=np.asarray(
            [item["best_delay_s"] for item in records], dtype=float
        ),
        affine_gain=np.asarray([item["affine_gain"] for item in records], dtype=float),
        affine_offset_v=np.asarray(
            [item["affine_offset_v"] for item in records], dtype=float
        ),
        measured_peak_to_peak_v=np.asarray(
            [item["measured_peak_to_peak_v"] for item in records], dtype=float
        ),
        expected_peak_to_peak_v=np.asarray(
            [item["expected_peak_to_peak_v"] for item in records], dtype=float
        ),
        range_ratio=np.asarray([item["range_ratio"] for item in records], dtype=float),
        saturation_fraction=np.asarray(
            [item["saturation_fraction"] for item in records], dtype=float
        ),
        saturation_detected=np.asarray(
            [item["saturation_detected"] for item in records], dtype=bool
        ),
        trigger_edge_time_s=np.asarray(
            [item["trigger_edge_time_s"] for item in records], dtype=float
        ),
    )

    result = {
        "experiment_id": EXPERIMENT_ID,
        "run_dir": str(run_dir),
        "capture_count": len(records),
        "expected_repeat_frequency_hz": float(expected["repeat_frequency_hz"]),
        "expected_period_s": period_s,
        "scope_cycles": cycles,
        "trigger": {
            "channel": int(parameters.get("SCOPE_TRIGGER_CHANNEL", 4)),
            "slope": "FALLing",
            "level_v": trigger_level,
            "edge_times_s": [float(item["trigger_edge_time_s"]) for item in records],
            "ch4_to_ch3_delay_s": [
                float(item["ch4_to_ch3_delay_s"]) for item in records
            ],
            "ch4_to_ch3_sample_origin_s": [
                float(item["ch4_to_ch3_sample_origin_s"]) for item in records
            ],
        },
        "ch4_to_ch3_fixed_delay": summary["ch4_to_ch3_delay_s"],
        "similarity_summary": summary,
        "captures": metrics,
        "threshold_evaluation": None,
        "files": [
            plot_file,
            "aligned_waveforms.npz",
            "similarity_diagnostics.npz",
            "similarity_report.yaml",
            "similarity_report.json",
        ],
    }
    (results_dir / "similarity_report.yaml").write_text(
        yaml.safe_dump(_builtin(result), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (results_dir / "similarity_report.json").write_text(
        json.dumps(_builtin(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> int:
    result = analyze(runtime_run_dir())
    print(f"Z 任意波线圈波形一致性分析完成: {result['run_dir']}")
    print(
        "形状相关系数: "
        f"{result['similarity_summary']['shape_correlation']['mean']:.6f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
