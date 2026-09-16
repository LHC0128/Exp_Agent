"""静态闭环离线分析；历史运行按旧快照分派到独立读取层。"""
from pathlib import Path

import numpy as np
import yaml

from ...plotting import format_axis, new_figure, save_figure, set_plot_style


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
    summary = yaml.safe_load((results / "iteration_summary.yaml").read_text(encoding="utf-8"))
    report = {"success": bool(summary), "target_reached": config["target_reached"],
              "stop_reason": config["stop_reason"], "completion_status": config["completion_status"],
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
    summary = yaml.safe_load((results / "iteration_summary.yaml").read_text(encoding="utf-8"))
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


def main() -> int:
    from ...experiment_runtime import runtime_run_dir
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
