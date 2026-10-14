"""各控制版本 A11 的 Z RF 相位响应离线对比。"""
from pathlib import Path
import numpy as np
from ...experiment_runtime import check_cancelled
from ...plotting import plot_style_context, new_figure, save_figure, style_legend


def control_legend(axis, **kwargs):
    legend = style_legend(axis, **kwargs)
    for text in legend.get_texts():
        text.set_fontfamily(["Arial", "Microsoft YaHei"])


def load_phase_scan(directory, config):
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


def plot_phase_responses(results, scans):
    with plot_style_context("paper"):
        figure, axes = new_figure(nrows=1, ncols=3, width_mm=177.8, height_mm=65)
        for version, data in scans.items():
            check_cancelled()
            for axis, component in zip(axes, ("X", "Y", "R")):
                axis.plot(data["scanned_phase_deg"], data[f"{component.lower()}_mean_v"], ".-", label=version)
        for axis, component in zip(axes, ("X", "Y", "R")):
            axis.set(xlabel="Z RF phase (deg)", ylabel=f"RF demodulator {component} (V)",
                title=f"{component} phase response", xlim=(0, 360), xticks=(0, 90, 180, 270, 360))
        handles, labels = axes[0].get_legend_handles_labels()
        figure.set_layout_engine("tight", rect=(0, 0, 1, 0.87))
        control_legend(figure, handles=handles, labels=labels, loc="upper center",
            bbox_to_anchor=(0.5, 0.99), ncol=len(scans))
        save_figure(figure, Path(results) / "z_phase_response_comparison.png")
