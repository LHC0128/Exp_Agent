"""四级校相及诊断；科学算法继续复用共享步骤。"""
from dataclasses import replace
import math
import numpy as np
from lockin_amplifier import DAQConfig, DemodulatorConfig, OscillatorConfig, SignalInputConfig, daq, demod
from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import (set_temperature_switch, calibrate_demod_phase, calibrate_direct_aw_phase,
    PhaseCalibrationConfig, DirectAWPhaseCalibrationConfig)
from ._helpers import hf2_snapshot, validate_levels, wait
from .excitation import upload_xy, arm_z
from .waveform import synthesize_xy, AW_AMPLITUDE_FLOOR_VPP
from .phase_plot import plot_z_phase_scan


class CalibrationQualityError(RuntimeError):
    """校相质量未达标，可在同一设备会话中重试。"""


def calibrate_demod0(ctx):
    params, devices, state, directory = ctx.params, ctx.devices, ctx.state, ctx.directory
    signals, channels, hf2 = (devices[key] for key in ("signals", "channels", "hf2"))
    cancellation = ctx.cancellation
    for name in ("x", "y", "z"):
        signals.set_output(False, channel=channels[name])
    ctx.outputs.update(X="OFF", Y="OFF", Z="OFF")
    report_runtime_progress("phase", "Demod0 校相", 5)
    demod.configure_signal_input(hf2, SignalInputConfig(input_index=0, range=params.demod0_signal_range, diff=False))
    demod.configure_oscillator(hf2, OscillatorConfig(osc_index=0, frequency=params.pump_mod_freq))
    d0_config = DemodulatorConfig(demod_index=0, rate=params.demod0_rate, input_channel=0, osc_select=0, time_constant=params.demod0_tc_calib, order=params.demod0_order)
    state["actual_rates"]["demod0_calib_Sa_s"] = float(demod.configure_demodulator(hf2, d0_config))
    set_temperature_switch(signals, False, channel=channels["temp"])
    wait(params.temp_off_settle_s)
    phase0 = calibrate_demod_phase(hf2, PhaseCalibrationConfig(demod_idx=0), settle_wait=wait, cancellation=cancellation)
    state["calibration"]["demod0"] = phase0.to_dict()
    set_temperature_switch(signals, True, channel=channels["temp"])
    wait(params.temp_switch_on_lag_s)
    ctx.d0_config, ctx.phase0 = d0_config, phase0


def calibrate_xy(ctx):
    params, devices, state, directory = ctx.params, ctx.devices, ctx.state, ctx.directory
    signals, channels, hf2 = (devices[key] for key in ("signals", "channels", "hf2"))
    envelope, cancellation = ctx.envelope, ctx.cancellation
    signals.set_output(False, channel=channels["z"])
    ctx.outputs["Z"] = "OFF"
    demod.configure_demodulator(hf2, replace(ctx.d0_config, phase=ctx.phase0.phase_shift_deg))
    state["xy_phase_calibration"] = {"converged": False, "history": []}
    initial_waveforms = synthesize_xy(params, envelope, params.xy_ctrl_phase)
    state["xy_aw_output"] = {
        "mode": "auto_envelope_peak",
        "vpp_V": float(initial_waveforms["xy_aw_vpp"]),
        "offset_V": 0.0,
        "amplitude_floor_vpp": AW_AMPLITUDE_FLOOR_VPP,
    }
    directory.update_config(**state)
    np.savez(directory.raw / "xy_aw_waveforms_initial.npz", **initial_waveforms)
    state["data_files"].append("raw/xy_aw_waveforms_initial.npz")
    report_runtime_progress("phase", "XY DirectAW 校相", 10)
    set_temperature_switch(signals, False, channel=channels["temp"])
    wait(params.temp_off_settle_s)

    def apply_and_measure(phase: float) -> dict:
        waveforms = upload_xy(signals, channels, params, envelope, phase)
        np.savez(directory.raw / "xy_aw_waveforms.npz", **waveforms)
        if "raw/xy_aw_waveforms.npz" not in state["data_files"]:
            state["data_files"].append("raw/xy_aw_waveforms.npz")
        wait(params.temp_off_settle_s)
        sample = demod.read_demod_sample(hf2, 0)
        diagnostics = state["xy_phase_calibration"]
        finite = all(math.isfinite(float(sample[key])) for key in ("x", "y", "r", "theta"))
        best_r = max([float(item["r"]) for item in diagnostics["history"]] + [float(sample["r"])]) if finite else 0.0
        minimum = max(params.xy_ctrl_phase_min_r_v, best_r * params.xy_ctrl_phase_min_r_ratio)
        diagnostics["history"].append({"phase_deg": float(phase), **{key: float(sample[key]) if math.isfinite(float(sample[key])) else None for key in ("x", "y", "r", "theta")}, "minimum_r_v": minimum})
        directory.update_config(**state)
        if not finite or sample["r"] < minimum:
            raise CalibrationQualityError("XY 校相信号低于门槛或含非有限值，停止测量")
        return sample

    xy_phase = calibrate_direct_aw_phase(params.xy_ctrl_phase, apply_and_measure, DirectAWPhaseCalibrationConfig(tolerance_deg=params.xy_ctrl_phase_tol_deg, max_measurements=params.xy_ctrl_phase_max_iter + 1, minimum_r_v=params.xy_ctrl_phase_min_r_v, minimum_r_ratio=params.xy_ctrl_phase_min_r_ratio), cancellation_check=check_cancelled)
    state["xy_phase_calibration"] = xy_phase.to_dict()
    directory.update_config(**state)
    if not xy_phase.converged:
        raise CalibrationQualityError("XY 校相未收敛，停止测量")
    set_temperature_switch(signals, True, channel=channels["temp"])
    wait(params.temp_switch_on_lag_s)
    ctx.xy_phase = xy_phase


def configure_loopback(ctx):
    params, devices, state, directory = ctx.params, ctx.devices, ctx.state, ctx.directory
    signals, channels, hf2 = (devices[key] for key in ("signals", "channels", "hf2"))
    d0_config, phase0 = ctx.d0_config, ctx.phase0
    report_runtime_progress("phase", "配置 RF 输入线路与 RF 解调器响应滤波", 12)
    if params.rf_input_source == "voltage1":
        demod.configure_signal_input(hf2, SignalInputConfig(input_index=1, range=params.loopback_input_range_v, diff=params.loopback_input_diff))
    from .preparation import check_rf_aux_voltage
    source_input = hf2.get_int(f"{hf2.demod_path(0)}/adcselect")
    if source_input not in range(hf2.capabilities["voltage_inputs"]):
        raise ValueError("Demod0 来源不是主电压输入，无法检查 RF 辅助输出的电压边界")
    check_rf_aux_voltage(params, ctx.root, ctx.mapping["lockin_r"]["device_library_id"],
                         hf2.get_double(f"{hf2.sig_in_path(source_input)}/range"))
    from ...lockin_control import apply_settings
    apply_settings(hf2, ctx.mapping["lockin_r"]["device_library_id"], 0, {"aux_output": {
        "index": params.rf_aux_output_idx, "demod_index": 0, "output_select": 1,
        "scale": params.loopback_aux_scale, "offset_v": params.loopback_aux_offset_v,
    }}, project_root=ctx.root)
    demod.configure_oscillator(hf2, OscillatorConfig(osc_index=params.demod3_osc_idx, frequency=params.z_rf_freq))
    state["actual_rates"]["demod0_meas_Sa_s"] = float(demod.configure_demodulator(hf2, replace(d0_config, time_constant=params.demod0_tc_meas, phase=phase0.phase_shift_deg)))
    response_config = DemodulatorConfig(demod_index=params.demod3_idx, rate=params.demod3_rate, input_source=params.rf_input_source, osc_select=params.demod3_osc_idx, time_constant=params.demod3_tc, order=params.demod3_order)
    state["actual_rates"]["demod3_response_Sa_s"] = float(demod.configure_demodulator(hf2, response_config, for_acquisition=True))
    validate_levels("Z_magnetic_field", params.z_rf_amplitude_init)
    signals.setup_sine(freq=params.z_rf_freq, amplitude=params.z_rf_amplitude_init, offset=0.0, phase=0.0, channel=channels["z"])
    signals.set_burst_mode("INFinity", channel=channels["z"])
    signals.set_burst_trigger_source("EXTernal", channel=channels["z"])
    signals.set_burst_trigger_slope("POSitive", channel=channels["z"])
    arm_z(signals, channels["z"], params, params.z_rf_amplitude_init)
    ctx.response_config = response_config


def calibrate_rf(ctx):
    params, devices, state, directory = ctx.params, ctx.devices, ctx.state, ctx.directory
    signals, channels, hf2 = (devices[key] for key in ("signals", "channels", "hf2"))
    state["rf_phase_calibration"] = {"points_completed": 0}
    # 延迟导入：mx_z 包的 __init__ 在导入期解析项目根目录，保持模块导入无副作用。
    from ..mx_z_optimal_control_rf_sensitivity.phase import fit_quadrature_phase_scan, paired_phase_order
    report_runtime_progress("phase", "扫描 Z RF 触发相位", 15)
    scan_axis = params.z_phase_scan_start_deg + params.z_phase_scan_step_deg * np.arange(int(round((params.z_phase_scan_stop_deg - params.z_phase_scan_start_deg) / params.z_phase_scan_step_deg)) + 1)
    scan_order = paired_phase_order(scan_axis)
    scan_rate = state["actual_rates"]["demod3_response_Sa_s"]
    scan_config = DAQConfig(device=hf2.device_id, duration=params.scan_point_duration_s, grid_cols=max(8, int(scan_rate * params.scan_point_duration_s)), grid_rows=1, grid_mode=2, signal_paths=["sample.r", "sample.x", "sample.y"])
    scan_r, scan_x, scan_y = [], [], []
    scan_arrays: dict = {}
    # 射频相位扫描按温控分组关闭温控，末组不足分组点数也在拟合前恢复。
    set_temperature_switch(signals, False, channel=channels["temp"])
    wait(params.temp_off_settle_s)
    for index, scan_phase in enumerate(scan_order):
        check_cancelled()
        if index and index % params.temp_group_size == 0:
            set_temperature_switch(signals, False, channel=channels["temp"])
            wait(params.temp_off_settle_s)
        arm_z(signals, channels["z"], params, params.z_rf_amplitude_init, phase_base=float(scan_phase))
        wait(params.rf_step_settle_s)
        results = daq.acquire_data(hf2, scan_config, demod_idx=params.demod3_idx, actual_rate=scan_rate, requested_rate=params.demod3_rate, timeout=params.scan_point_duration_s + 10, cancellation_check=check_cancelled, strict_cleanup=True)
        from lockin_amplifier import result_rate, acquisition_metadata
        by_name = {result.signal_name: np.asarray(result.values, dtype=float) for result in results}
        if set(by_name) != {"sample.r", "sample.x", "sample.y"} or not all(np.all(np.isfinite(values)) for values in by_name.values()):
            raise RuntimeError(f"RF 相位扫描第 {index + 1} 点 DAQ 未返回有效 R/X/Y 数据")
        scan_r.append(by_name["sample.r"])
        scan_x.append(by_name["sample.x"])
        scan_y.append(by_name["sample.y"])
        # 逐点落盘，取消或拟合失败也保留已测曲线。
        r_matrix, x_matrix, y_matrix = (np.asarray(series) for series in (scan_r, scan_x, scan_y))
        scan_arrays = {
            "scanned_phase_deg": scan_order[: index + 1],
            "r_mean_v": r_matrix.mean(axis=1), "x_mean_v": x_matrix.mean(axis=1), "y_mean_v": y_matrix.mean(axis=1),
            "r_std_v": r_matrix.std(axis=1, ddof=1), "x_std_v": x_matrix.std(axis=1, ddof=1), "y_std_v": y_matrix.std(axis=1, ddof=1),
            "r_series_V": r_matrix, "x_series_V": x_matrix, "y_series_V": y_matrix,
            "z_rf_amplitude_vpp": params.z_rf_amplitude_init, "actual_rate_Sa_s": result_rate(results, scan_rate), "duration_s": params.scan_point_duration_s, "demod_idx": params.demod3_idx,
            **acquisition_metadata(results), "time_s": results[0].time,
        }
        np.savez(directory.raw / "z_phase_scan.npz", **scan_arrays)
        state["rf_phase_calibration"]["points_completed"] = index + 1
        if "raw/z_phase_scan.npz" not in state["data_files"]:
            state["data_files"].append("raw/z_phase_scan.npz")
        directory.update_config(**state)
        if (index + 1) % params.temp_group_size == 0 or index + 1 == len(scan_order):
            set_temperature_switch(signals, True, channel=channels["temp"])
            wait(params.temp_switch_on_lag_s)
    try:
        # 不设 R²/显著性门槛，直接采用拟合选定的相位；仅数值异常视为失败。
        quadrature_fit, fit_arrays = fit_quadrature_phase_scan(scan_order, scan_arrays["x_mean_v"], scan_arrays["y_mean_v"], r_squared_min=0.0, amplitude_sigma_min=0.0)
        if not math.isfinite(quadrature_fit.in_phase_amplitude_v) or quadrature_fit.in_phase_amplitude_v <= 0:
            raise RuntimeError("RF 相位扫描同相幅度为零，无法确定最优触发相位")
    except (ValueError, RuntimeError, FloatingPointError) as exc:
        plot_z_phase_scan(directory.raw, directory.results, {"error": str(exc)})
        raise
    z_positive_phase = float(quadrature_fit.selected_phase_deg) % 360.0
    state["rf_phase_calibration"].update({
        "selected_phase_deg": z_positive_phase,
        "initial_phase_deg": params.z_burst_phase_init % 360,
        "rf_amplitude_vpp": params.z_rf_amplitude_init,
        "actual_rate_Sa_s": scan_rate,
        "acquisition_order": "phi_then_phi_plus_180",
        "quadrature_fit": quadrature_fit.to_dict(),
        "fit_gates": "disabled",
    })
    state["calibration"]["z_positive_field_phase_deg"] = z_positive_phase
    state["calibration"]["z_negative_field_phase_deg"] = (z_positive_phase + 180) % 360
    np.savez(directory.raw / "z_phase_scan.npz", **{**scan_arrays, **fit_arrays, "selected_phase_deg": z_positive_phase})
    state["z_phase_scan_plot"] = "results/" + plot_z_phase_scan(directory.raw, directory.results, {"quadrature_fit": quadrature_fit.to_dict(), "selected_phase_deg": z_positive_phase})
    directory.update_config(**state)
    ctx.z_positive_phase = z_positive_phase


def calibrate_demod3(ctx):
    params, devices, state, directory = ctx.params, ctx.devices, ctx.state, ctx.directory
    signals, channels, hf2 = (devices[key] for key in ("signals", "channels", "hf2"))
    z_positive_phase, response_config, cancellation = ctx.z_positive_phase, ctx.response_config, ctx.cancellation
    report_runtime_progress("phase", f"校准 RF 解调器（索引 {params.demod3_idx}）", 20)
    arm_z(signals, channels["z"], params, params.z_rf_amplitude_init, phase_base=z_positive_phase)
    set_temperature_switch(signals, False, channel=channels["temp"])
    wait(params.temp_off_settle_s)
    phase3 = calibrate_demod_phase(hf2, PhaseCalibrationConfig(demod_idx=params.demod3_idx), settle_wait=wait, cancellation=cancellation)
    response_config = replace(response_config, phase=phase3.phase_shift_deg)
    state["calibration"]["demod3"] = phase3.to_dict()
    state["phase_calibration_status"] = "completed"
    snapshot_key = "hf2_calibration_snapshot" if ctx.identity.RESPONSE_COMPONENT == "R" else "hf2_response_snapshot"
    state[snapshot_key] = hf2_snapshot(hf2, params)
    directory.update_config(**state)
    set_temperature_switch(signals, True, channel=channels["temp"])
    wait(params.temp_switch_on_lag_s)
    ctx.response_config = response_config


def set_rf_frequency(ctx, frequency):
    """共同触发持续运行，改频时先关闭 Z，随后由原重臂步骤恢复。"""
    signals, channels, hf2 = (ctx.devices[key] for key in ("signals", "channels", "hf2"))
    signals.set_output(False, channel=channels["z"])
    signals.set_burst_state(False, channel=channels["z"])
    ctx.outputs["Z"] = "OFF"
    signals.set_frequency(frequency, channel=channels["z"])
    demod.configure_oscillator(hf2, OscillatorConfig(osc_index=ctx.params.demod3_osc_idx, frequency=frequency))

