"""Z 任意波静态斜率闭环：采集、对齐、低通误差、累加命令。"""
from __future__ import annotations

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
from .models import ZAWClosedLoopWaveformCorrectionParams
from .static_feedback import (
    applied_from_voltage as _applied_from_voltage, align_cycle, average_complete_cycles,
    periodic_lowpass, prepare_feedback, rms, scope_range,
)

EXPERIMENT_ID = "z-aw-closed-loop-waveform-correction"
DATA_TYPE = "Z_AW_Closed_Loop_Waveform_Correction"
EXECUTION_MODE = "typed_workflow"


def _log_progress(message: str) -> None:
    print(f"[Z 闭环] {message}", flush=True)


def _trigger_relative_time(frame: dict, trigger_level_v: float) -> np.ndarray:
    edge = _falling_edge_time(frame["trigger_time_s"], frame["trigger_voltage_v"], trigger_level_v)
    return frame["time_s"] - edge


def _export_best(run_dir, prepared, params, records: list[dict]) -> dict:
    """只导出已有实测反馈的命令，不再滤波或重新选择测量相位。"""
    if not records:
        return {"final_command_measured": False}
    best = min(records, key=lambda row: row["relative_rms_error"])
    with np.load(run_dir.raw / f"iteration_{best['iteration']:03d}" / "feedback.npz") as data:
        command = data["command_voltage_v"]
        measured = data["measured_current_a"]
    normalized = command / (params.z_aw_output_vpp / 2)
    np.savez(
        run_dir.results / "corrected_control_waveform.npz",
        format_version=np.int64(2), correction_method=np.asarray("static_current_feedback"),
        time_s=prepared.theory.time_s,
        omega_ctrl_hz=params.control_scale * prepared.theory.omega_ctrl_hz,
        target_current_a=prepared.target_current_a, measured_current_a=measured,
        voltage_v=command, normalized=normalized,
        repeat_frequency_hz=prepared.theory.repeat_frequency_hz,
        amplitude_vpp=params.z_aw_output_vpp, offset_v=0.0,
        coupling_calibration_run=np.asarray(prepared.calibration.run_name),
        coupling_calibration_sha256=np.asarray(prepared.calibration.analysis_sha256),
        static_gain_a_per_v=prepared.fit.gain_a_per_v, static_intercept_a=prepared.fit.intercept_a,
        best_iteration=best["iteration"], relative_rms_error=best["relative_rms_error"],
    )
    np.savetxt(run_dir.results / "corrected_control_waveform.csv", np.column_stack((
        prepared.theory.time_s, prepared.target_current_a, measured, command, normalized,
    )), delimiter=",", header="time_s,target_current_a,measured_current_a,command_voltage_v,normalized", comments="")
    return {"best_iteration": best["iteration"], "best_relative_rms_error": best["relative_rms_error"],
            "final_command_measured": True, "command_waveform_sha256": sha256_array(command)}


def run(params: ZAWClosedLoopWaveformCorrectionParams) -> Path:
    """误差增大仍继续；只有明确停止条件和硬件失败结束迭代。"""
    root = find_project_root()
    mapping = load_mapping(root)
    prepared = prepare_feedback(root, params)
    theory, target, fit = prepared.theory, prepared.target_current_a, prepared.fit
    resistance = prepared.calibration.sense_resistor_ohm
    dt = float(np.median(np.diff(theory.time_s)))
    initial_scale, initial_offset = scope_range(target * resistance, params.scope_vertical_divisions,
                                               params.scope_headroom_factor)
    run_dir = create_run_directory(DATA_TYPE, params.run_tag, params.to_external(),
                                   schema_version=params.schema_version, project_root=root)
    shutil.copyfile(prepared.calibration.analysis_path, run_dir.raw / "static_calibration_source.yaml")
    shutil.copyfile(theory.waveform_path, run_dir.raw / "theory_waveform.csv")
    shutil.copyfile(theory.parameter_path, run_dir.raw / "theory_parameters.csv")
    np.savez(run_dir.raw / "target.npz", time_s=theory.time_s, target_current_a=target,
             initial_command_voltage_v=prepared.initial.voltage_v)
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID, execution_mode=EXECUTION_MODE,
        correction_method="static_current_feedback", current_power_limits_enforced=False,
        static_calibration={"source_run": prepared.calibration.run_name,
                            "sha256": prepared.calibration.analysis_sha256,
                            "gain_a_per_v": fit.gain_a_per_v, "intercept_a": fit.intercept_a,
                            "r_squared": fit.r_squared, "point_count": len(fit.voltage_v),
                            "sense_resistor_ohm": resistance,
                            "coupling_hz_per_a": prepared.calibration.slope_hz_per_a},
        source_hashes={"theory_waveform_sha256": theory.waveform_sha256,
                       "theory_parameter_sha256": theory.parameter_sha256},
        scope_initial_range={"scale_v_div": initial_scale, "offset_v": initial_offset,
                             "headroom_factor": params.scope_headroom_factor,
                             "target_min_v": float((target * resistance).min()),
                             "target_max_v": float((target * resistance).max())},
    )
    _log_progress(f"静态斜率 {fit.gain_a_per_v:.9g} A/V，截距 {fit.intercept_a:.9g} A；"
                  f"理论量程 {initial_scale:g} V/div，偏置 {initial_offset:.6g} V，余量 {params.scope_headroom_factor:g} 倍")
    session = DeviceSession()
    devices, channels = {}, {}
    records = []
    applied = prepared.initial
    pass_count = 0
    stop_reason = "达到最大轮数"
    completion = "failed"
    failure = ""
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
            raise ValueError("误差截止频率超过示波器实际采样奈奎斯特频率")
        snapshot["actual_sample_rate_sa_s"] = actual_rate
        run_dir.update_config(clock_sources=clocks, scope_configuration=snapshot)
        for k in range(params.max_iterations):
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
            aligned_frames = []
            for repeat in range(params.iteration_repeats):
                check_cancelled()
                _log_progress(f"轮次 {k + 1}/{params.max_iterations}，采集 {repeat + 1}/{params.iteration_repeats}")
                frame = capture_with_headroom(devices, config, params, iteration_dir / f"scope_capture_{repeat:03d}.npz")
                cycle = average_complete_cycles(_trigger_relative_time(frame, params.scope_trigger_level_v),
                                                 frame["measured_voltage_v"] / resistance,
                                                 theory.time_s, 1 / theory.repeat_frequency_hz)
                aligned, shift = align_cycle(cycle, target)
                np.savez(iteration_dir / f"alignment_{repeat:03d}.npz", cycle_current_a=cycle,
                         aligned_current_a=aligned, shift_samples=shift, shift_s=shift * dt)
                aligned_frames.append(aligned)
            measured = np.mean(aligned_frames, axis=0)
            error = target - measured
            score = rms(error) / prepared.target_rms_a
            filtered = periodic_lowpass(error, dt, params.error_cutoff_hz)
            np.savez(iteration_dir / "feedback.npz", time_s=theory.time_s, target_current_a=target,
                     measured_current_a=measured, error_current_a=error, filtered_error_current_a=filtered,
                     command_voltage_v=applied.voltage_v)
            record = {"iteration": k, "relative_rms_error": score, "error_rms_a": rms(error),
                      "filtered_error_rms_a": rms(filtered), "command_sha256": sha256_array(applied.voltage_v)}
            records.append(record)
            _log_progress(f"轮次 {k + 1}：相对 RMS 误差 {score:.6%}，电流误差 RMS {rms(error):.6g} A")
            pass_count = pass_count + 1 if score <= params.target_relative_rms else 0
            if pass_count >= params.required_passes:
                stop_reason = "达标"
                break
            if k == params.max_iterations - 1:
                break
            increment = params.iteration_damping * filtered / fit.gain_a_per_v
            candidate = applied.voltage_v + increment
            np.savez(iteration_dir / "update.npz", increment_voltage_v=increment, candidate_voltage_v=candidate)
            try:
                applied = _applied_from_voltage(candidate, amplitude_vpp=params.z_aw_output_vpp,
                                                offset_v=0.0)
            except ValueError as exc:
                stop_reason = "下一轮命令越界"
                record["update_rejected"] = str(exc)
                break
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
        run_dir.update_config(**_export_best(run_dir, prepared, params, records))
        _log_progress(f"结束：{stop_reason}；有效轮次 {len(records)}；结果目录 {run_dir.root}")
    return run_dir.root


def main() -> int:
    run(load_runtime_params(ZAWClosedLoopWaveformCorrectionParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
