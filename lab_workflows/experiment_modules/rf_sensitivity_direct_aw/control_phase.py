"""各控制版本 A11 的 Z RF 相位响应离线校验与对比。"""
from pathlib import Path
import numpy as np
from ...experiment_runtime import check_cancelled
from ...plotting import plot_style_context, new_figure, save_figure, style_legend
from .run_format import manual_rf_phase


def control_legend(axis, **kwargs):
    legend = style_legend(axis, **kwargs)
    for text in legend.get_texts():
        text.set_fontfamily(["Arial", "Microsoft YaHei"])


def load_phase_scan(directory, config):
    phase = manual_rf_phase(config)
    if phase is not None:
        with np.load(Path(directory) / "raw/response_data.npz", allow_pickle=False) as data:
            amplitudes, phases = np.asarray(data["amplitudes_V"]), np.asarray(data["z_burst_phase_deg"])
        expected = (phase + np.where(amplitudes < 0, 180, 0)) % 360
        if (amplitudes.ndim != 1 or phases.shape != amplitudes.shape or not np.isfinite(amplitudes).all()
                or not np.isfinite(phases).all() or not np.allclose(phases, expected, rtol=0, atol=1e-8)):
            raise ValueError("手动 RF 相位与实际响应采集相位不一致")
        return None
    path = Path(directory) / "raw/z_phase_scan.npz"
    if not path.is_file():
        raise ValueError(f"{Path(directory).name}：缺少 raw/z_phase_scan.npz 相位扫描")
    keys = ("scanned_phase_deg", "x_mean_v", "y_mean_v", "r_mean_v")
    with np.load(path, allow_pickle=False) as data:
        if any(key not in data for key in keys):
            raise ValueError(f"{Path(directory).name}：相位扫描缺少相位或 X/Y/R 响应")
        arrays = {key: np.asarray(data[key], dtype=float) for key in keys}
        if "demod_idx" in data and int(data["demod_idx"]) != int(config["parameters"].get("DEMOD3_IDX", 3)):
            raise ValueError(f"{Path(directory).name}：相位扫描的 RF 解调器索引与运行配置不一致")
    parameters = config["parameters"]
    expected = int(round((parameters["Z_PHASE_SCAN_STOP_DEG"] - parameters["Z_PHASE_SCAN_START_DEG"])
        / parameters["Z_PHASE_SCAN_STEP_DEG"])) + 1
    if expected < 2 or any(values.shape != (expected,) or not np.all(np.isfinite(values)) for values in arrays.values()):
        raise ValueError(f"{Path(directory).name}：相位扫描不完整或含非有限值")
    phase = np.mod(arrays["scanned_phase_deg"], 360.0)
    order = np.argsort(phase)
    arrays["scanned_phase_deg"] = phase
    return {key: values[order] for key, values in arrays.items()}


def plot_phase_responses(results, scans, *, save_plot=None):
    save_plot = save_plot or save_figure
    with plot_style_context("paper"):
        figure, axes = new_figure(nrows=1, ncols=3, width_mm=177.8, height_mm=65)
        for index, (version, data) in enumerate(scans.items(), 1):
            check_cancelled()
            label = version if version.isascii() else f"Control {index}"
            for axis, component in zip(axes, ("X", "Y", "R")):
                axis.plot(data["scanned_phase_deg"], data[f"{component.lower()}_mean_v"], ".-", label=label)
        for axis, component in zip(axes, ("X", "Y", "R")):
            axis.set(xlabel="Z RF phase (deg)", ylabel=f"RF demodulator {component} (V)",
                title=f"{component} phase response", xlim=(0, 360), xticks=(0, 90, 180, 270, 360))
        handles, labels = axes[0].get_legend_handles_labels()
        figure.set_layout_engine("tight", rect=(0, 0, 1, 0.87))
        control_legend(figure, handles=handles, labels=labels, loc="upper center",
            bbox_to_anchor=(0.5, 0.99), ncol=len(scans))
        save_plot(figure, Path(results) / "z_phase_response_comparison.png")
