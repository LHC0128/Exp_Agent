"""静态闭环离线分析；历史运行按旧快照分派到独立读取层。"""
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import yaml

from ...plotting import format_axis, new_figure, save_figure, set_plot_style


def analyze(run_dir: Path) -> dict:
    """直接绘制采集时保存的对齐反馈，不再次对齐或修改冻结命令。"""
    run_dir = Path(run_dir).resolve()
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    if config.get("correction_method") != "static_current_feedback":
        from .legacy_analysis import analyze as analyze_legacy
        return analyze_legacy(run_dir)
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
        plt.close(fig)
        fig, axes = new_figure(nrows=3, ncols=1, kind="wide", constrained_layout=True)
        axes[0].plot(time_s * 1e6, target * 1e3, label="Target")
        axes[0].plot(time_s * 1e6, measured * 1e3, label="Measured (aligned)")
        axes[0].legend()
        axes[1].plot(time_s * 1e6, (target - measured) * 1e3)
        axes[2].plot(time_s * 1e6, command)
        for axis, label in zip(axes, ("Current (mA)", "Error (mA)", "Command (V)")):
            format_axis(axis, xlabel="Time (us)", ylabel=label)
        save_figure(fig, results / "waveform_comparison.png")
        plt.close(fig)
        report["files"] = ["convergence.png", "waveform_comparison.png", "corrected_control_waveform.npz", "corrected_control_waveform.csv"]
    (results / "final_waveform_report.yaml").write_text(yaml.safe_dump(report, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return report


def main() -> int:
    from ...experiment_runtime import runtime_run_dir
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
