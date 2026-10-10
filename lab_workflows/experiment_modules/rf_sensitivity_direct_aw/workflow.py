# %% [markdown]
# # RF 灵敏度（DirectAW）采集工作流

# %% 导入与可取消等待
from __future__ import annotations

from lab_workflows.devices import create_lockin
from lab_workflows.lockin_compatibility import require_lockin_compatibility
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import math
import time
import sys

import numpy as np

from gs200 import GS200Instrument
from lockin_amplifier import HF2Instrument, DAQConfig, DemodulatorConfig, OscillatorConfig, SignalInputConfig, daq, demod
from tec_controller import TECInstrument
from ...common import CancellationToken, WorkflowCancelled, find_project_root, load_mapping, load_safety_limits, validate_safety_limit
from ...devices import signal_generator_max_arb_points
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...steps import (
    ArbitraryWaveformSpec, DeviceSession, DGChannelShutdown, DisconnectTarget,
    DirectAWPhaseCalibrationConfig, PhaseCalibrationConfig, STANDARD_PRESERVED_OUTPUTS, TemperatureSwitchRestore,
    calibrate_demod_phase, calibrate_direct_aw_phase, configure_temperature_control,
    connect_signal_generator_routes, create_run_directory, run_safety_shutdown,
    set_temperature_switch, synchronize_connected_clocks, upload_arbitrary,
)
from .models import RFSensitivityDirectAWParams
from .phase_plot import plot_z_phase_scan
from .run_format import DATA_TYPE, EXPERIMENT_ID, SIGNAL_CHAIN
from .waveform import load_envelope, synthesize_xy


class _RuntimeCancellation(CancellationToken):
    def raise_if_cancelled(self) -> None:
        super().raise_if_cancelled()
        check_cancelled()


def wait(seconds: float) -> None:
    deadline = time.monotonic() + max(0, seconds)
    check_cancelled()
    while (remaining := deadline - time.monotonic()) > 0:
        time.sleep(min(0.1, remaining))
        check_cancelled()


def connect_devices(mapping: dict) -> dict:
    session = DeviceSession()
    try:
        signals, channels = connect_signal_generator_routes(session, "signals", {
            name: (key, mapping[key]) for name, key in (
                ("x", "X_magnetic_field"), ("y", "Y_magnetic_field"),
                ("z", "Z_magnetic_field"), ("trigger", "Time_sequence_2"),
                ("pump_power", "Pump_laser_power"), ("probe_power", "Probe_laser_power"),
                ("pump_rf", "Pump_modulation"), ("pump_gate", "Time_sequence"),
                ("temp", "Temp_Switch"),
            )
        })
        gs_config, hf_config, tec_config = (mapping[key] for key in ("main_magnetic_field", "lockin_r", "temperature"))
        gs = session.connect("gs200", gs_config["resource"], lambda: GS200Instrument(gs_config["resource"]))
        hf2 = session.connect("hf2", f"hf2://{hf_config['host']}:{hf_config['port']}/{hf_config['device_id']}", lambda: create_lockin(hf_config))
        tec = session.connect_optional("tec", tec_config["resource"], lambda: TECInstrument(tec_config["resource"]), device_label="TEC")
        return {"signals": signals, "channels": channels, "gs200": gs, "hf2": hf2, "tec": tec, "optional_connection_errors": session.optional_connection_errors()}
    except BaseException:
        session.cleanup_connection_failure()
        raise


def validate_levels(key: str, amplitude: float, offset: float = 0.0) -> None:
    for value in (offset, offset - amplitude / 2, offset + amplitude / 2):
        validate_safety_limit(key, float(value))


def hf2_snapshot(hf2: HF2Instrument) -> dict:
    """保存实际节点，包含响应或噪声配置的实际采样率。"""
    snapshot = {}
    for base, integers, doubles in (
        (hf2.demod_path(0), ("enable", "adcselect", "oscselect", "harmonic", "order"), ("rate", "timeconstant", "phaseshift")),
        (hf2.demod_path(3), ("enable", "adcselect", "oscselect", "harmonic", "order"), ("rate", "timeconstant", "phaseshift")),
        (hf2.sig_in_path(0), ("ac", "imp50", "diff"), ("range",)),
        (hf2.sig_in_path(1), ("ac", "imp50", "diff"), ("range",)),
        (hf2.aux_out_path(1), ("outputselect", "demodselect"), ("scale", "offset")),
        (hf2.osc_path(0), (), ("freq",)), (hf2.osc_path(1), (), ("freq",)),
    ):
        for node in integers:
            snapshot[f"{base}/{node}"] = int(hf2.get_int(f"{base}/{node}"))
        for node in doubles:
            value = float(hf2.get_double(f"{base}/{node}"))
            if not math.isfinite(value):
                raise RuntimeError(f"HF2 回读非有限：{base}/{node}")
            snapshot[f"{base}/{node}"] = value
    return snapshot


def upload_xy(signals, channels: dict, params: RFSensitivityDirectAWParams, envelope: dict, phase: float) -> dict:
    waveforms = synthesize_xy(params, envelope, phase)
    for axis in ("x", "y"):
        check_cancelled()
        channel = channels[axis]
        validate_levels(f"{axis.upper()}_magnetic_field", params.xy_aw_output_vpp, params.xy_aw_output_offset)
        signals.set_output(False, channel=channel)
        upload_arbitrary(signals, ArbitraryWaveformSpec(waveforms[f"{axis}_normalized"], envelope["repeat_freq_Hz"], params.xy_aw_output_vpp, params.xy_aw_output_offset, channel=channel))
        signals.set_burst_mode("INFinity", channel=channel)
        signals.set_burst_ncycles(50000, channel=channel)
        signals.set_burst_trigger_source("EXTernal", channel=channel)
        signals.set_burst_trigger_slope("POSitive", channel=channel)
        signals.set_burst_phase(0.0, channel=channel)
        signals.set_burst_state(True, channel=channel)
    for axis in ("x", "y"):
        signals.set_output(True, channel=channels[axis])
    return waveforms


def arm_z(signals, channel: int, params: RFSensitivityDirectAWParams, amplitude: float, phase_base: float | None = None) -> float:
    """调幅只调用 set_amplitude；正负号由已校准的 Burst 相差表示。

    ``phase_base`` 为正 RF 的相位基准；缺省时用参数中的初始基准，
    RF 相位扫描完成后改用扫描选定的相位。
    """
    validate_levels("Z_magnetic_field", abs(amplitude))
    validate_safety_limit("Z_magnetic_field", abs(amplitude))
    base = params.z_burst_phase_init if phase_base is None else float(phase_base)
    phase = (base + (180 if amplitude < 0 else 0)) % 360
    signals.set_output(False, channel=channel)
    signals.set_burst_state(False, channel=channel)
    signals.set_burst_phase(phase, channel=channel)
    if abs(amplitude) > 1e-6:
        signals.set_amplitude(abs(amplitude), channel=channel)
        signals.set_burst_state(True, channel=channel)
        signals.set_output(True, channel=channel)
        wait(1.5 / params.xy_trigger_freq)
    return phase


# %% 正式采集
def run(params: RFSensitivityDirectAWParams) -> Path:
    require_lockin_compatibility('rf-sensitivity-aw', params.to_external())
    root = find_project_root()
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    envelope = load_envelope(params, min(signal_generator_max_arb_points(mapping[key]) for key in ("X_magnetic_field", "Y_magnetic_field")))
    check_cancelled()
    directory = create_run_directory(DATA_TYPE, params.run_tag, params.to_external(), schema_version=2, project_root=root)
    state = {
        "experiment_id": EXPERIMENT_ID, "execution_mode": "typed_workflow",
        "acquisition_method": "direct_aw_x", "started_at": datetime.now().isoformat(),
        "completion_status": "running", "analysis_status": "not_started",
        "phase_calibration_status": "not_started", "xy_phase_calibration": {"converged": False, "history": []},
        "rf_phase_calibration": {"points_completed": 0},
        "signal_chain": SIGNAL_CHAIN, "common_trigger": {"mapping_key": "Time_sequence_2", "output": "channel", "targets": ["X Ext Trig", "Y Ext Trig", "Z Ext Trig"], "frequency_Hz": params.xy_trigger_freq},
        "calibration": {"z_positive_field_phase_deg": params.z_burst_phase_init % 360, "z_negative_field_phase_deg": (params.z_burst_phase_init + 180) % 360, "z_v_to_nT": params.z_v_to_nt},
        "actual_rates": {}, "data_files": [], "response_points_completed": 0, "noise_records_completed": 0,
    }
    directory.update_config(**state)
    devices = {}
    original_error = None
    response_config = None
    cancellation = _RuntimeCancellation()
    try:
        report_runtime_progress("connect", "连接设备并严格同步参考时钟", 0)
        devices = connect_devices(mapping)
        signals, channels, hf2, gs = (devices[key] for key in ("signals", "channels", "hf2", "gs200"))
        state["clock_sync"] = synchronize_connected_clocks(devices, mapping, {"signals": "X_magnetic_field", "hf2": "lockin_r"}, settle_wait=wait)
        directory.update_config(**state)
        # setup_dc 会顺带打开输出（DG900 的 :APPLy:DC 自动开启），先归 DC 再统一关闭，
        # 确保 Demod0 校相时 XY/Z 控制场是真正关闭而不是 DC 0 V 输出。
        for name, key in (("x", "X_magnetic_field"), ("y", "Y_magnetic_field"), ("z", "Z_magnetic_field"), ("trigger", "Time_sequence_2"), ("pump_gate", "Time_sequence")):
            validate_safety_limit(key, 0.0)
            signals.setup_dc(0.0, channel=channels[name])
            signals.set_burst_state(False, channel=channels[name])
            signals.set_mod_state(False, channel=channels[name])
            signals.set_output(False, channel=channels[name])
        for name, key, voltage in (("pump_power", "Pump_laser_power", params.pump_laser_power), ("probe_power", "Probe_laser_power", params.probe_laser_power)):
            validate_safety_limit(key, voltage)
            signals.set_burst_state(False, channel=channels[name])
            signals.set_mod_state(False, channel=channels[name])
            signals.setup_dc(voltage, channel=channels[name])
            signals.set_output(True, channel=channels[name])
        validate_safety_limit("main_magnetic_field", params.main_magnetic_field)
        gs.set_source_function(mapping["main_magnetic_field"]["source_function"])
        gs.set_current_limit(load_safety_limits(root)["main_magnetic_field"]["max"] / 1000)
        gs.set_current(params.main_magnetic_field / 1000)
        gs.set_output(True)
        validate_safety_limit("Temp_Switch", params.temp_switch)
        signals.set_burst_state(False, channel=channels["temp"])
        signals.set_mod_state(False, channel=channels["temp"])
        set_temperature_switch(signals, True, channel=channels["temp"])
        state["temperature_control"] = configure_temperature_control(devices["tec"], params.temperature, channel=mapping["temperature"]["channel"], cancellation=cancellation).to_dict()
        state["optional_connection_errors"] = devices["optional_connection_errors"]
        validate_levels("Pump_modulation", params.pump_mod_amplitude)
        signals.set_burst_state(False, channel=channels["pump_rf"])
        signals.set_mod_state(False, channel=channels["pump_rf"])
        signals.setup_sine(freq=100e6, amplitude=params.pump_mod_amplitude, channel=channels["pump_rf"])
        signals.set_output(True, channel=channels["pump_rf"])
        validate_levels("Time_sequence", params.rf_gate_amplitude, params.rf_gate_offset)
        validate_safety_limit("PUMP_MOD_DUTY", params.pump_mod_duty)
        signals.setup_pulse(freq=params.pump_mod_freq, amplitude=params.rf_gate_amplitude, offset=params.rf_gate_offset, width=params.pump_mod_duty / 100 / params.pump_mod_freq, channel=channels["pump_gate"])
        signals.set_pulse_dcycle(params.pump_mod_duty, channel=channels["pump_gate"])
        signals.set_output(True, channel=channels["pump_gate"])
        signals.set_sync_state(True, channel=channels["pump_gate"])

        validate_levels("Time_sequence_2", params.xy_trigger_amplitude, params.xy_trigger_offset)
        signals.setup_square(freq=params.xy_trigger_freq, amplitude=params.xy_trigger_amplitude, offset=params.xy_trigger_offset, dcycle=params.xy_trigger_duty, phase=params.xy_trigger_phase, channel=channels["trigger"])
        signals.phase_init(channel=channels["trigger"])
        signals.set_output(True, channel=channels["trigger"])
        # 仪器在波形为 DC 时会自动关闭通道 SYNC，切回方波不会自动恢复，需显式重新打开；
        # 时序信号2 的主输出与 SYNC 均持续运行，含安全收尾之后。
        signals.set_sync_state(True, channel=channels["trigger"])
        # 此后仅操作被触发通道，共同触发持续运行到安全收尾。
        state["phase_calibration_status"] = "running"
        directory.update_config(**state)
        report_runtime_progress("phase", "Demod0 校相", 5)
        demod.configure_signal_input(hf2, SignalInputConfig(input_index=0, range=params.demod0_signal_range, ac_coupling=True, diff=False, impedance=50))
        demod.configure_oscillator(hf2, OscillatorConfig(osc_index=0, frequency=params.pump_mod_freq))
        d0_config = DemodulatorConfig(demod_index=0, rate=params.demod0_rate, input_channel=0, osc_select=0, time_constant=params.demod0_tc_calib, order=params.demod0_order)
        state["actual_rates"]["demod0_calib_Sa_s"] = float(demod.configure_demodulator(hf2, d0_config))
        set_temperature_switch(signals, False, channel=channels["temp"])
        wait(0.5)
        phase0 = calibrate_demod_phase(hf2, PhaseCalibrationConfig(demod_idx=0), settle_wait=wait, cancellation=cancellation)
        state["calibration"]["demod0"] = phase0.to_dict()
        set_temperature_switch(signals, True, channel=channels["temp"])
        wait(params.temp_switch_on_lag_s)
        initial_waveforms = synthesize_xy(params, envelope, params.xy_ctrl_phase)
        np.savez(directory.raw / "xy_aw_waveforms_initial.npz", **initial_waveforms)
        state["data_files"].append("raw/xy_aw_waveforms_initial.npz")
        report_runtime_progress("phase", "XY DirectAW 校相", 10)
        set_temperature_switch(signals, False, channel=channels["temp"])
        wait(0.5)

        def apply_and_measure(phase: float) -> dict:
            waveforms = upload_xy(signals, channels, params, envelope, phase)
            np.savez(directory.raw / "xy_aw_waveforms.npz", **waveforms)
            if "raw/xy_aw_waveforms.npz" not in state["data_files"]:
                state["data_files"].append("raw/xy_aw_waveforms.npz")
            wait(params.xy_phase_settle_time)
            sample = demod.read_demod_sample(hf2, 0)
            diagnostics = state["xy_phase_calibration"]
            finite = all(math.isfinite(float(sample[key])) for key in ("x", "y", "r", "theta"))
            best_r = max([float(item["r"]) for item in diagnostics["history"]] + [float(sample["r"])]) if finite else 0.0
            minimum = max(params.xy_ctrl_phase_min_r_v, best_r * params.xy_ctrl_phase_min_r_ratio)
            diagnostics["history"].append({"phase_deg": float(phase), **{key: float(sample[key]) if math.isfinite(float(sample[key])) else None for key in ("x", "y", "r", "theta")}, "minimum_r_v": minimum})
            directory.update_config(**state)
            if not finite or sample["r"] < minimum:
                raise RuntimeError("XY 校相信号低于门槛或含非有限值，停止测量")
            return sample

        xy_phase = calibrate_direct_aw_phase(params.xy_ctrl_phase, apply_and_measure, DirectAWPhaseCalibrationConfig(tolerance_deg=params.xy_ctrl_phase_tol_deg, max_measurements=params.xy_ctrl_phase_max_iter + 1, minimum_r_v=params.xy_ctrl_phase_min_r_v, minimum_r_ratio=params.xy_ctrl_phase_min_r_ratio), cancellation_check=check_cancelled)
        state["xy_phase_calibration"] = xy_phase.to_dict()
        directory.update_config(**state)
        if not xy_phase.converged:
            raise RuntimeError("XY 校相未收敛，停止测量")
        set_temperature_switch(signals, True, channel=channels["temp"])
        wait(params.temp_switch_on_lag_s)

        report_runtime_progress("phase", "配置 HF2 回环与 Demod3 响应滤波", 12)
        demod.configure_signal_input(hf2, SignalInputConfig(input_index=1, range=params.loopback_input_range_v, ac_coupling=params.loopback_input_ac, diff=params.loopback_input_diff, impedance=params.loopback_input_impedance_ohm))
        # HF2 分别使用 outputselect 和 demodselect，避免套用其他型号的合并枚举。
        validate_safety_limit("lockin_r", params.loopback_aux_offset_v)
        hf2.set_int(f"{hf2.aux_out_path(1)}/outputselect", 1)
        hf2.set_int(f"{hf2.aux_out_path(1)}/demodselect", 0)
        validate_safety_limit("lockin_r", params.loopback_aux_scale)
        hf2.set_double(f"{hf2.aux_out_path(1)}/scale", params.loopback_aux_scale)
        hf2.set_double(f"{hf2.aux_out_path(1)}/offset", params.loopback_aux_offset_v)
        hf2.sync()
        for node, expected in ((f"{hf2.aux_out_path(1)}/outputselect", 1), (f"{hf2.aux_out_path(1)}/demodselect", 0)):
            if hf2.get_int(node) != expected:
                raise RuntimeError(f"HF2 回环源回读不一致：{node}")
        demod.configure_oscillator(hf2, OscillatorConfig(osc_index=1, frequency=params.z_rf_freq))
        state["actual_rates"]["demod0_meas_Sa_s"] = float(demod.configure_demodulator(hf2, replace(d0_config, time_constant=params.demod0_tc_meas, phase=phase0.phase_shift_deg)))
        response_config = DemodulatorConfig(demod_index=3, rate=params.demod3_rate, input_channel=1, osc_select=1, time_constant=params.demod3_tc, order=params.demod3_order)
        state["actual_rates"]["demod3_response_Sa_s"] = float(demod.configure_demodulator(hf2, response_config))
        if hf2.get_int(f"{hf2.demod_path(3)}/adcselect") != 1:
            raise RuntimeError("Demod3 必须实际使用 Signal Input2")
        validate_levels("Z_magnetic_field", params.z_rf_amplitude_init)
        signals.setup_sine(freq=params.z_rf_freq, amplitude=params.z_rf_amplitude_init, offset=0.0, phase=0.0, channel=channels["z"])
        signals.set_burst_mode("INFinity", channel=channels["z"])
        signals.set_burst_trigger_source("EXTernal", channel=channels["z"])
        signals.set_burst_trigger_slope("POSitive", channel=channels["z"])
        arm_z(signals, channels["z"], params, params.z_rf_amplitude_init)
        # 第 3 步校准：扫描 Z RF Burst 相位，对齐控制 AM 调制与射频场的相位。
        # 延迟导入：mx_z 包的 __init__ 在导入期解析项目根目录，保持模块导入无副作用。
        from ..mx_z_optimal_control_rf_sensitivity.phase import fit_quadrature_phase_scan, paired_phase_order
        report_runtime_progress("phase", "扫描 Z RF 触发相位", 15)
        scan_axis = params.z_phase_scan_start_deg + params.z_phase_scan_step_deg * np.arange(int(round((params.z_phase_scan_stop_deg - params.z_phase_scan_start_deg) / params.z_phase_scan_step_deg)) + 1)
        scan_order = paired_phase_order(scan_axis)
        scan_rate = state["actual_rates"]["demod3_response_Sa_s"]
        scan_config = DAQConfig(device=hf2.device_id, duration=params.z_phase_scan_duration_s, grid_cols=max(8, int(scan_rate * params.z_phase_scan_duration_s)), grid_rows=1, grid_mode=2, signal_paths=["sample.r", "sample.x", "sample.y"])
        scan_r, scan_x, scan_y = [], [], []
        scan_arrays: dict = {}
        set_temperature_switch(signals, False, channel=channels["temp"])
        wait(0.5)
        for index, scan_phase in enumerate(scan_order):
            check_cancelled()
            arm_z(signals, channels["z"], params, params.z_rf_amplitude_init, phase_base=float(scan_phase))
            wait(params.rf_settle_time)
            results = daq.acquire_data(hf2, scan_config, demod_idx=3, actual_rate=scan_rate, timeout=params.z_phase_scan_duration_s + 10, cancellation_check=check_cancelled, strict_cleanup=True)
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
                "z_rf_amplitude_vpp": params.z_rf_amplitude_init, "actual_rate_Sa_s": scan_rate, "duration_s": params.z_phase_scan_duration_s,
            }
            np.savez(directory.raw / "z_phase_scan.npz", **scan_arrays)
            state["rf_phase_calibration"]["points_completed"] = index + 1
            if "raw/z_phase_scan.npz" not in state["data_files"]:
                state["data_files"].append("raw/z_phase_scan.npz")
            directory.update_config(**state)
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
        report_runtime_progress("phase", "校准 Demod3", 20)
        arm_z(signals, channels["z"], params, params.z_rf_amplitude_init, phase_base=z_positive_phase)
        set_temperature_switch(signals, False, channel=channels["temp"])
        wait(0.5)
        phase3 = calibrate_demod_phase(hf2, PhaseCalibrationConfig(demod_idx=3), settle_wait=wait, cancellation=cancellation)
        response_config = replace(response_config, phase=phase3.phase_shift_deg)
        state["calibration"]["demod3"] = phase3.to_dict()
        state["phase_calibration_status"] = "completed"
        state["hf2_response_snapshot"] = hf2_snapshot(hf2)
        directory.update_config(**state)
        set_temperature_switch(signals, True, channel=channels["temp"])
        wait(params.temp_switch_on_lag_s)

        amplitudes = np.linspace(params.rf_amp_start, params.rf_amp_stop, params.rf_amp_points)
        records = {key: [] for key in ("x", "y", "r", "theta")}
        phases = []
        total = len(amplitudes) + params.noise_n_avg
        started = time.monotonic()

        def progress(stage: str, completed: int) -> None:
            report_runtime_progress(stage, f"已完成 {completed}/{total} 个采集单元", 25 + 65 * completed / total, estimated_remaining_seconds=(time.monotonic() - started) / completed * (total - completed))

        for index, amplitude in enumerate(amplitudes):
            check_cancelled()
            phase = arm_z(signals, channels["z"], params, float(amplitude), phase_base=z_positive_phase)
            wait(params.rf_settle_time)
            set_temperature_switch(signals, False, channel=channels["temp"])
            wait(0.5)
            samples = []
            for _ in range(params.response_n_avg):
                check_cancelled()
                sample = demod.read_demod_sample(hf2, 3)
                if not all(math.isfinite(float(sample[key])) for key in records):
                    raise RuntimeError("响应采样包含非有限值")
                samples.append(sample)
                wait(params.response_avg_interval)
            for key in ("x", "y", "r"):
                records[key].append(float(np.mean([sample[key] for sample in samples])))
            records["theta"].append(float(np.degrees(np.angle(np.mean(np.exp(1j * np.asarray([sample["theta"] for sample in samples])))))))
            phases.append(phase)
            np.savez(directory.raw / "response_data.npz", amplitudes_V=amplitudes[:index + 1], amplitudes_nT=amplitudes[:index + 1] * params.z_v_to_nt, demod3_x_V=records["x"], demod3_y_V=records["y"], demod3_r_V=records["r"], demod3_theta_deg=records["theta"], z_burst_phase_deg=phases, z_rf_freq_Hz=params.z_rf_freq, z_v_to_nT=params.z_v_to_nt, xy_ctrl_phase_deg=xy_phase.final_phase_deg, xy_ctrl_quad_deg=params.xy_ctrl_quad)
            state["response_points_completed"] = index + 1
            if "raw/response_data.npz" not in state["data_files"]:
                state["data_files"].append("raw/response_data.npz")
            directory.update_config(**state)
            set_temperature_switch(signals, True, channel=channels["temp"])
            wait(params.temp_switch_on_lag_s)
            progress("response", index + 1)

        arm_z(signals, channels["z"], params, 0.0)
        try:
            actual_rate = float(demod.configure_demodulator(hf2, replace(response_config, rate=params.noise_rate, time_constant=params.demod3_noise_tc, order=params.demod3_noise_order)))
            if not math.isfinite(actual_rate) or actual_rate <= 0 or actual_rate * params.noise_duration < 2:
                raise RuntimeError("实际噪声采样率或记录长度无效")
            state["actual_rates"]["demod3_noise_Sa_s"] = actual_rate
            state["hf2_noise_snapshot"] = hf2_snapshot(hf2)
            directory.update_config(**state)
            daq_config = DAQConfig(device=hf2.device_id, duration=params.noise_duration, grid_cols=int(actual_rate * params.noise_duration), grid_rows=1, grid_mode=2, signal_paths=["sample.x"])
            for index in range(params.noise_n_avg):
                check_cancelled()
                set_temperature_switch(signals, False, channel=channels["temp"])
                wait(0.5)
                results = daq.acquire_data(hf2, daq_config, demod_idx=3, actual_rate=actual_rate, timeout=params.noise_duration + 10, cancellation_check=check_cancelled, strict_cleanup=True)
                if not results or results[0].values.size < 2 or not np.all(np.isfinite(results[0].values)):
                    raise RuntimeError("噪声 DAQ 未返回有效 X 数据")
                name = f"noise_D3_{index:04d}.npz"
                np.savez(directory.raw / name, noise_x_V=results[0].values, time_s=results[0].time, actual_rate_Sa_s=actual_rate, demod_idx=3, signal_path="sample.x", noise_duration_s=params.noise_duration)
                state["data_files"].append(f"raw/{name}")
                state["noise_records_completed"] = index + 1
                directory.update_config(**state)
                set_temperature_switch(signals, True, channel=channels["temp"])
                wait(params.temp_switch_on_lag_s)
                progress("noise", len(amplitudes) + index + 1)
        finally:
            # 包含 DAQ 异常和取消，恢复响应滤波与已校准相位。
            active_error = sys.exc_info()[0]
            try:
                demod.configure_demodulator(hf2, response_config)
            except Exception as exc:
                state["hf2_restore_error"] = str(exc)
                if active_error is None:
                    raise
        check_cancelled()
        state["completion_status"] = "completed"
    except BaseException as exc:
        original_error = exc
        state["completion_status"] = "cancelled" if isinstance(exc, WorkflowCancelled) else "failed"
        state["failure_reason"] = str(exc)
        if state["phase_calibration_status"] == "running":
            state["phase_calibration_status"] = "failed"
        raise
    finally:
        report_runtime_progress("shutdown", "执行安全收尾", 95)
        signals = devices.get("signals")
        channels = devices.get("channels", {})
        report = run_safety_shutdown(
            # 时序信号2（共同触发及其 SYNC）与 Pump 门控脉冲按实验要求在收尾后继续运行。
            dg_channels=[DGChannelShutdown(signals, channels[name], key, label) for name, key, label in (("x", "X_magnetic_field", "X DirectAW"), ("y", "Y_magnetic_field", "Y DirectAW"), ("z", "Z_magnetic_field", "Z RF")) if name in channels],
            temperature_switch=TemperatureSwitchRestore(signals, channels["temp"]) if "temp" in channels else None,
            disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
            preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence_2", "Time_sequence"),
        )
        state["safety_shutdown"] = report.to_dict()
        if not report.completed and original_error is None:
            state["completion_status"] = "failed"
            state["failure_reason"] = "安全收尾失败：" + "；".join(report.errors)
        if devices.get("hf2") is not None:
            try:
                state["hf2_final_snapshot"] = hf2_snapshot(devices["hf2"])
            except Exception as exc:
                state["hf2_snapshot_error"] = str(exc)
        state["finished_at"] = datetime.now().isoformat()
        directory.update_config(**state)
        report_runtime_progress("finished", f"采集{state['completion_status']}：{directory.root}", 100)
        if not report.completed and original_error is None:
            raise RuntimeError(state["failure_reason"])
    return directory.root


# %% 子进程入口
def main() -> None:
    run(load_runtime_params(RFSensitivityDirectAWParams))


if __name__ == "__main__":
    main()
