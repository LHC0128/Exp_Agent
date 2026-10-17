"""示波器原始波形校验、Welch PSD、量程诊断与绘图职责。"""
from pathlib import Path
import json

import matplotlib
matplotlib.use("Agg")
import numpy as np
from scipy.signal import welch

from ...experiment_runtime import check_cancelled
from ...plotting import format_axis, new_figure, save_figure, set_plot_style
from .run_format import analysis_block_reason


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


def load_inputs(run_dir: Path) -> dict:
    """B01：校验运行身份后读取并校验原始波形与垂直量程元数据。"""
    check_cancelled()
    import yaml
    config_data: dict = {}
    config_path = run_dir / "experiment_config.yaml"
    if config_path.exists():
        config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if reason := analysis_block_reason(config_data):
        raise ValueError(reason)
    raw_path = run_dir / "raw" / "waveform.npz"
    if not raw_path.is_file():
        raise ValueError("缺少原始波形: raw/waveform.npz")
    with np.load(raw_path, allow_pickle=False) as data:
        required = ("time_s", "voltage_v", "channel", "requested_rate_sa_s", "requested_duration_s")
        missing = sorted(set(required) - set(data.files))
        if missing:
            raise ValueError("原始波形缺少字段：" + "、".join(missing))
        t, v, rate = validate_waveform(data["time_s"], data["voltage_v"])
        channel = data["channel"].item()
        requested_rate = float(data["requested_rate_sa_s"])
        requested_duration = float(data["requested_duration_s"])
        if type(channel) is not int or channel not in range(1, 5) or any(not np.isfinite(value) or value <= 0
                for value in (requested_rate, requested_duration)):
            raise ValueError("原始波形的通道、请求采样率或时长无效")
        raw_snapshot = {}
        if "configuration_json" in data:
            try:
                raw_snapshot = json.loads(str(data["configuration_json"].item()))
            except (ValueError, TypeError, json.JSONDecodeError):
                raw_snapshot = {}
    snapshots = [config_data.get("scope_configuration", {}), raw_snapshot]
    vertical = vertical_metadata([s for s in snapshots if isinstance(s, dict)], v)
    return {"time_s": t, "voltage_v": v, "actual_rate_sa_s": rate, "channel": channel,
            "requested_rate_sa_s": requested_rate, "requested_duration_s": requested_duration,
            "actual_duration_s": float(t[-1] - t[0]), "vertical": vertical}


def export_display(run_dir: Path, inputs: dict, frequency: np.ndarray, density: np.ndarray,
                   metadata: dict) -> dict:
    """B03：生成抽点显示数据、超量程诊断和 paper 风格结果图。"""
    check_cancelled()
    t, v = inputs["time_s"], inputs["voltage_v"]
    vertical = inputs["vertical"]
    results = run_dir / "results"
    indices = envelope_indices(v)
    display = {
        "schema_version": 2, "run_id": run_dir.name, "channel": inputs["channel"],
        "sample_count": int(v.size), "actual_rate_sa_s": inputs["actual_rate_sa_s"],
        "actual_duration_s": inputs["actual_duration_s"],
        "requested_rate_sa_s": inputs["requested_rate_sa_s"],
        "requested_duration_s": inputs["requested_duration_s"],
        "time_s": t[indices].tolist(), "voltage_v": v[indices].tolist(),
        "display_decimated": indices.size < v.size,
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
                     fontsize=8, color="red" if vertical["overrange"] else "0.35")
    format_axis(axes[0], xlabel="Time (s)", ylabel="Voltage (V)")
    axes[1].plot(frequency, density, lw=0.8)
    format_axis(axes[1], xlabel="Frequency (Hz)", ylabel="PSD (V²/Hz)")
    save_figure(fig, results / "waveform_psd.png")
    check_cancelled()
    # 最后发布显示文件；历史列表只把完整分析视为可绘制。
    _json(results / "display.json", display)
    return display
