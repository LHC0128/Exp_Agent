"""逐控制复用 DirectAW 分析，聚合谱图不混用斜率。"""
import re
import json
import numpy as np
import yaml
from ...experiment_runtime import check_cancelled
from ...plotting import plot_style_context, new_figure, save_figure
from ..rf_sensitivity_direct_aw import analysis_steps as shared
from . import run_format


def control_inputs(directory, config):
    seen = set()
    for relative in config["parameters"]["CONTROL_WAVEFORM_FILES"]:
        if not re.fullmatch(r"v[1-9]\d*/waveforms/optimal_control_waveform\.csv", relative):
            raise ValueError("控制文件快照路径无效")
        version = relative.split("/")[0]
        if version in seen:
            raise ValueError("运行快照包含重复控制版本")
        seen.add(version)
        child = (directory / "controls" / version).resolve()
        if directory.resolve() not in child.parents:
            raise ValueError("控制子运行超出运行目录")
        child_config = yaml.safe_load((child / "experiment_config.yaml").read_text(encoding="utf-8"))
        if reason := run_format.analysis_block_reason(child_config):
            raise ValueError(f"{version}：{reason}")
        if child_config["parameters"]["ARB_WAVEFORM_FILE"] != relative:
            raise ValueError("控制子运行与父运行来源不一致")
        from .stages import NOISE
        if any(child_config["parameters"].get(key) != config["parameters"].get(key) for key in (*NOISE, "Z_RF_FREQ")):
            raise ValueError("控制子运行的噪声配置或 RF 载频与本轮不一致")
        yield version, child, child_config
    if not seen:
        raise ValueError("运行快照未选择控制版本")


def load_inputs(directory, config):
    for _, child, child_config in control_inputs(directory, config):
        check_cancelled()
        shared.load_inputs(child, child_config)


def response_fit(directory, config, *, component="X"):
    fits = {}
    for version, child, child_config in control_inputs(directory, config):
        check_cancelled()
        fits[version] = shared.response_fit(child, child_config, component=component)
    (directory / "results/response_fits.json").write_text(json.dumps(fits, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return fits


def noise_psd(directory, config, nperseg):
    results = {}
    for version, child, child_config in control_inputs(directory, config):
        check_cancelled()
        results[version] = shared.noise_psd(child, child_config, nperseg)
    (directory / "results/psd_index.json").write_text(json.dumps(results, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return results


def sensitivity_and_plots(directory, config, nperseg, *, identity=run_format):
    analyses, curves, arrays = {}, {}, {}
    for version, child, child_config in control_inputs(directory, config):
        check_cancelled()
        analyses[version] = shared.sensitivity_and_plots(child, child_config, nperseg, identity=identity)
        analyses[version]["control_metrics"] = child_config.get("control_metrics", {})
        with np.load(child / "results/sensitivity.npz", allow_pickle=False) as data:
            curves[version] = {name: data[name].copy() for name in ("freq_Hz", "sensitivity_fT_per_sqrtHz", "psd_avg_V2_per_Hz")}
            arrays.update({version + "__" + name: data[name] for name in data.files})
    output = directory / "results"
    np.savez(output / "sensitivity.npz", **arrays)
    with plot_style_context("paper"):
        figure, axes = new_figure(nrows=1, ncols=3, width_mm=177.8, height_mm=65)
        for version, child, child_config in control_inputs(directory, config):
            with np.load(child / "results/response_fit.npz", allow_pickle=False) as data:
                axes[0].plot(data["amplitudes_nT"], data["demod3_x_V"], ".-", label=version)
            data = curves[version]
            positive = data["freq_Hz"] > 0
            axes[1].loglog(data["freq_Hz"][positive], data["psd_avg_V2_per_Hz"][positive], label=version)
            axes[2].loglog(data["freq_Hz"][positive], data["sensitivity_fT_per_sqrtHz"][positive], label=version)
        labels = (("RF field (nT)", "RF demodulator X (V)", "RF response"),
            ("Baseband frequency (Hz)", "PSD (V^2/Hz)", "Noise ON"),
            ("Baseband frequency (Hz)", "Sensitivity (fT/sqrt(Hz))", "RF sensitivity"))
        for axis, (x, y, title) in zip(axes, labels):
            axis.set(xlabel=x, ylabel=y, title=title)
            axis.legend()
        save_figure(figure, output / "full_analysis.png")
        save_figure(figure, output / "full_analysis.pdf")
    summary = {"experiment_id": identity.EXPERIMENT_ID, "controls": analyses,
        "noise_state": "ON", "frequency_axis": "rf_demodulator_baseband_Hz"}
    (output / "analysis.json").write_text(json.dumps(summary, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    (output / "analysis.yaml").write_text(yaml.safe_dump(summary, allow_unicode=True), encoding="utf-8")
    return summary
