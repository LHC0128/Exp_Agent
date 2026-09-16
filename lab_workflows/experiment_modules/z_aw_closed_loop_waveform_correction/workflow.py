"""Z 任意波响应滤波闭环：采集、评分对齐、命令坐标逆滤波更新。"""

from __future__ import annotations

from dataclasses import replace
import json
import shutil
from pathlib import Path

import numpy as np
import yaml

from ...common import WorkflowCancelled, find_project_root, load_mapping
from ...current_feedback import sha256_array
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import DeviceSession, create_run_directory, synchronize_connected_clocks
from ...z_arbitrary_control import configure_optimal_control_trigger
from ..z_aw_waveform_scope_check.analysis import _falling_edge_time
from ..z_aw_waveform_scope_check.workflow import _connect_devices, _scope_config, _sleep_cancellable, safe_shutdown
from .capture import capture_with_headroom
from .hardware import configure_verified_output
from .harmonic_feedback import MeasurementQualityError, coefficients, harmonic_basis
from .harmonic_loop import run_harmonic_loop
from .models import ZAWClosedLoopWaveformCorrectionParams
from .response_filter import (
    PreparedLoop, circular_convolution, prepare_closed_loop, shift_bandlimited,
)
from .static_feedback import (
    applied_from_voltage as _applied_from_voltage, align_cycle, average_complete_cycles,
    rms, scope_range,
)

EXPERIMENT_ID = "z-aw-closed-loop-waveform-correction"
DATA_TYPE = "Z_AW_Closed_Loop_Waveform_Correction"
EXECUTION_MODE = "typed_workflow"
CORRECTION_METHOD = "time_domain"


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


def _export_best(run_dir, prepared: PreparedLoop, params, records: list[dict], final_validation=None) -> dict:
    """只导出已有实测反馈的命令，不滤波、不重新对齐、不重新选相位。"""
    if not records:
        return {"final_command_measured": False}
    harmonic = params.correction_method == "harmonic_jacobian"
    if harmonic and not final_validation:
        return {"final_command_measured": False}
    best = final_validation if harmonic else min(records, key=lambda row: row["relative_rms_error"])
    source = run_dir.raw / best.get("measurement_path", f"iteration_{best['iteration']:03d}/holdout") / "feedback.npz"
    with np.load(source) as data:
        command = data["command_voltage_v"]
        measured = data["measured_current_a"]
    normalized = command / (params.z_aw_output_vpp / 2)
    np.savez(
        run_dir.results / "corrected_control_waveform.npz",
        format_version=np.int64(4 if harmonic else 3), correction_method=np.asarray(params.correction_method),
        phase_reference=np.asarray("CH4_falling_edge" if harmonic else "per_frame_target_alignment"),
        final_reload_validated=np.bool_(harmonic and final_validation["validated"]),
        measurement_path=np.asarray(str(source.relative_to(run_dir.raw)).replace("\\", "/")),
        identification_protocol=np.asarray("central_difference_harmonic_jacobian" if harmonic else ""),
        time_s=prepared.theory.time_s,
        omega_ctrl_hz=params.control_scale * prepared.theory.omega_ctrl_hz,
        target_current_a=prepared.target_current_a, measured_current_a=measured,
        voltage_v=command, normalized=normalized,
        repeat_frequency_hz=prepared.theory.repeat_frequency_hz,
        amplitude_vpp=params.z_aw_output_vpp, offset_v=0.0,
        coupling_calibration_run=np.asarray(prepared.calibration.run_name),
        coupling_calibration_sha256=np.asarray(prepared.calibration.analysis_sha256),
        frequency_response_run=np.asarray(prepared.response.run_name if prepared.response is not None else ""),
        frequency_response_sha256=np.asarray(prepared.response.response_sha256 if prepared.response is not None else ""),
        error_cutoff_hz=np.float64(params.error_cutoff_hz),
        inverse_regularization=np.float64(params.inverse_regularization),
        static_gain_a_per_v=prepared.fit.gain_a_per_v,
        static_intercept_a=prepared.fit.intercept_a,
        best_iteration=best["iteration"], relative_rms_error=best["relative_rms_error"],
    )
    np.savetxt(run_dir.results / "corrected_control_waveform.csv", np.column_stack((
        prepared.theory.time_s, prepared.target_current_a, measured, command, normalized,
    )), delimiter=",", header="time_s,target_current_a,measured_current_a,command_voltage_v,normalized", comments="")
    return {"best_iteration": best["iteration"], "best_relative_rms_error": best["relative_rms_error"],
            "final_command_measured": True, "command_waveform_sha256": sha256_array(command)}


def run(params: ZAWClosedLoopWaveformCorrectionParams) -> Path:
    """固定步长主循环；误差增大仍继续，只有明确停止条件和硬件失败结束。"""
    root = find_project_root()
    mapping = load_mapping(root)
    prepared = prepare_closed_loop(root, params)
    theory, target, fit = prepared.theory, prepared.target_current_a, prepared.fit
    resistance = prepared.calibration.sense_resistor_ohm
    initial_scale, initial_offset = scope_range(target * resistance, params.scope_vertical_divisions,
                                               params.scope_headroom_factor)
    run_dir = create_run_directory(DATA_TYPE, params.run_tag, params.to_external(),
                                   schema_version=params.schema_version, project_root=root)
    shutil.copyfile(prepared.calibration.analysis_path, run_dir.raw / "static_calibration_source.yaml")
    if prepared.response is not None:
        shutil.copyfile(prepared.response.response_path, run_dir.raw / "aw_frequency_response_source.npz")
    shutil.copyfile(theory.waveform_path, run_dir.raw / "theory_waveform.csv")
    shutil.copyfile(theory.parameter_path, run_dir.raw / "theory_parameters.csv")
    np.savez(run_dir.raw / "response_filter.npz",
             frequency_hz=prepared.frequency_hz,
             response_model_a_per_v=prepared.response_model_a_per_v,
             inverse_kernel_frequency=prepared.inverse_kernel_frequency,
             forward_kernel=prepared.forward_kernel, inverse_kernel=prepared.inverse_kernel,
             learning_band=prepared.learning_band,
             target_current_a=target, initial_command_voltage_v=prepared.initial.voltage_v)
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID, execution_mode=EXECUTION_MODE,
        correction_method=params.correction_method, current_power_limits_enforced=params.correction_method == "harmonic_jacobian",
        static_calibration={"source_run": prepared.calibration.run_name,
                            "sha256": prepared.calibration.analysis_sha256,
                            "gain_a_per_v": fit.gain_a_per_v, "intercept_a": fit.intercept_a,
                            "r_squared": fit.r_squared, "point_count": len(fit.voltage_v),
                            "sense_resistor_ohm": resistance,
                            "coupling_hz_per_a": prepared.calibration.slope_hz_per_a},
        current_frequency_response=({"mode": "static_gain_time_domain"} if prepared.response is None else {
            "source_run": prepared.response.run_name,
            "sha256": prepared.response.response_sha256,
            "output_protocol": "coherent_swept_sine_current_response",
            "command_voltage_reference": "50_ohm",
            "phase_reference": "measured_drive_voltage",
            "sweep_point_count": int(prepared.response.frequency_hz.size),
            "reliable_point_count": int(prepared.response.reliable.sum()),
            "reliable_min_hz": float(np.min(prepared.response.frequency_hz[prepared.response.reliable])),
            "reliable_max_hz": float(np.max(prepared.response.frequency_hz[prepared.response.reliable])),
            "sense_resistor_ohm": prepared.response.sense_resistor_ohm,
            "requested_cutoff_hz": prepared.requested_cutoff_hz,
            "effective_low_hz": prepared.effective_low_hz,
            "effective_cutoff_hz": prepared.effective_cutoff_hz,
            "interpolated_harmonic_count": int(prepared.interpolated_frequency_hz.size),
            "uncovered_harmonic_count": int(prepared.excluded_frequency_hz.size),
            "inverse_regularization": params.inverse_regularization}),
        source_hashes={"theory_waveform_sha256": theory.waveform_sha256,
                       "theory_parameter_sha256": theory.parameter_sha256},
        scope_initial_range={"scale_v_div": initial_scale, "offset_v": initial_offset,
                             "headroom_factor": params.scope_headroom_factor,
                             "target_min_v": float((target * resistance).min()),
                             "target_max_v": float((target * resistance).max())},
    )
    _log_progress(f"静态斜率 {fit.gain_a_per_v:.9g} A/V，截距 {fit.intercept_a:.9g} A；"
                  f"学习频带 (0, {params.error_cutoff_hz:.6g}] Hz，"
                  f"正则化比例 {params.inverse_regularization:g}；"
                  f"目标带外 RMS {prepared.target_outside_band_rms_a:.6g} A"
                  f"（目标 RMS {prepared.target_rms_a:.6g} A）")
    session = DeviceSession()
    devices, channels = {}, {}
    records = []
    applied = prepared.initial
    pass_count = 0
    stop_reason = "达到最大轮数"
    completion = "failed"
    failure = ""
    final_validation = None
    try:
        try:
            devices, channels = _connect_devices(mapping, session)
        except Exception:
            session.cleanup_connection_failure()
            raise
        clocks = synchronize_connected_clocks({"z_control": devices["z_control"]}, mapping,
                                               {"z_control": "Z_magnetic_field"}, settle_s=0.2)
        config, snapshot = _scope_config(params, theory, measured_scale_v_div=initial_scale,
                                         measured_offset_v=initial_offset)
        devices["acquirer"].apply_config(config)
        actual_rate = float(devices["scope"].get_sampling_rate())
        if params.error_cutoff_hz > actual_rate / 2:
            raise ValueError("最高学习频率超过示波器实际采样奈奎斯特频率")
        snapshot["actual_sample_rate_sa_s"] = actual_rate
        run_dir.update_config(clock_sources=clocks, scope_configuration=snapshot)
        def upload(voltage, path):
            check_cancelled()
            candidate = _applied_from_voltage(voltage, amplitude_vpp=params.z_aw_output_vpp, offset_v=0.0)
            path.mkdir(parents=True, exist_ok=True)
            np.savez(path / "command.npz", time_s=theory.time_s, voltage_v=voltage)
            device = devices["z_control"]
            device.set_output(False, channel=channels["trigger"])
            device.set_output_load("INFinity", channel=channels["trigger"])
            device.set_voltage_unit("VPP", channel=channels["trigger"])
            configure_optimal_control_trigger(params, device, channels["trigger"], output=False)
            readback = configure_verified_output(params, device, channels["z_field"], theory, candidate)
            (path / "output_readback.json").write_text(json.dumps(readback, ensure_ascii=False, indent=2), encoding="utf-8")
            device.set_output(True, channel=channels["trigger"])
            _sleep_cancellable(params.output_settle_s)

        def acquire(voltage, path, repeats):
            upload(voltage, path)
            return _measure_round(devices, config, prepared, replace(params, iteration_repeats=repeats), voltage, path)

        if params.correction_method == "harmonic_jacobian":
            stop_reason, final_validation = run_harmonic_loop(
                prepared, params, run_dir, records, acquire, upload, _log_progress)
        for k in range(0 if params.correction_method == "harmonic_jacobian" else params.max_iterations):
            check_cancelled()
            iteration_dir = run_dir.raw / f"iteration_{k:03d}"
            iteration_dir.mkdir()
            np.savez(iteration_dir / "command.npz", time_s=theory.time_s, voltage_v=applied.voltage_v)
            device = devices["z_control"]
            device.set_output(False, channel=channels["trigger"])
            device.set_output_load("INFinity", channel=channels["trigger"])
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
        completion = "completed"
    except WorkflowCancelled as exc:
        completion, stop_reason, failure = "cancelled", "用户取消", str(exc)
        raise
    except Exception as exc:
        stop_reason, failure = "实验失败", str(exc)
        raise
    finally:
        report = safe_shutdown(devices, channels)
        if report.errors:
            completion = "failed"
            failure = "；".join(filter(None, (failure, *report.errors)))
            _log_progress(f"设备关闭失败：{failure}")
        (run_dir.results / "iteration_summary.yaml").write_text(yaml.safe_dump(records, allow_unicode=True), encoding="utf-8")
        run_dir.update_config(completion_status=completion, stop_reason=stop_reason, failure_reason=failure,
                              target_reached=stop_reason == "达标", iterations=records,
                              safety_shutdown=report.to_dict())
        run_dir.update_config(**_export_best(run_dir, prepared, params, records, final_validation))
        _log_progress(f"结束：{stop_reason}；有效轮次 {len(records)}；结果目录 {run_dir.root}")
    return run_dir.root


def main() -> int:
    run(load_runtime_params(ZAWClosedLoopWaveformCorrectionParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
