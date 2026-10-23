"""多控制的只读来源校验、独立分析和汇总；不连接仪器。"""
import csv
import json
import re
from pathlib import Path
import numpy as np
import yaml
from ...experiment_runtime import check_cancelled
from ...plotting import plot_style_context, new_figure, save_figure
from . import analysis_steps as shared, run_format
from .control_phase import load_phase_scan, plot_phase_responses, control_legend


def control_inputs(directory, config, *, identity=run_format):
    directory = Path(directory).resolve()
    if identity.EXPERIMENT_ID == run_format.EXPERIMENT_ID and not run_format.is_multi_control(config):
        yield None, directory, config
        return
    files = config["parameters"].get("CONTROL_WAVEFORM_FILES")
    if not isinstance(files, list) or not files:
        raise ValueError("运行快照未选择控制版本")
    seen = set()
    for relative in files:
        if not isinstance(relative, str) or not re.fullmatch(r"[^/\\\x00]+/waveforms/optimal_control_waveform\.csv", relative):
            raise ValueError("控制文件快照路径无效")
        version = relative.split("/")[0]
        if version in (".", "..") or version in seen:
            raise ValueError("运行快照包含无效或重复控制版本")
        seen.add(version)
        child = (directory / "controls" / version).resolve()
        if directory not in child.parents:
            raise ValueError("控制子运行超出运行目录")
        child_config = yaml.safe_load((child / "experiment_config.yaml").read_text(encoding="utf-8"))
        if reason := identity.analysis_block_reason(child_config):
            raise ValueError(f"{version}：{reason}")
        child_parameters = child_config["parameters"]
        if child_parameters.get("ARB_WAVEFORM_FILE") != relative or child_parameters.get("CONTROL_WAVEFORM_FILES") != [relative]:
            raise ValueError("控制子运行与父运行来源不一致")
        # 子运行只改变选择项和目录标签；其他物理参数、补偿和采集配置必须与父快照一致。
        for key, value in config["parameters"].items():
            if key not in ("ARB_WAVEFORM_FILE", "CONTROL_WAVEFORM_FILES", "RUN_TAG") and child_parameters.get(key) != value:
                label = f"RF 相位设置 {key}" if key in ("Z_PHASE_SCAN_ENABLED", "Z_BURST_PHASE_INIT") else key
                raise ValueError(f"{version}：控制子运行的 {label} 与本轮不一致")
        child_config["analysis_options"] = config.get("analysis_options", {})
        yield version, child, child_config


def load_inputs(directory, config, *, identity=run_format):
    for _, child, child_config in control_inputs(directory, config, identity=identity):
        check_cancelled()
        shared.load_inputs(child, child_config)
        load_phase_scan(child, child_config)


def response_fit(directory, config, *, component="X", identity=run_format):
    fits = {}
    for version, child, child_config in control_inputs(directory, config, identity=identity):
        check_cancelled()
        fits[version] = shared.response_fit(child, child_config, component=component)
    (directory / "results/response_fits.json").write_text(json.dumps(fits, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return fits


def noise_psd(directory, config, nperseg, *, identity=run_format):
    results, rows = {}, []
    for version, child, child_config in control_inputs(directory, config, identity=identity):
        check_cancelled()
        results[version] = shared.noise_psd(child, child_config, nperseg)
        with (child / "results/noise_record_quality.csv").open(newline="", encoding="utf-8-sig") as stream:
            rows.extend(dict(control=version, **row) for row in csv.DictReader(stream))
    output = directory / "results"
    (output / "psd_index.json").write_text(json.dumps(results, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    quality = dict(controls=results, n_records=sum(row["n_records"] for row in results.values()),
        n_accepted=sum(row["n_accepted"] for row in results.values()), n_rejected=sum(row["n_rejected"] for row in results.values()))
    (output / "noise_rejection_summary.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    with (output / "noise_record_quality.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return results


def sensitivity_and_plots(directory, config, nperseg, *, identity=run_format, save_plot=None):
    save_plot = save_plot or save_figure
    analyses, curves, arrays, scans = {}, {}, {}, {}
    inputs = list(control_inputs(directory, config, identity=identity))
    for index, (version, child, child_config) in enumerate(inputs, 1):
        check_cancelled()
        analyses[version] = shared.sensitivity_and_plots(child, child_config, nperseg, identity=identity)
        analyses[version]["control_metrics"] = child_config.get("control_metrics", {})
        analyses[version]["rf_phase_calibration"] = child_config.get("rf_phase_calibration", {})
        analyses[version]["plot_label"] = version if version.isascii() else f"Control {index}"
        phase_scan = load_phase_scan(child, child_config)
        if phase_scan is not None:
            scans[version] = phase_scan
        with np.load(child / "results/sensitivity.npz", allow_pickle=False) as data:
            curves[version] = {name: data[name].copy() for name in ("freq_Hz", "sensitivity_fT_per_sqrtHz", "psd_avg_V2_per_Hz")}
            arrays.update({version + "__" + name: data[name] for name in data.files})
    output = directory / "results"
    np.savez(output / "sensitivity.npz", **arrays)
    with plot_style_context("paper"):
        figure, axes = new_figure(nrows=1, ncols=2, width_mm=177.8, height_mm=65)
        for version, child, _ in inputs:
            label = analyses[version]["plot_label"]
            with np.load(child / "results/response_fit.npz", allow_pickle=False) as data:
                axes[0].plot(data["amplitudes_nT"], data["demod3_x_V"], ".-", label=label)
            data = curves[version]
            positive = data["freq_Hz"] > 0
            axes[1].loglog(data["freq_Hz"][positive], data["sensitivity_fT_per_sqrtHz"][positive], label=label)
        for axis, (x, y, title) in zip(axes, (("RF field (nT)", "RF demodulator X (V)", "RF response"),
                ("Baseband frequency (Hz)", "Sensitivity (fT/sqrt(Hz))", "RF sensitivity"))):
            axis.set(xlabel=x, ylabel=y, title=title)
            control_legend(axis)
        save_plot(figure, output / "full_analysis.png")
    if scans:
        plot_phase_responses(output, scans)
    quality = json.loads((output / "noise_rejection_summary.json").read_text(encoding="utf-8"))
    summary = dict(experiment_id=identity.EXPERIMENT_ID, controls=analyses, noise_rejection=quality,
        rf_phase_scan_status="completed" if scans else "skipped",
        noise_state="ON" if identity.EXPERIMENT_ID == "rf-sensitivity-aw-known-noise" else "OFF",
        frequency_axis="rf_demodulator_baseband_Hz")
    (output / "analysis.json").write_text(json.dumps(summary, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    (output / "analysis.yaml").write_text(yaml.safe_dump(summary, allow_unicode=True), encoding="utf-8")
    return summary
