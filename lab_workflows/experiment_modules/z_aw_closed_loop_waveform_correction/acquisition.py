"""闭环采集、逐轮更新及恢复后保存；科学算法沿用原实现。"""
from dataclasses import replace
import json
from pathlib import Path
import numpy as np
import yaml
from ...common import WorkflowCancelled
from ...current_feedback import sha256_array
from ...experiment_runtime import check_cancelled
from ...z_arbitrary_control import configure_optimal_control_trigger
from ..z_aw_waveform_scope_check.analysis import _falling_edge_time
from ..z_aw_waveform_scope_check.acquisition import _sleep_cancellable
from .capture import capture_with_headroom
from .hardware import configure_verified_output
from .harmonic_feedback import MeasurementQualityError, coefficients, harmonic_basis
from .harmonic_loop import run_harmonic_loop
from .response_filter import PreparedLoop, circular_convolution, shift_bandlimited
from .static_feedback import applied_from_voltage as _applied_from_voltage, align_cycle, average_complete_cycles, rms
from . import preparation
from .export import export_best

def _log_progress(message: str) -> None:
    print(f"[Z 闭环] {message}", flush=True)


def _trigger_relative_time(frame: dict, trigger_level_v: float) -> np.ndarray:
    edge = _falling_edge_time(frame["trigger_time_s"], frame["trigger_voltage_v"], trigger_level_v)
    return frame["time_s"] - edge


def _band_error(error: np.ndarray, band: np.ndarray) -> tuple[float, float]:
    """返回带内／带外误差 RMS；完整 RMS 仍是唯一达标指标。"""
    n = error.size
    spectrum = np.fft.rfft(error)
    in_band = rms(np.fft.irfft(spectrum * band, n=n))
    outside = rms(np.fft.irfft(spectrum * (~band), n=n))
    return in_band, outside


def _measure_round(devices, config, prepared: PreparedLoop, params, voltage_v: np.ndarray,
                   iteration_dir: Path) -> dict:
    """采集重复帧、换回命令坐标并给出本轮电压修正；不修改任何输入。"""
    theory, target = prepared.theory, prepared.target_current_a
    dt = float(np.median(np.diff(theory.time_s)))
    predicted = circular_convolution(prepared.forward_kernel, voltage_v) + prepared.fit.intercept_a
    aligned_frames = []
    harmonic = params.correction_method == "harmonic_jacobian"
    command_coordinate_errors = []
    for repeat in range(params.iteration_repeats):
        check_cancelled()
        _log_progress(f"采集 {repeat + 1}/{params.iteration_repeats}")
        frame = capture_with_headroom(devices, config, params,
                                      iteration_dir / f"scope_capture_{repeat:03d}.npz",
                                      **({"sense_resistor_ohm": prepared.calibration.sense_resistor_ohm} if harmonic else {}))
        cycle = average_complete_cycles(
            _trigger_relative_time(frame, params.scope_trigger_level_v),
            frame["measured_voltage_v"] / prepared.calibration.sense_resistor_ohm,
            theory.time_s, 1 / theory.repeat_frequency_hz)
        measured_aligned, measured_shift = (cycle, 0.0) if harmonic else align_cycle(cycle, target)
        error_aligned = target - measured_aligned
        error_command = shift_bandlimited(
            error_aligned, -measured_shift, prepared.learning_band)
        aligned_frames.append(measured_aligned)
        command_coordinate_errors.append(error_command)
        np.savez(iteration_dir / f"alignment_{repeat:03d}.npz",
                 cycle_current_a=cycle, aligned_current_a=measured_aligned,
                 error_command_current_a=error_command,
                 shift_samples=measured_shift, shift_s=measured_shift * dt)
    measured = np.mean(aligned_frames, axis=0)
    error = target - measured
    in_band_rms, outside_band_rms = _band_error(error, prepared.learning_band)
    correction = circular_convolution(
        prepared.inverse_kernel, np.mean(command_coordinate_errors, axis=0))
    np.savez(iteration_dir / "feedback.npz", time_s=theory.time_s, target_current_a=target,
             measured_current_a=measured, error_current_a=error,
             command_voltage_v=voltage_v, predicted_current_a=predicted,
             correction_voltage_v=correction, learning_band=prepared.learning_band)
    frames = np.asarray(aligned_frames)
    frame_scores = np.sqrt(np.mean((frames - target) ** 2, axis=1)) / prepared.target_rms_a
    score_sem = float(np.std(frame_scores, ddof=1) / np.sqrt(len(frames))) if len(frames) > 1 else 0.0
    if harmonic:
        # 异常数据已落盘；低幅掉帧和重复间相位变化都必须阻断学习。
        if np.min(np.sqrt(np.mean(frames ** 2, axis=1))) < 0.1 * prepared.target_rms_a:
            raise MeasurementQualityError("实测电流异常掉落到目标 RMS 的 10% 以下，停止学习")
        spread = np.max(np.sqrt(np.mean((frames - measured) ** 2, axis=1))) / prepared.target_rms_a
        basis = harmonic_basis(target.size, np.flatnonzero(prepared.learning_band))
        vectors = coefficients(frames, basis).reshape(len(frames), -1, 2)
        center = vectors.mean(axis=0)
        relative = np.linalg.norm(vectors - center, axis=-1) / np.maximum(np.linalg.norm(center, axis=-1), prepared.target_rms_a * 1e-3)
        # 逐点 spread 主要反映示波器量化噪声；谐波复系数才决定理论波形是否失真。
        if np.max(relative) > params.harmonic_repeatability_fraction:
            raise MeasurementQualityError("重复帧谐波幅度/相位不稳定，停止学习；未删除异常帧")
    return {
        "measured": measured, "error": error, "correction": correction,
        "frames": frames, "score_sem": score_sem,
        "score": rms(error) / prepared.target_rms_a,
        "error_rms_a": rms(error),
        "in_band_error_rms_a": in_band_rms,
        "outside_band_error_rms_a": outside_band_rms,
    }


def run_feedback_loop(ctx, run_dir, records):
    params, prepared = ctx.params, ctx.prepared
    devices, channels, config = ctx.devices, ctx.channels, ctx.scope_config
    theory, applied = prepared.theory, prepared.initial
    pass_count, stop_reason = 0, "达到最大轮数"
    for k in range(params.max_iterations):
        check_cancelled()
        iteration_dir = run_dir.raw / f"iteration_{k:03d}"
        iteration_dir.mkdir()
        np.savez(iteration_dir / "command.npz", time_s=theory.time_s, voltage_v=applied.voltage_v)
        device = devices["z_control"]
        device.set_output(False, channel=channels["trigger"])
        device.set_voltage_unit("VPP", channel=channels["trigger"])
        configure_optimal_control_trigger(params, device, channels["trigger"], output=False)
        readback = configure_verified_output(params, device, channels["z_field"], theory, applied)
        (iteration_dir / "output_readback.json").write_text(json.dumps(readback, ensure_ascii=False, indent=2), encoding="utf-8")
        device.set_output(True, channel=channels["trigger"])
        _sleep_cancellable(params.output_settle_s)
        _log_progress(f"轮次 {k + 1}/{params.max_iterations}")
        measured_round = _measure_round(devices, config, prepared, params,
                                        applied.voltage_v, iteration_dir)
        holdout_dir = iteration_dir / "holdout"
        holdout_dir.mkdir()
        holdout = _measure_round(devices, config, prepared,
                                 replace(params, iteration_repeats=params.holdout_repeats),
                                 applied.voltage_v, holdout_dir)
        record = {"iteration": k, "relative_rms_error": holdout["score"],
                  "feedback_relative_rms_error": measured_round["score"],
                  "holdout_relative_rms_error": holdout["score"],
                  "error_rms_a": holdout["error_rms_a"],
                  "in_band_error_rms_a": holdout["in_band_error_rms_a"],
                  "outside_band_error_rms_a": holdout["outside_band_error_rms_a"],
                  "command_sha256": sha256_array(applied.voltage_v)}
        records.append(record)
        _log_progress(f"轮次 {k + 1}：反馈 {measured_round['score']:.6%}，"
                      f"独立验证 {holdout['score']:.6%}，"
                      f"带内 {measured_round['in_band_error_rms_a']:.6g} A，"
                      f"带外 {measured_round['outside_band_error_rms_a']:.6g} A")
        pass_count = pass_count + 1 if holdout["score"] <= params.target_relative_rms else 0
        if pass_count >= params.required_passes:
            stop_reason = "达标"
            break
        if k == params.max_iterations - 1:
            break
        increment = params.iteration_damping * measured_round["correction"]
        max_step = float(params.maximum_step_v)
        peak_step = float(np.max(np.abs(increment)))
        if peak_step > max_step:
            increment = increment * (max_step / peak_step)
        candidate = applied.voltage_v + increment
        np.savez(iteration_dir / "update.npz", increment_voltage_v=increment, candidate_voltage_v=candidate)
        try:
            applied = _applied_from_voltage(candidate, amplitude_vpp=params.z_aw_output_vpp,
                                            offset_v=0.0)
        except ValueError as exc:
            stop_reason = "下一轮命令越界"
            record["update_rejected"] = str(exc)
            break
        # 误差增大也从当前命令继续，这是明确保留的行为。
    return stop_reason, None


def acquire_run(ctx):
    params, prepared = ctx.params, ctx.prepared
    try:
        run_dir = preparation.initialize_run(ctx)
    except BaseException as exc:
        if ctx.run_directory is not None:
            try:
                ctx.run_directory.update_config(completion_status="failed", failure_reason=str(exc))
            except Exception as save_error:
                ctx.persistence_errors.append(f"保存初始化故障失败：{save_error}")
        raise
    ctx.run_directory, ctx.run_dir = run_dir, run_dir.root
    records, final_validation = [], None
    completion, stop_reason, failure = "failed", "实验失败", None
    ctx.outputs_started = True

    def upload(voltage, path):
        check_cancelled()
        candidate = _applied_from_voltage(voltage, amplitude_vpp=params.z_aw_output_vpp, offset_v=0.0)
        path.mkdir(parents=True, exist_ok=True)
        np.savez(path / "command.npz", time_s=prepared.theory.time_s, voltage_v=voltage)
        device = ctx.devices["z_control"]
        device.set_output(False, channel=ctx.channels["trigger"])
        device.set_voltage_unit("VPP", channel=ctx.channels["trigger"])
        configure_optimal_control_trigger(params, device, ctx.channels["trigger"], output=False)
        readback = configure_verified_output(params, device, ctx.channels["z_field"], prepared.theory, candidate)
        (path / "output_readback.json").write_text(json.dumps(readback, ensure_ascii=False, indent=2), encoding="utf-8")
        device.set_output(True, channel=ctx.channels["trigger"])
        _sleep_cancellable(params.output_settle_s)

    def acquire(voltage, path, repeats):
        upload(voltage, path)
        return _measure_round(ctx.devices, ctx.scope_config, prepared,
            replace(params, iteration_repeats=repeats), voltage, path)

    try:
        if params.correction_method == "harmonic_jacobian":
            stop_reason, final_validation = run_harmonic_loop(
                prepared, params, run_dir, records, acquire, upload, _log_progress)
        else:
            stop_reason, final_validation = run_feedback_loop(ctx, run_dir, records)
        completion = "completed"
    except BaseException as exc:
        failure = exc
        if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)):
            completion, stop_reason = "cancelled", "用户取消"
        raise
    finally:
        # 恢复危险输出后再写汇总；慢盘或保存失败不会把 Z 留在开启状态。
        report = preparation.close_loop_outputs(ctx)
        errors = list(report.errors)
        ctx.outputs.update({"Z 任意波": "DC 0 V / OFF" if report.completed else "未知",
                            "共同触发": "保持最后状态", "Scope": "STOP" if report.completed else "未知"})
        try:
            text = yaml.safe_dump(records, allow_unicode=True)
            (run_dir.raw / "iteration_summary.yaml").write_text(text, encoding="utf-8")
            (run_dir.results / "iteration_summary.yaml").write_text(text, encoding="utf-8")
            run_dir.update_config(completion_status="failed" if errors else completion, stop_reason=stop_reason,
                failure_reason="；".join(([str(failure)] if failure else []) + errors),
                target_reached=stop_reason == "达标", iterations=records, safety_shutdown=report.to_dict())
            run_dir.update_config(**export_best(run_dir, prepared, params, records, final_validation))
        except Exception as exc:
            if failure is None:
                try:
                    run_dir.update_config(completion_status="failed", failure_reason=str(exc))
                except Exception:
                    pass
                raise
            ctx.persistence_errors.append(f"保存闭环诊断失败：{exc}")
        if errors:
            ctx.persistence_errors.extend(errors)
            if failure is None:
                raise RuntimeError("闭环输出恢复失败：" + "；".join(errors))
    return ["experiment_config.yaml", "raw/iteration_summary.yaml", "results/iteration_summary.yaml"]
