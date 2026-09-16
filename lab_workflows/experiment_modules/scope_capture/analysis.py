"""完整时域记录的离线校验、Welch PSD 和显示数据。"""

from pathlib import Path
import json

import numpy as np
from scipy.signal import welch

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, runtime_run_dir
from ...plotting import format_axis, new_figure, save_figure, set_plot_style


def validate_waveform(time_s, voltage_v) -> tuple[np.ndarray, np.ndarray, float]:
    t = np.asarray(time_s, dtype=float)
    v = np.asarray(voltage_v, dtype=float)
    if t.ndim != 1 or v.ndim != 1 or t.size != v.size or t.size < 2:
        raise ValueError("时间轴与电压必须是一维、等长且至少两个点")
    if not np.all(np.isfinite(t)) or not np.all(np.isfinite(v)):
        raise ValueError("波形含非有限值")
    steps = np.diff(t)
    dt = float(np.median(steps))
    if dt <= 0 or not np.allclose(steps, dt, rtol=1e-5, atol=abs(dt) * 1e-7):
        raise ValueError("时间轴必须严格递增且等间隔，不能计算 PSD")
    return t, v, 1.0 / dt


def calculate_psd(voltage_v: np.ndarray, sample_rate: float):
    """单边功率谱密度；逐段去均值，固定周期 Hann 窗与半段重叠。"""
    v = np.asarray(voltage_v, dtype=float)
    if v.ndim != 1 or v.size < 2 or not np.all(np.isfinite(v)):
        raise ValueError("PSD 输入须为至少两个有限电压样本")
    if not np.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("PSD 实际采样率必须为正有限数")
    nperseg = min(10000, v.size)
    frequency, density = welch(
        v, fs=sample_rate, window="hann", nperseg=nperseg,
        noverlap=nperseg // 2, detrend="constant", scaling="density",
        return_onesided=True,
    )
    return frequency, density, {
        "method": "welch", "window": "hann", "detrend": "constant",
        "nperseg": nperseg, "noverlap": nperseg // 2, "unit": "V²/Hz",
        "sample_rate_sa_s": sample_rate, "frequency_resolution_hz": sample_rate / nperseg,
    }


def envelope_indices(values: np.ndarray, maximum: int = 6000) -> np.ndarray:
    """每桶保留极小、极大值及两端，避免稀疏抽点漏掉窄脉冲。"""
    if values.size <= maximum:
        return np.arange(values.size)
    indices = [0, values.size - 1]
    edges = np.linspace(0, values.size, (maximum - 2) // 2 + 1, dtype=int)
    for start, stop in zip(edges[:-1], edges[1:]):
        bucket = values[start:stop]
        indices.extend((int(start + np.argmin(bucket)), int(start + np.argmax(bucket))))
    return np.unique(indices)


def _json(path: Path, value) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def vertical_metadata(snapshots: list[dict], voltage: np.ndarray) -> dict:
    """优先使用实际读回，旧记录没有量程时保留未知状态。"""
    for snapshot in snapshots:
        inputs = snapshot.get("input_settings", {})
        scale = snapshot.get("actual_vertical_scale_v_div", inputs.get("scale"))
        offset = snapshot.get("actual_vertical_offset_v", inputs.get("offset"))
        try:
            scale, offset = float(scale), float(offset)
            divisions = float(snapshot.get("vertical_divisions", 8))
        except (TypeError, ValueError):
            continue
        if not all(np.isfinite(n) for n in (scale, offset, divisions)) or scale <= 0 or divisions <= 0:
            continue
        low, high = offset - scale * divisions / 2, offset + scale * divisions / 2
        over = (voltage < low) | (voltage > high)
        return {"vertical_scale_v_div": scale, "vertical_offset_v": offset,
                "vertical_range_min_v": low, "vertical_range_max_v": high,
                "overrange": bool(np.any(over)), "overrange_fraction": float(np.mean(over))}
    return dict.fromkeys(("vertical_scale_v_div", "vertical_offset_v", "vertical_range_min_v",
                          "vertical_range_max_v", "overrange", "overrange_fraction"))


def analyze(run_dir: Path) -> dict:
    check_cancelled()
    run_dir = Path(run_dir).resolve()
    with np.load(run_dir / "raw" / "waveform.npz", allow_pickle=False) as data:
        t, v, rate = validate_waveform(data["time_s"], data["voltage_v"])
        channel = int(data["channel"])
        requested_rate = float(data["requested_rate_sa_s"])
        requested_duration = float(data["requested_duration_s"])
        raw_snapshot = {}
        if "configuration_json" in data:
            try:
                raw_snapshot = json.loads(str(data["configuration_json"].item()))
            except (ValueError, TypeError, json.JSONDecodeError):
                raw_snapshot = {}
    config_path = run_dir / "experiment_config.yaml"
    config_data = {}
    if config_path.exists():
        import yaml
        config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    snapshots = [config_data.get("scope_configuration", {}), raw_snapshot]
    vertical = vertical_metadata([s for s in snapshots if isinstance(s, dict)], v)
    check_cancelled()
    frequency, density, metadata = calculate_psd(v, rate)
    check_cancelled()
    results = run_dir / "results"
    results.mkdir(exist_ok=True)
    np.savez(results / "psd.npz", frequency_hz=frequency, psd_v2_hz=density)
    _json(results / "psd.json", {**metadata, "frequency_hz": frequency.tolist(), "psd_v2_hz": density.tolist()})
    indices = envelope_indices(v)
    display = {
        "schema_version": 2, "run_id": run_dir.name, "channel": channel,
        "sample_count": int(v.size), "actual_rate_sa_s": rate,
        "actual_duration_s": float(t[-1] - t[0]), "requested_rate_sa_s": requested_rate,
        "requested_duration_s": requested_duration, "time_s": t[indices].tolist(),
        "voltage_v": v[indices].tolist(), "display_decimated": indices.size < v.size,
        **vertical,
        "psd": metadata,
    }
    set_plot_style("paper")
    check_cancelled()
    fig, axes = new_figure(nrows=2, ncols=1, kind="wide", constrained_layout=True)
    axes[0].plot(t[indices], v[indices], lw=0.8)
    range_min, range_max = vertical["vertical_range_min_v"], vertical["vertical_range_max_v"]
    if range_min is not None and range_max is not None:
        axes[0].axhline(range_min, color="0.45", ls="--", lw=0.8)
        axes[0].axhline(range_max, color="0.45", ls="--", lw=0.8)
        shown_t, shown_v = t[indices], v[indices]
        axes[0].fill_between(shown_t, range_max, np.maximum(shown_v, range_max), where=shown_v > range_max, interpolate=True, color="r", alpha=0.18)
        axes[0].fill_between(shown_t, range_min, np.minimum(shown_v, range_min), where=shown_v < range_min, interpolate=True, color="r", alpha=0.18)
        note = (f"{vertical['vertical_scale_v_div']:.4g} V/div | Offset: {vertical['vertical_offset_v']:.4g} V\n"
                f"Range: {range_min:.4g} to {range_max:.4g} V" + (" | OVERRANGE" if vertical["overrange"] else ""))
        axes[0].text(0.99, 0.98, note, transform=axes[0].transAxes, ha="right", va="top",
                     fontsize=9, color="red" if vertical["overrange"] else "0.35")
    format_axis(axes[0], xlabel="Time (s)", ylabel="Voltage (V)")
    axes[1].plot(frequency, density, lw=0.8)
    format_axis(axes[1], xlabel="Frequency (Hz)", ylabel="PSD (V²/Hz)")
    save_figure(fig, results / "waveform_psd.png")
    check_cancelled()
    # 最后发布显示文件；历史列表只把完整分析视为可绘制。
    _json(results / "display.json", display)
    return {"run_dir": str(run_dir), "sample_count": int(v.size)}


if __name__ == "__main__":
    try:
        result = analyze(runtime_run_dir())
        print(f"分析完成：{result['sample_count']} 点，结果位于 {result['run_dir']}")
    except WorkflowCancelled as exc:
        print(str(exc))
        raise SystemExit(130)
