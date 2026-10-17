"""静态闭环离线分析；历史运行按旧快照分派到独立读取层。"""
from pathlib import Path

import numpy as np
import yaml

from ...plotting import format_axis, new_figure, save_figure, set_plot_style
from . import run_format


def summary_path(run_dir):
    raw = run_dir / "raw/iteration_summary.yaml"
    return raw if raw.is_file() else run_dir / "results/iteration_summary.yaml"


def load_inputs(run_dir):
    """检查保存的科学输入；不加载外部来源，不写历史或重新评分。"""
    run_dir = Path(run_dir).resolve()
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    rows = yaml.safe_load(summary_path(run_dir).read_text(encoding="utf-8")) or []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("闭环轮次汇总必须为映射列表")
    for row in rows:
        score = row.get("shape_nrmse") if config.get("correction_method") is None else row.get("relative_rms_error")
        if not isinstance(score, (int, float)) or not np.isfinite(score) or score < 0:
            raise ValueError("闭环轮次误差必须为有限非负值")
    frozen = run_dir / "results/corrected_control_waveform.npz"
    if frozen.is_file():
        with np.load(frozen, allow_pickle=False) as data:
            names = ["time_s", "target_current_a", "voltage_v"]
            if config.get("correction_method") is not None:
                names.append("measured_current_a")
            values = [np.asarray(data[name], dtype=float) for name in names]
            time = values[0]
            if (time.ndim != 1 or time.size < 2 or np.any(np.diff(time) <= 0) or
                    any(value.shape != time.shape or not np.all(np.isfinite(value)) for value in values)):
                raise ValueError("冻结波形的时间轴、形状或数值无效")
            if config.get("correction_method") in run_format.METHODS:
                best = int(data["best_iteration"])
                name = str(data["measurement_path"]) if "measurement_path" in data.files else f"iteration_{best:03d}/feedback.npz"
                raw = (run_dir / "raw").resolve()
                feedback = (raw / name).resolve()
                if raw not in feedback.parents:
                    raise ValueError("保存的反馈路径必须位于本轮 raw 内")
                with np.load(feedback, allow_pickle=False) as frame:
                    band = frame["learning_band"]
                    if band.shape != (time.size // 2 + 1,) or band.dtype.kind != "b":
                        raise ValueError("保存的学习频带与波形网格不一致")
    elif rows and config.get("correction_method") not in run_format.METHODS:
        raise ValueError("历史闭环运行缺少已实测冻结波形")
    return {"experiment_id": run_format.EXPERIMENT_ID, "method": config.get("correction_method", "legacy"),
            "iteration_count": len(rows), "final_command_measured": frozen.is_file()}


def analyze(run_dir: Path) -> dict:
    """直接绘制采集时保存的对齐反馈，不再次对齐或修改冻结命令。"""
    run_dir = Path(run_dir).resolve()
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    method = config.get("correction_method")
    if method in {"response_filtered_feedback", "time_domain", "harmonic_jacobian"}:
        return _analyze_response_filtered(run_dir, config)
    if method != "static_current_feedback":
        from .legacy_analysis import analyze as analyze_legacy
        return analyze_legacy(run_dir)
    return _analyze_static(run_dir, config)


def _learning_band_hz(config: dict) -> float | None:
    """返回实际生效的学习频带上限；历史 schema v11 运行记录的是请求上限。"""
    response = config.get("current_frequency_response", {})
    value = response.get("effective_cutoff_hz")
    if value is None:
        return response.get("error_cutoff_hz") or config.get("parameters", {}).get("ERROR_CUTOFF_HZ")
    return value


def _analyze_response_filtered(run_dir: Path, config: dict) -> dict:
    """响应滤波闭环：绘制带内／带外误差拆分与主要谐波诊断。"""
    results = run_dir / "results"
    summary = yaml.safe_load(summary_path(run_dir).read_text(encoding="utf-8")) or []
    report = {"success": bool(summary), "target_reached": bool(config.get("target_reached")),
              "stop_reason": config.get("stop_reason", ""), "completion_status": config.get("completion_status", "unknown"),
              "iteration_count": len(summary), "metric": "RMS(target-measured)/RMS(target)",
              "independent_validation": bool(config.get("final_reload_validated")) or any("holdout_relative_rms_error" in row for row in summary), "files": [],
              "final_reload_validated": bool(config.get("final_reload_validated")),
              "learning_band": _learning_band_hz(config),
              "inverse_regularization": config.get("current_frequency_response", {}).get("inverse_regularization")}
    if summary and (results / "corrected_control_waveform.npz").is_file():
        with np.load(results / "corrected_control_waveform.npz", allow_pickle=False) as data:
            time_s, target, measured, command = [data[key] for key in
                ("time_s", "target_current_a", "measured_current_a", "voltage_v")]
            best = int(data["best_iteration"])
            score = float(data["relative_rms_error"])
            measurement_path = str(data["measurement_path"]) if "measurement_path" in data.files else f"iteration_{best:03d}/feedback.npz"
        report.update(best_iteration=best, best_relative_rms_error=score, final_command_measured=True,
                      target_error_threshold=config["parameters"]["TARGET_RELATIVE_RMS"])
        with np.load(run_dir / "raw" / measurement_path) as data:
            band = np.asarray(data["learning_band"], dtype=bool)
        target_rms = float(np.sqrt(np.mean(target ** 2)))
        iterations = [item["iteration"] + 1 for item in summary]
        set_plot_style("paper")
        fig, axis = new_figure(kind="wide", constrained_layout=True)
        axis.plot(iterations, [item["relative_rms_error"] for item in summary],
                  "o-", label="Measured error")
        axis.plot(iterations,
                  [item["in_band_error_rms_a"] / target_rms for item in summary],
                  "s--", label="In-band error")
        axis.plot(iterations,
                  [item["outside_band_error_rms_a"] / target_rms for item in summary],
                  "^:", label="Out-of-band error")
        axis.axhline(report["target_error_threshold"], ls="--", label="Threshold")
        axis.scatter([best + 1], [score], marker="*", s=80, label="Exported iteration")
        format_axis(axis, xlabel="Measurement round", ylabel="Relative RMS error")
        axis.legend()
        save_figure(fig, results / "convergence.png")
        spectrum = np.fft.rfft(target - measured)
        in_band = np.fft.irfft(spectrum * band, n=target.size)
        outside = np.fft.irfft(spectrum * (~band), n=target.size)
        fig, axes = new_figure(nrows=4, ncols=1, kind="wide", constrained_layout=True)
        axes[0].plot(time_s * 1e6, target * 1e3, label="Target")
        axes[0].plot(time_s * 1e6, measured * 1e3, label="Measured (CH4 reference)" if config.get("correction_method") == "harmonic_jacobian" else "Measured (aligned)")
        axes[0].legend()
        axes[1].plot(time_s * 1e6, (target - measured) * 1e3)
        axes[2].plot(time_s * 1e6, in_band * 1e3, label="In-band error")
        axes[2].plot(time_s * 1e6, outside * 1e3, label="Out-of-band error")
        axes[2].legend()
        axes[3].plot(time_s * 1e6, command)
        for axis, label in zip(axes, ("Current (mA)", "Error (mA)", "Band error (mA)", "Command (V)")):
            format_axis(axis, xlabel="Time (us)", ylabel=label)
        save_figure(fig, results / "waveform_comparison.png")
        report["files"] = ["convergence.png", "waveform_comparison.png",
                           "corrected_control_waveform.npz", "corrected_control_waveform.csv"]
    (results / "final_waveform_report.yaml").write_text(yaml.safe_dump(report, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return report


def _analyze_static(run_dir: Path, config: dict) -> dict:
    results = run_dir / "results"
    summary = yaml.safe_load(summary_path(run_dir).read_text(encoding="utf-8")) or []
    report = {"success": bool(summary), "target_reached": config["target_reached"],
              "stop_reason": config["stop_reason"], "completion_status": config["completion_status"],
              "iteration_count": len(summary), "metric": "RMS(target-measured)/RMS(target)",
              "independent_validation": False, "files": []}
    if summary:
        with np.load(results / "corrected_control_waveform.npz", allow_pickle=False) as data:
            time_s, target, measured, command = [data[key] for key in
                ("time_s", "target_current_a", "measured_current_a", "voltage_v")]
            best = int(data["best_iteration"])
            score = float(data["relative_rms_error"])
        report.update(best_iteration=best, best_relative_rms_error=score, final_command_measured=True,
                      target_error_threshold=config["parameters"]["TARGET_RELATIVE_RMS"])
        set_plot_style("paper")
        fig, axis = new_figure(kind="wide", constrained_layout=True)
        axis.plot([x["iteration"] + 1 for x in summary], [x["relative_rms_error"] for x in summary], "o-", label="Measured error")
        axis.axhline(report["target_error_threshold"], ls="--", label="Threshold")
        axis.scatter([best + 1], [score], marker="*", s=80, label="Exported iteration")
        format_axis(axis, xlabel="Measurement round", ylabel="Relative RMS error")
        axis.legend()
        save_figure(fig, results / "convergence.png")
        fig, axes = new_figure(nrows=3, ncols=1, kind="wide", constrained_layout=True)
        axes[0].plot(time_s * 1e6, target * 1e3, label="Target")
        axes[0].plot(time_s * 1e6, measured * 1e3, label="Measured (aligned)")
        axes[0].legend()
        axes[1].plot(time_s * 1e6, (target - measured) * 1e3)
        axes[2].plot(time_s * 1e6, command)
        for axis, label in zip(axes, ("Current (mA)", "Error (mA)", "Command (V)")):
            format_axis(axis, xlabel="Time (us)", ylabel=label)
        save_figure(fig, results / "waveform_comparison.png")
        report["files"] = ["convergence.png", "waveform_comparison.png", "corrected_control_waveform.npz", "corrected_control_waveform.csv"]
    (results / "final_waveform_report.yaml").write_text(yaml.safe_dump(report, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return report
