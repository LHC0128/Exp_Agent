"""Z 任意波闭环校正结果离线分析。"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...plotting import format_axis, new_figure, save_figure, set_plot_style


def _comparison_metrics(target: np.ndarray, measured: np.ndarray) -> dict[str, float]:
    """计算能同时反映波形形状和绝对输出幅值的误差指标。"""
    target = np.asarray(target, dtype=float).reshape(-1)
    measured = np.asarray(measured, dtype=float).reshape(-1)
    if target.size < 2 or target.size != measured.size:
        raise ValueError("target 与 measured 必须是长度相同且至少含两个点的数组")
    if not np.all(np.isfinite(target)) or not np.all(np.isfinite(measured)):
        raise ValueError("target 或 measured 包含 NaN/无穷值")
    error = measured - target
    target_std = max(float(np.std(target)), 1e-15)
    target_peak = float(np.max(np.abs(target)))
    measured_peak = float(np.max(np.abs(measured)))
    target_rms = float(np.sqrt(np.mean(target * target)))
    measured_rms = float(np.sqrt(np.mean(measured * measured)))
    if target_std > 1e-15 and np.std(measured) > 1e-15:
        correlation = float(np.corrcoef(target, measured)[0, 1])
    else:
        correlation = float("nan")
    return {
        "shape_nrmse": float(np.sqrt(np.mean(error * error)) / target_std),
        "shape_correlation": correlation,
        "bias_a": float(np.mean(error)),
        "mae_a": float(np.mean(np.abs(error))),
        "rmse_a": float(np.sqrt(np.mean(error * error))),
        "max_abs_error_a": float(np.max(np.abs(error))),
        "target_peak_abs_a": target_peak,
        "measured_peak_abs_a": measured_peak,
        "peak_ratio": measured_peak / target_peak if target_peak > 1e-15 else float("nan"),
        "target_rms_a": target_rms,
        "measured_rms_a": measured_rms,
        "rms_ratio": measured_rms / target_rms if target_rms > 1e-15 else float("nan"),
    }


def _load_feedback(run_dir: Path, iteration: int) -> dict[str, np.ndarray] | None:
    """读取某轮闭环工作流保存的对齐反馈数组。"""
    path = run_dir / "raw" / f"iteration_{iteration:03d}" / "feedback.npz"
    if not path.is_file():
        return None
    with np.load(path, allow_pickle=False) as data:
        required = ("time_s", "target_current_a", "measured_current_a")
        if any(name not in data.files for name in required):
            raise ValueError(f"反馈文件缺少必要数组: {path}")
        result = {name: np.asarray(data[name], dtype=float) for name in data.files}
    return result


def _align_periodic_phase(
    target: np.ndarray,
    measured: np.ndarray,
) -> tuple[np.ndarray, int, float]:
    """按整周期循环相关将 measured 移到 target 相位，并返回移位和相关系数。"""
    target_values = np.asarray(target, dtype=float).reshape(-1)
    measured_values = np.asarray(measured, dtype=float).reshape(-1)
    if target_values.size != measured_values.size or target_values.size < 2:
        raise ValueError("周期相位对齐要求 target/measured 长度一致")
    target_centered = target_values - float(np.mean(target_values))
    measured_centered = measured_values - float(np.mean(measured_values))
    correlation = np.fft.ifft(
        np.conj(np.fft.fft(target_centered)) * np.fft.fft(measured_centered)
    ).real
    shift = int(np.argmax(correlation))
    aligned = np.roll(measured_values, -shift)
    corr = float(np.corrcoef(target_values, aligned)[0, 1])
    return aligned, shift, corr


def analyze(run_dir: Path) -> dict[str, Any]:
    """生成迭代收敛、实际电流对比和绝对幅值误差诊断。"""
    run_dir = Path(run_dir).resolve()
    results_dir = run_dir / "results"
    summary_path = results_dir / "iteration_summary.yaml"
    waveform_path = results_dir / "corrected_control_waveform.npz"
    if not summary_path.is_file() or not waveform_path.is_file():
        raise FileNotFoundError("闭环校正结果缺少 iteration_summary 或 corrected_control_waveform")
    with summary_path.open(encoding="utf-8") as stream:
        summary = yaml.safe_load(stream) or []
    with np.load(waveform_path, allow_pickle=False) as data:
        time_s = np.asarray(data["time_s"], dtype=float)
        target_current = np.asarray(data["target_current_a"], dtype=float)
        command_voltage = np.asarray(data["voltage_v"], dtype=float)
    set_plot_style("paper")
    figure, axes = new_figure(nrows=3, ncols=1, kind="wide", constrained_layout=True)
    iterations = np.asarray([item["iteration"] for item in summary], dtype=float)
    errors = np.asarray([item["shape_nrmse"] for item in summary], dtype=float)
    holdout_errors = np.asarray(
        [item.get("holdout", {}).get("shape_nrmse", np.nan) for item in summary],
        dtype=float,
    )
    axes[0].plot(iterations, errors, "o-", label="Iteration samples (saved)")
    axes[0].plot(iterations, holdout_errors, "s--", label="Hold-out samples (saved)")
    format_axis(axes[0], xlabel="Iteration", ylabel="Current shape NRMSE")
    axes[0].grid(True, alpha=0.25)
    axes[0].legend(loc="best")
    axes[1].plot(time_s * 1e6, target_current, label="Target current")
    format_axis(axes[1], xlabel="Time (us)", ylabel="Target current (A)")
    axes[1].grid(True, alpha=0.25)
    axes[1].legend(loc="best")
    axes[2].plot(time_s * 1e6, command_voltage, color="C1", label="Command voltage")
    format_axis(axes[2], xlabel="Time (us)", ylabel="DG command (V)")
    axes[2].grid(True, alpha=0.25)
    axes[2].legend(loc="best")
    filename = "convergence.png"
    save_figure(figure, results_dir / filename)
    selection_errors = holdout_errors if np.any(np.isfinite(holdout_errors)) else errors
    best_index = int(np.nanargmin(selection_errors)) if selection_errors.size else -1
    best_iteration = int(iterations[best_index]) if best_index >= 0 else -1
    per_iteration: list[dict[str, Any]] = []
    feedback_by_iteration: dict[int, dict[str, np.ndarray]] = {}
    for index, item in enumerate(summary):
        iteration = int(item.get("iteration", index))
        feedback = _load_feedback(run_dir, iteration)
        record: dict[str, Any] = {"iteration": iteration}
        if feedback is not None:
            target = feedback["target_current_a"]
            measured = feedback["measured_current_a"]
            aligned_measured, shift, corr = _align_periodic_phase(target, measured)
            record["phase_alignment"] = {
                "feedback_shift_samples": int(shift),
                "feedback_shift_fraction_period": float(shift / target.size),
                "feedback_correlation_after_alignment": corr,
            }
            feedback = dict(feedback)
            feedback["measured_current_a_aligned"] = aligned_measured
            record["feedback"] = _comparison_metrics(target, aligned_measured)
            if "holdout_current_a" in feedback:
                aligned_holdout, hold_shift, hold_corr = _align_periodic_phase(
                    target, feedback["holdout_current_a"]
                )
                feedback["holdout_current_a_aligned"] = aligned_holdout
                record["holdout"] = _comparison_metrics(target, aligned_holdout)
                record["phase_alignment"].update(
                    {
                        "holdout_shift_samples": int(hold_shift),
                        "holdout_shift_fraction_period": float(hold_shift / target.size),
                        "holdout_correlation_after_alignment": hold_corr,
                    }
                )
            feedback_by_iteration[iteration] = feedback
        else:
            record["feedback"] = None
        per_iteration.append(record)

    # 使用相位对齐后的指标重画收敛曲线；原始 summary 仍保留，便于追溯采集时的错相位情况。
    errors = np.asarray(
        [
            (item.get("feedback") or {}).get("shape_nrmse", np.nan)
            for item in per_iteration
        ],
        dtype=float,
    )
    holdout_errors = np.asarray(
        [
            (item.get("holdout") or {}).get("shape_nrmse", np.nan)
            for item in per_iteration
        ],
        dtype=float,
    )
    set_plot_style("paper")
    figure, axes = new_figure(nrows=3, ncols=1, kind="wide", constrained_layout=True)
    axes[0].plot(iterations, errors, "o-", label="Iteration samples (phase aligned)")
    axes[0].plot(iterations, holdout_errors, "s--", label="Hold-out samples (phase aligned)")
    format_axis(axes[0], xlabel="Iteration", ylabel="Current shape NRMSE")
    axes[0].grid(True, alpha=0.25)
    axes[0].legend(loc="best")
    axes[1].plot(time_s * 1e6, target_current, label="Target current")
    format_axis(axes[1], xlabel="Time (us)", ylabel="Target current (A)")
    axes[1].grid(True, alpha=0.25)
    axes[1].legend(loc="best")
    axes[2].plot(time_s * 1e6, command_voltage, color="C1", label="Command voltage")
    format_axis(axes[2], xlabel="Time (us)", ylabel="DG command (V)")
    axes[2].grid(True, alpha=0.25)
    axes[2].legend(loc="best")
    save_figure(figure, results_dir / filename)

    selection_errors = holdout_errors if np.any(np.isfinite(holdout_errors)) else errors
    best_index = int(np.nanargmin(selection_errors)) if selection_errors.size else -1
    best_iteration = int(iterations[best_index]) if best_index >= 0 else -1

    comparison_file = "waveform_comparison.csv"
    comparison_figure = "waveform_comparison.png"
    best_feedback = feedback_by_iteration.get(best_iteration)
    if best_feedback is not None:
        comparison_target = best_feedback["target_current_a"]
        comparison_measured = best_feedback.get(
            "measured_current_a_aligned", best_feedback["measured_current_a"]
        )
        comparison_time = best_feedback["time_s"]
        comparison_holdout = best_feedback.get("holdout_current_a_aligned")
        with (results_dir / comparison_file).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            columns = ["time_s", "target_current_a", "measured_current_a", "error_current_a"]
            if comparison_holdout is not None:
                columns.extend(["holdout_current_a", "holdout_error_current_a"])
            writer.writerow(columns)
            for index in range(comparison_time.size):
                row = [
                    float(comparison_time[index]),
                    float(comparison_target[index]),
                    float(comparison_measured[index]),
                    float(comparison_measured[index] - comparison_target[index]),
                ]
                if comparison_holdout is not None:
                    row.extend(
                        [
                            float(comparison_holdout[index]),
                            float(comparison_holdout[index] - comparison_target[index]),
                        ]
                    )
                writer.writerow(row)

        set_plot_style("paper")
        figure, axes = new_figure(nrows=3, ncols=1, kind="wide", constrained_layout=True)
        time_us = comparison_time * 1e6
        axes[0].plot(time_us, comparison_target, label="Target current")
        axes[0].plot(time_us, comparison_measured, label="Measured current")
        if comparison_holdout is not None:
            axes[0].plot(time_us, comparison_holdout, "--", label="Hold-out current")
        format_axis(axes[0], xlabel="Time (us)", ylabel="Current (A)")
        axes[0].set_title(f"Best iteration {best_iteration}: target vs measured current")
        axes[0].grid(True, alpha=0.25)
        axes[0].legend(loc="best")
        axes[1].plot(time_us, comparison_measured - comparison_target, label="Feedback error")
        if comparison_holdout is not None:
            axes[1].plot(time_us, comparison_holdout - comparison_target, label="Hold-out error")
        axes[1].axhline(0.0, color="black", linewidth=0.8)
        format_axis(axes[1], xlabel="Time (us)", ylabel="Error (A)")
        axes[1].grid(True, alpha=0.25)
        axes[1].legend(loc="best")
        target_ma = 1e3 * comparison_target
        measured_ma = 1e3 * comparison_measured
        axes[2].plot(time_us, target_ma, label="Target current")
        axes[2].plot(time_us, measured_ma, label="Measured current")
        format_axis(axes[2], xlabel="Time (us)", ylabel="Current (mA)")
        axes[2].set_ylim(
            float(min(np.min(target_ma), np.min(measured_ma))),
            float(max(np.max(target_ma), np.max(measured_ma))),
        )
        axes[2].grid(True, alpha=0.25)
        axes[2].legend(loc="best")
        save_figure(figure, results_dir / comparison_figure)

    best_record = next(
        (item for item in per_iteration if item["iteration"] == best_iteration),
        {"feedback": None},
    )
    report = {
        "iteration_count": len(summary),
        "best_iteration": best_iteration,
        "best_shape_nrmse": float(errors[best_index]) if errors.size else float("nan"),
        "last_shape_nrmse": float(errors[-1]) if errors.size else float("nan"),
        "best_holdout_shape_nrmse": (
            float(holdout_errors[best_index])
            if best_index >= 0 and np.isfinite(holdout_errors[best_index])
            else float("nan")
        ),
        "best_feedback_comparison": best_record.get("feedback"),
        "best_holdout_comparison": best_record.get("holdout"),
        "per_iteration_comparison": per_iteration,
        "files": [
            filename,
            comparison_figure,
            comparison_file,
            "corrected_control_waveform.npz",
            "corrected_control_waveform.csv",
        ] if best_feedback is not None else [
            filename,
            "corrected_control_waveform.npz",
            "corrected_control_waveform.csv",
        ],
    }
    (results_dir / "final_waveform_report.yaml").write_text(yaml.safe_dump(report, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return report


def main() -> int:
    from ...experiment_runtime import runtime_run_dir

    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
