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
    config_path = run_dir / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    config = config or {}
    parameters = config.get("parameters", {})
    threshold = parameters.get("TARGET_SHAPE_NRMSE")
    learning_frequencies = config.get("current_frequency_response", {}).get("learning_frequencies_hz")
    full_time_domain = config.get("current_frequency_response", {}).get("learning_mode") == "full_time_domain"
    iterations = np.asarray([item["iteration"] for item in summary], dtype=int)
    filename = "convergence.png"
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
    figure, axes = new_figure(figsize=(7, 7), nrows=3, ncols=1, constrained_layout=True)
    axes[0].plot(iterations + 1, errors, "o-", label="Iteration samples (phase aligned)")
    axes[0].plot(iterations + 1, holdout_errors, "s--", label="Hold-out samples (phase aligned)")
    format_axis(axes[0], xlabel="Iteration (1-based)", ylabel="Current NRMSE")
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

    # 额外离线相位对齐仅用于诊断，不能重新挑选与冻结命令不一致的轮次。
    saved_errors = np.asarray([item.get("holdout", {}).get("shape_nrmse", item["shape_nrmse"]) for item in summary])
    finite = np.flatnonzero(np.isfinite(saved_errors))
    best_index = int(finite[np.argmin(saved_errors[finite])]) if finite.size else -1
    matches = [index for index, item in enumerate(summary)
               if item["iteration"] == config.get("best_iteration")]
    selection_basis = "saved_summary"
    if matches:
        best_index = matches[0]
        selection_basis = "saved_best_iteration"
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
        figure, axes = new_figure(figsize=(7, 7), nrows=3, ncols=1, constrained_layout=True)
        time_us = comparison_time * 1e6
        axes[0].plot(time_us, comparison_target * 1e3, label="Target current")
        axes[0].plot(time_us, comparison_measured * 1e3, label="Measured current")
        if comparison_holdout is not None:
            axes[0].plot(time_us, comparison_holdout * 1e3, "--", label="Hold-out current")
        format_axis(axes[0], xlabel="Time (us)", ylabel="Current (mA)")
        axes[0].set_title(f"Iteration {best_iteration + 1} (saved best): actual current amplitudes")
        axes[0].grid(True, alpha=0.25)
        axes[0].legend(loc="best")
        axes[1].plot(time_us, (comparison_measured - comparison_target) * 1e3, label="Feedback error")
        if comparison_holdout is not None:
            axes[1].plot(time_us, (comparison_holdout - comparison_target) * 1e3, label="Hold-out error")
        axes[1].axhline(0.0, color="black", linewidth=0.8)
        format_axis(axes[1], xlabel="Time (us)", ylabel="Error (mA)")
        axes[1].grid(True, alpha=0.25)
        axes[1].legend(loc="best")
        for values, label in [(comparison_target, "Target shape"), (comparison_measured, "Measured shape")]:
            normalized = (values - np.mean(values)) / max(float(np.std(values)), 1e-15)
            axes[2].plot(time_us, normalized, label=label)
        format_axis(axes[2], xlabel="Time (us)", ylabel="Centered current / RMS")
        axes[2].set_title("Shape only: independently normalized, not corrected measurements")
        axes[2].grid(True, alpha=0.25)
        axes[2].legend(loc="best")
        save_figure(figure, results_dir / comparison_figure)

    best_record = next(
        (item for item in per_iteration if item["iteration"] == best_iteration),
        {"feedback": None},
    )
    report = {
        "analysis_version": 3,
        "selection_basis": selection_basis,
        "target_nrmse_threshold": threshold,
        "target_reached": (
            bool(best_index >= 0 and summary[best_index].get("holdout")
                 and summary[best_index]["shape_nrmse"] <= threshold
                 and summary[best_index]["holdout"]["shape_nrmse"] <= threshold)
            if threshold is not None else None
        ),
        "acquisition_stop_reason": config.get("stop_reason"),
        "final_command_filter": config.get("final_command_filter"),
        "final_command_measured": config.get("final_command_measured", True),
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
    if best_feedback is not None:
        from .diagnostics import spectral_diagnostics

        measured = best_feedback.get("holdout_current_a_aligned", best_feedback["measured_current_a_aligned"])
        diagnosis = spectral_diagnostics(
            best_feedback["time_s"], best_feedback["target_current_a"], measured,
            best_feedback.get("command_voltage_v", command_voltage),
            learning_frequencies_hz=learning_frequencies,
            output_vpp=parameters.get("Z_AW_OUTPUT_VPP"),
            full_time_domain=full_time_domain,
        )
        report["diagnosis"] = diagnosis
        _write_diagnostic_outputs(results_dir, diagnosis, report, feedback_by_iteration)
        report["files"].extend(["amplitude_bandwidth_diagnosis.png", "diagnosis.md"])
    (results_dir / "final_waveform_report.yaml").write_text(yaml.safe_dump(report, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return report


def _write_diagnostic_outputs(
    results_dir: Path, diagnosis: dict[str, Any], report: dict[str, Any],
    feedback_by_iteration: dict[int, dict[str, np.ndarray]],
) -> None:
    """绘制谐波和幅值可达性估算，并保存中文诊断说明。"""
    harmonics = diagnosis["harmonics"][:5]
    figure, axes = new_figure(figsize=(7, 6.4), nrows=2, constrained_layout=True)
    x = np.arange(len(harmonics))
    axes[0].bar(x - 0.18, [item["target_amplitude_a"] * 1e3 for item in harmonics], 0.36, label="Target")
    axes[0].bar(x + 0.18, [item["measured_amplitude_a"] * 1e3 for item in harmonics], 0.36, label="Measured hold-out / feedback")
    axes[0].set_xticks(x, [f"{item['frequency_hz'] / 1e3:.0f}" for item in harmonics])
    format_axis(axes[0], xlabel="Frequency (kHz)", ylabel="Harmonic amplitude (mA)")
    axes[0].legend(loc="upper right")
    commands, currents = [], []
    for feedback in feedback_by_iteration.values():
        if "command_voltage_v" not in feedback:
            continue
        n = len(feedback["time_s"])
        measured = feedback.get("holdout_current_a", feedback["measured_current_a"])
        commands.append(float(2 * abs(np.fft.rfft(feedback["command_voltage_v"])[1]) / n))
        currents.append(float(2 * abs(np.fft.rfft(measured)[1]) / n))
    if commands:
        axes[1].plot(commands, np.asarray(currents) * 1e3, "o-", label="Measured iterations")
    gain = diagnosis["fundamental_gain_a_per_v"]
    required = diagnosis["estimated_required_fundamental_peak_v"]
    if gain is not None and required is not None:
        voltage = np.linspace(0, max(required, max(commands, default=0)) * 1.1, 200)
        axes[1].plot(voltage, voltage * gain * 1e3, "--", label="Linear gain extrapolation (not measured)")
    axes[1].axhline(harmonics[0]["target_amplitude_a"] * 1e3, linestyle=":", color="C0", label="Target fundamental")
    if diagnosis["configured_vpp"] is not None:
        axes[1].axvline(diagnosis["configured_vpp"] / 2, linestyle=":", color="C3", label="Configured Vpp / 2 (reference)")
    format_axis(axes[1], xlabel="Command fundamental peak (V)", ylabel="Current fundamental peak (mA)")
    axes[1].legend(loc="best", fontsize=7)
    for axis in axes:
        axis.grid(True, alpha=0.2)
    save_figure(figure, results_dir / "amplitude_bandwidth_diagnosis.png")
    comparison = report.get("best_holdout_comparison") or report["best_feedback_comparison"]
    lines = [
        "# 闭环电流幅值与带宽诊断", "",
        "本报告重新分析已有原始数据，没有重新采集、缩放真实测量值或修改冻结命令。", "",
        f"- 冻结最佳轮次：第 {report['best_iteration'] + 1} 轮（文件索引 {report['best_iteration']}）。",
        f"- 原采集停止原因：{report.get('acquisition_stop_reason') or '未记录'}。",
        f"- 全波形目标是否达标：{report['target_reached']}；阈值 {report['target_nrmse_threshold']}。",
        f"- 独立验证优先的 NRMSE：{comparison['shape_nrmse']:.6f}。",
        f"- 目标 / 实测 RMS：{comparison['target_rms_a'] * 1e3:.4f} / {comparison['measured_rms_a'] * 1e3:.4f} mA。",
        f"- 实测 RMS 为目标的 {comparison['rms_ratio'] * 100:.2f}%。",
        f"- 仅作形状诊断的拟合幅值倍率：{diagnosis['amplitude_fit_scale']:.4f}；",
        f"  拟合倍率后的形状 NRMSE：{diagnosis['shape_only_nrmse_after_amplitude_fit']:.6f}。", "",
        "| 谐波 | 频率 kHz | 目标幅值 mA | 实测幅值 mA |", "|---|---:|---:|---:|",
    ]
    lines.extend(f"| {h['harmonic']} | {h['frequency_hz']/1e3:.3f} | {h['target_amplitude_a']*1e3:.6f} | {h['measured_amplitude_a']*1e3:.6f} |" for h in harmonics)
    final_filter = report.get("final_command_filter")
    if report.get("final_command_measured") is False:
        lines.extend([
            "",
            "最终交付命令在冻结最佳轮次后又进行了低通，但没有对该低通后命令额外采集硬件反馈。",
            "以上 NRMSE、RMS 和谐波指标对应滤波前最佳迭代命令的实测结果，不能视为最终交付命令的实测指标。",
        ])
    else:
        lines.extend(["", "最终交付命令与已实测的冻结最佳迭代命令一致。"])
    if isinstance(final_filter, dict) and final_filter.get("filtering_applied"):
        lines.extend([
            "",
            f"最终命令低通截止频率：{float(final_filter['cutoff_hz']) / 1e3:.3f} kHz。",
            f"相对滤波前迭代命令，滤除差值 RMS / 峰值：{float(final_filter['removed_rms_v']):.6g} / "
            f"{float(final_filter['removed_peak_v']):.6g} V。",
            f"为保持固定 Vpp/offset 电压范围，滤波后整体缩放系数："
            f"{float(final_filter['voltage_scale_after_filter']):.6g}。",
        ])
    if gain is not None and required is not None:
        lines.extend([
            "", f"基波实测增益约 {gain * 1e3:.4f} mA/V。按该增益线性外推，目标基波约需 {required:.3f} V 峰值，",
            f"对应 {2 * required:.3f} Vpp 的正弦量级；本次配置为 {diagnosis['configured_vpp']} Vpp。",
            "该估算没有验证大幅度线性或高次谐波；图中的 Vpp/2 只是正弦电压余量参考，不是任意波基波的严格上界。",
        ])
    if diagnosis.get("learning_mode") == "full_time_domain":
        lines.extend([
            "", "本次使用完整时域误差更新：包含 DC 和全部采样分量，无频带滤波。",
            "带外残差为零仅表示未屏蔽误差分量，不代表目标可达或迭代一定收敛；电压约束和实测动态仍然有效。",
        ])
    elif diagnosis["learning_band_known"]:
        lines.extend([
            "", f"学习频带误差 NRMSE：{diagnosis['learning_band_nrmse']:.6f}。",
            f"若学习频带完全匹配，且其他实测分量保持不变，条件残差 NRMSE 仍为 {diagnosis['conditional_nrmse_if_learning_band_perfect']:.6f}。",
            "这是假设带外响应保持不变的离线分解，不是硬件不可突破的绝对误差下限。",
        ])
    lines.extend(["", "原采集正常结束仅说明流程完成，不代表目标误差达标。新代码会先检查幅值匹配所需的命令范围，再决定是否允许采集。"])
    (results_dir / "diagnosis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    from ...experiment_runtime import runtime_run_dir

    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
