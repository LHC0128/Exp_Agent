"""只导出已实测且满足方法资格的冻结命令。"""
import numpy as np
from ...current_feedback import sha256_array
from .response_filter import PreparedLoop

def export_best(run_dir, prepared: PreparedLoop, params, records: list[dict], final_validation=None) -> dict:
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
