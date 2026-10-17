"""闭环来源、Scope 配置与每轮目录准备；复用 Z 波形接入和安全步骤。"""
from pathlib import Path
import numpy as np
from ...current_feedback import sha256_file
from ...steps import create_run_directory, DGChannelShutdown, run_safety_shutdown
from ...steps.safety_shutdown import ShutdownAction
from ..z_aw_waveform_scope_check.preparation import preflight
from ..z_aw_waveform_scope_check.acquisition import _scope_config
from .response_filter import prepare_closed_loop
from .static_feedback import scope_range
from .run_format import EXPERIMENT_ID, DATA_TYPE, RAW_FORMAT_VERSION


def checked_copy(source, destination, expected):
    source, destination = Path(source), Path(destination)
    if sha256_file(source) != expected:
        raise ValueError("闭环来源已改变，请重新执行 A03 来源准备")
    destination.write_bytes(source.read_bytes())
    if sha256_file(destination) != expected:
        raise ValueError("闭环来源在冻结期间改变，请重新执行 A03 来源准备")


def prepare_scope(ctx):
    scale, offset = scope_range(ctx.prepared.target_current_a * ctx.prepared.calibration.sense_resistor_ohm,
        ctx.params.scope_vertical_divisions, ctx.params.scope_headroom_factor)
    config, snapshot = _scope_config(ctx.params, ctx.prepared.theory,
        measured_scale_v_div=scale, measured_offset_v=offset)
    ctx.devices["acquirer"].apply_config(config)
    rate = float(ctx.devices["scope"].get_sampling_rate())
    if not np.isfinite(rate) or rate <= 0 or ctx.params.error_cutoff_hz >= rate / 2:
        raise ValueError("最高学习频率必须严格低于示波器实际采样奈奎斯特频率")
    snapshot["actual_sample_rate_sa_s"] = rate
    return config, snapshot


def close_loop_outputs(ctx):
    return run_safety_shutdown(dg_channels=(DGChannelShutdown(ctx.devices["z_control"],
        ctx.channels["z_field"], "Z_magnetic_field", "Z 任意波"),),
        extra_actions=(ShutdownAction("停止 SDS 采集", ctx.devices["scope"].trigger_stop),))


def initialize_run(ctx):
    params, root, prepared = ctx.params, ctx.root, ctx.prepared
    theory, target, fit = prepared.theory, prepared.target_current_a, prepared.fit
    resistance = prepared.calibration.sense_resistor_ohm
    initial_scale, initial_offset = scope_range(target * resistance, params.scope_vertical_divisions, params.scope_headroom_factor)
    run_dir = create_run_directory(DATA_TYPE, params.run_tag, params.to_external(),
                                   schema_version=params.schema_version, project_root=root)
    ctx.run_directory, ctx.run_dir = run_dir, run_dir.root
    run_dir.update_config(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE,
        raw_format_version=RAW_FORMAT_VERSION, correction_method=params.correction_method,
        completion_status="failed", failure_reason="闭环初始化未完成")
    checked_copy(prepared.calibration.analysis_path, run_dir.raw / "static_calibration_source.yaml", prepared.calibration.analysis_sha256)
    if prepared.response is not None:
        checked_copy(prepared.response.response_path, run_dir.raw / "aw_frequency_response_source.npz", prepared.response.response_sha256)
    checked_copy(theory.waveform_path, run_dir.raw / "theory_waveform.csv", theory.waveform_sha256)
    checked_copy(theory.parameter_path, run_dir.raw / "theory_parameters.csv", theory.parameter_sha256)
    np.savez(run_dir.raw / "response_filter.npz",
             frequency_hz=prepared.frequency_hz,
             response_model_a_per_v=prepared.response_model_a_per_v,
             inverse_kernel_frequency=prepared.inverse_kernel_frequency,
             forward_kernel=prepared.forward_kernel, inverse_kernel=prepared.inverse_kernel,
             learning_band=prepared.learning_band,
             target_current_a=target, initial_command_voltage_v=prepared.initial.voltage_v)
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID, execution_mode="typed_workflow",
        data_type=DATA_TYPE, raw_format_version=RAW_FORMAT_VERSION, completion_status="running", failure_reason="",
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
    print(f"静态斜率 {fit.gain_a_per_v:.9g} A/V，截距 {fit.intercept_a:.9g} A；"
                  f"学习频带 (0, {params.error_cutoff_hz:.6g}] Hz，"
                  f"正则化比例 {params.inverse_regularization:g}；"
                  f"目标带外 RMS {prepared.target_outside_band_rms_a:.6g} A"
                  f"（目标 RMS {prepared.target_rms_a:.6g} A）")
    run_dir.update_config(clock_sources=ctx.clock_sources, scope_configuration=ctx.scope_snapshot)
    return run_dir
