"""XY DirectAW + Demod3 R 二维噪声谱采集工作流。"""

from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
from tqdm import tqdm

from gs200 import GS200Instrument
from lockin_amplifier import (
    DAQConfig,
    DemodulatorConfig,
    HF2Instrument,
    OscillatorConfig,
    SignalInputConfig,
    daq,
    demod,
)
from tec_controller import TECInstrument

from lab_workflows.common import (
    find_project_root,
    load_mapping,
    load_safety_limits,
    validate_safety_limit,
)
from lab_workflows.devices import create_signal_generator
from lab_workflows.experiment_runtime import check_cancelled, load_runtime_params
from lab_workflows.steps import (
    ArbitraryWaveformSpec,
    DGChannelShutdown,
    DeviceSession,
    DirectAWPhaseCalibrationConfig,
    DisconnectTarget,
    PhaseCalibrationConfig,
    STANDARD_PRESERVED_OUTPUTS,
    TemperatureSwitchRestore,
    calibrate_demod_phase,
    calibrate_direct_aw_phase,
    create_run_directory,
    run_safety_shutdown,
    set_temperature_switch,
    synchronize_connected_clocks,
    upload_arbitrary,
    wait_for_temperature_stable,
)

from .acquisition import acquire_demod_r_mean
from .models import NoiseSpectrumXYDemod3RParams
from .scan import build_scan_axes, estimate_scan_duration_s, iter_frequency_batches


EXPERIMENT_ID = "noise-spectrum-xy-demod3-r"
EXPERIMENT_TYPE = "Noise_Spectrum_XY_Demod3_R"
PURPOSE = "noise_spectroscopy_demod3_r"


def _interruptible_sleep(duration_s: float) -> None:
    """支持 GUI 取消的短等待。"""
    deadline = time.monotonic() + max(0.0, float(duration_s))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, deadline - time.monotonic()))


def _save_raw_matrix(
    path: Path,
    r_mean_v: np.ndarray,
    control_frequency_hz: np.ndarray,
    envelope_v: np.ndarray,
    demod3_frequency_hz: np.ndarray,
) -> None:
    """增量保存均值矩阵；连续采样数组不会写盘。"""
    np.savez(
        path,
        r_mean_V=np.asarray(r_mean_v, dtype=float),
        control_frequency_Hz=np.asarray(control_frequency_hz, dtype=float),
        xy_envelope_voltage_V=np.asarray(envelope_v, dtype=float),
        demod3_frequency_Hz=np.asarray(demod3_frequency_hz, dtype=float),
    )


def _build_waveforms(
    params: NoiseSpectrumXYDemod3RParams,
    envelope_v: float,
    phase_deg: float,
) -> tuple[np.ndarray, np.ndarray]:
    """生成固定 Vpp/Offset 下的 X/Y 归一化 DirectAW 数组。"""
    time_s = np.arange(params.xy_aw_points, dtype=float) / (
        params.xy_aw_points * params.xy_aw_repeat_freq_hz
    )
    phase = 2.0 * np.pi * params.xy_ctrl_freq * time_s + math.radians(phase_deg)
    x_v = float(envelope_v) * np.cos(phase)
    y_v = float(envelope_v) * np.cos(phase + math.radians(params.xy_ctrl_quad))
    half_range = params.xy_aw_output_vpp / 2.0
    output_low = params.xy_aw_output_offset_v - half_range
    output_high = params.xy_aw_output_offset_v + half_range
    for key, values in (("X_magnetic_field", x_v), ("Y_magnetic_field", y_v)):
        validate_safety_limit(key, float(np.min(values)))
        validate_safety_limit(key, float(np.max(values)))
        if np.min(values) < output_low - 1e-9 or np.max(values) > output_high + 1e-9:
            raise ValueError(
                f"{key} 波形超出固定 AW 可表达范围 [{output_low:.6f}, {output_high:.6f}] V"
            )
    x_norm = np.clip((x_v - params.xy_aw_output_offset_v) / half_range, -1.0, 1.0)
    y_norm = np.clip((y_v - params.xy_aw_output_offset_v) / half_range, -1.0, 1.0)
    return x_norm, y_norm


def run() -> Path:
    """执行完整采集并返回运行目录。"""
    project_root = find_project_root()
    params = load_runtime_params(NoiseSpectrumXYDemod3RParams)
    mapping = load_mapping(project_root)
    limits = load_safety_limits(project_root)
    control_axis, envelope_axis, demod3_axis = build_scan_axes(params)
    estimated_s = estimate_scan_duration_s(params)
    print(
        f"二维扫描 {params.control_freq_points}×{params.demod3_freq_points} 点，"
        f"理论耗时 {estimated_s:.0f} s ≈ {estimated_s / 3600:.2f} h（不含升温、校相和通信）"
    )

    session = DeviceSession()
    run_directory = None
    shutdown_report = None
    connected = False

    try:
        gs_cfg = mapping["main_magnetic_field"]
        gs = session.connect(
            "gs200",
            str(gs_cfg["resource"]),
            lambda: GS200Instrument(gs_cfg["resource"]),
        )
        gs.set_source_function(gs_cfg["source_function"])
        gs.set_current_limit(limits["main_magnetic_field"]["max"] / 1000.0)

        dg_comp_cfg = mapping["X_magnetic_field"]
        dg_comp = session.connect(
            "dg_comp",
            str(dg_comp_cfg["resource"]),
            lambda: create_signal_generator(dg_comp_cfg),
        )
        dg_trigger_cfg = mapping["X_magnetic_field_AM"]
        dg_trigger = session.connect(
            "dg_trigger",
            str(dg_trigger_cfg["resource"]),
            lambda: create_signal_generator(dg_trigger_cfg),
        )
        dg_sweep_cfg = mapping["Z_magnetic_field"]
        dg_sweep = session.connect(
            "dg_sweep",
            str(dg_sweep_cfg["resource"]),
            lambda: create_signal_generator(dg_sweep_cfg),
        )
        dg_mod_cfg = mapping["Pump_modulation"]
        dg_mod = session.connect(
            "dg_mod",
            str(dg_mod_cfg["resource"]),
            lambda: create_signal_generator(dg_mod_cfg),
        )

        dg_temp_cfg = mapping["Temp_Switch"]
        dg_temp = session.connect(
            "dg_temp",
            str(dg_temp_cfg["resource"]),
            lambda: create_signal_generator(dg_temp_cfg),
        )

        laser_cfg = mapping["Pump_laser_power"]
        probe_cfg = mapping["Probe_laser_power"]
        if laser_cfg["resource"] != probe_cfg["resource"] or laser_cfg["model"] != probe_cfg["model"]:
            raise ValueError("Pump/Probe 光功率未映射到同一台信号源")
        dg_laser = session.connect(
            "dg_laser",
            str(laser_cfg["resource"]),
            lambda: create_signal_generator(laser_cfg),
        )

        tec_cfg = mapping["temperature"]
        tec = session.connect(
            "tec",
            str(tec_cfg["resource"]),
            lambda: TECInstrument(port=tec_cfg["resource"]),
        )

        hf2_cfg = mapping["lockin_r"]
        hf2_resource = f"hf2://{hf2_cfg['device_id']}@{hf2_cfg.get('host', '127.0.0.1')}:{hf2_cfg.get('port', 8005)}"
        hfi = session.connect(
            "hf2",
            hf2_resource,
            lambda: HF2Instrument(
                host=hf2_cfg.get("host", "127.0.0.1"),
                port=hf2_cfg.get("port", 8005),
                api_level=1,
                device_id=hf2_cfg["device_id"],
            ),
        )
        clock_sources = synchronize_connected_clocks(
            {
                "dg_comp": dg_comp,
                "dg_trigger": dg_trigger,
                "dg_sweep": dg_sweep,
                "dg_mod": dg_mod,
                "dg_temp": dg_temp,
                "dg_laser": dg_laser,
                "hf2": hfi,
            },
            mapping,
            {
                "dg_comp": "X_magnetic_field",
                "dg_trigger": "X_magnetic_field_AM",
                "dg_sweep": "Z_magnetic_field",
                "dg_mod": "Pump_modulation",
                "dg_temp": "Temp_Switch",
                "dg_laser": "Pump_laser_power",
                "hf2": "lockin_r",
            },
        )
        connected = True

        def set_temp(enabled: bool) -> float:
            return set_temperature_switch(
                dg_temp,
                enabled,
                channel=int(dg_temp_cfg["channel"]),
                on_voltage=params.temp_switch,
                off_voltage=0.0,
            )

        # 所有初始输出设置前均执行物理量安全校验。
        validate_safety_limit("Pump_laser_power", params.pump_laser_power, limits)
        validate_safety_limit("Probe_laser_power", params.probe_laser_power, limits)
        dg_laser.setup_dc(params.pump_laser_power, channel=int(laser_cfg["channel"]))
        dg_laser.setup_dc(params.probe_laser_power, channel=int(probe_cfg["channel"]))

        validate_safety_limit("main_magnetic_field", params.main_magnetic_field, limits)
        gs.set_current(params.main_magnetic_field / 1000.0)
        gs.set_output(True)

        validate_safety_limit("Temp_Switch", params.temp_switch, limits)
        set_temp(True)
        validate_safety_limit("temperature", params.temperature, limits)
        tec.set_target_temperature(params.temperature, channel=1)
        tec.set_enable(True, channel=1)
        wait_for_temperature_stable(
            tec,
            params.temperature,
            channel=1,
            tolerance_c=params.temperature_tolerance_c,
            stable_reads=params.temperature_stable_reads,
            poll_interval_s=params.temperature_poll_interval_s,
            timeout_s=params.temperature_timeout_s,
        )

        for key, device, channel in (
            ("X_magnetic_field_AM", dg_trigger, 1),
            ("Y_magnetic_field_AM", dg_trigger, 2),
            ("Z_magnetic_field", dg_sweep, 1),
            ("Time_sequence_2", dg_sweep, 2),
        ):
            validate_safety_limit(key, 0.0, limits)
            device.setup_dc(0.0, channel=channel)
            device.set_output(False, channel=channel)

        run_directory = create_run_directory(
            EXPERIMENT_TYPE,
            params.run_tag,
            params.to_external(),
            schema_version=params.schema_version,
            project_root=project_root,
        )
        raw_matrix_path = run_directory.raw / "demod3_r_mean_matrix.npz"
        r_mean_matrix = np.full(
            (params.control_freq_points, params.demod3_freq_points),
            np.nan,
            dtype=float,
        )
        _save_raw_matrix(raw_matrix_path, r_mean_matrix, control_axis, envelope_axis, demod3_axis)
        run_directory.update_config(
            experiment_id=EXPERIMENT_ID,
            execution_mode="typed_workflow",
            purpose=PURPOSE,
            clock_sources=clock_sources,
            scan={
                "control_frequency_Hz": control_axis.tolist(),
                "xy_envelope_voltage_V": envelope_axis.tolist(),
                "demod3_frequency_Hz": demod3_axis.tolist(),
                "estimated_duration_s": estimated_s,
                "temperature_batch_points": params.temp_recovery_batch_points,
                "temperature_recovery_time_s": params.temp_recovery_time_s,
            },
            wiring={
                "physical_loopback": "Demod0 Y -> AuxOut2 -> Signal Input 2 DC -> Demod3",
                "signal": "Demod3 sample.r continuous mean",
            },
            data_files=["raw/demod3_r_mean_matrix.npz"],
        )

        # Pump 调制与门控。
        validate_safety_limit("Pump_modulation", params.pump_mod_amplitude, limits)
        dg_mod.setup_sine(100e6, params.pump_mod_amplitude, offset=0.0, phase=0.0, channel=1)
        gate_low = params.rf_gate_offset - params.rf_gate_amplitude / 2.0
        gate_high = params.rf_gate_offset + params.rf_gate_amplitude / 2.0
        validate_safety_limit("Time_sequence", gate_low, limits)
        validate_safety_limit("Time_sequence", gate_high, limits)
        pulse_width = (params.pump_mod_duty / 100.0) / params.pump_mod_freq
        dg_mod.setup_pulse(
            params.pump_mod_freq,
            params.rf_gate_amplitude,
            offset=params.rf_gate_offset,
            width=pulse_width,
            channel=2,
        )
        dg_mod.set_sync_state(False, channel=2)

        # 两路固定相位外触发保持连续运行。
        trigger_low = params.xy_trigger_offset - params.xy_trigger_amplitude / 2.0
        trigger_high = params.xy_trigger_offset + params.xy_trigger_amplitude / 2.0
        for key in ("X_magnetic_field_AM", "Y_magnetic_field_AM"):
            validate_safety_limit(key, trigger_low, limits)
            validate_safety_limit(key, trigger_high, limits)
        for channel in (1, 2):
            dg_trigger.set_burst_state(False, channel=channel)
            dg_trigger.set_mod_state(False, channel=channel)
            dg_trigger.setup_square(
                params.xy_trigger_freq,
                params.xy_trigger_amplitude,
                offset=params.xy_trigger_offset,
                dcycle=params.xy_trigger_duty,
                phase=params.xy_trigger_phase % 360.0,
                channel=channel,
            )
        for channel in (1, 2):
            dg_trigger.phase_init(channel=channel)

        # Demod0 配置与自动校相。
        set_temp(False)
        for channel in (1, 2):
            dg_comp.set_output(False, channel=channel)
        demod.configure_signal_input(
            hfi,
            SignalInputConfig(
                input_index=0,
                range=params.demod0_signal_range_v,
                ac_coupling=True,
                diff=False,
                impedance=50,
            ),
        )
        demod.configure_oscillator(
            hfi,
            OscillatorConfig(osc_index=params.demod0_osc_idx, frequency=params.pump_mod_freq),
        )
        actual_rate_d0 = demod.configure_demodulator(
            hfi,
            DemodulatorConfig(
                demod_index=params.demod0_idx,
                enable=True,
                rate=params.demod0_rate_sa_s,
                input_channel=0,
                osc_select=params.demod0_osc_idx,
                harmonic=1,
                time_constant=params.demod0_tc_calib_s,
                order=params.demod0_order,
                phase=0.0,
            ),
        )
        demod0_phase = calibrate_demod_phase(
            hfi,
            PhaseCalibrationConfig(
                demod_idx=params.demod0_idx,
                tolerance_deg=1.0,
                max_attempts=5,
                settle_time=0.2,
            ),
        )
        set_temp(True)

        # 物理回环：Demod0 Y -> AuxOut2 -> Signal Input 2 -> Demod3。
        demod.configure_signal_input(
            hfi,
            SignalInputConfig(
                input_index=params.sigin2_index,
                range=params.sigin2_range_v,
                ac_coupling=params.sigin2_ac_coupling,
                diff=False,
                impedance=params.sigin2_impedance_ohm,
            ),
        )
        auxout_path = hfi.aux_out_path(params.auxout_index)
        output_select = params.auxout_source_demod_idx * 4 + params.auxout_source_select
        hfi.set_int(f"{auxout_path}/outputselect", output_select)
        hfi.set_double(f"{auxout_path}/scale", params.auxout_scale)
        hfi.set_double(f"{auxout_path}/offset", params.auxout_offset_v)
        hfi.sync()

        def upload_direct_aw(envelope_v: float, phase_deg: float, outputs_on: bool = True) -> None:
            x_values, y_values = _build_waveforms(params, envelope_v, phase_deg)
            for channel, values in ((1, x_values), (2, y_values)):
                dg_comp.set_output(False, channel=channel)
                upload_arbitrary(
                    dg_comp,
                    ArbitraryWaveformSpec(
                        values=values,
                        frequency=params.xy_aw_repeat_freq_hz,
                        amplitude=params.xy_aw_output_vpp,
                        offset=params.xy_aw_output_offset_v,
                        phase=0.0,
                        channel=channel,
                        output=False,
                    ),
                )
            for channel in (1, 2):
                dg_comp.set_burst_state(True, channel=channel)
                dg_comp.set_burst_mode("INFinity", channel=channel)
                dg_comp.set_burst_ncycles(50000, channel=channel)
                dg_comp.set_burst_trigger_source("EXTernal", channel=channel)
                dg_comp.set_burst_trigger_slope("POSitive", channel=channel)
                dg_comp.set_burst_phase(0.0, channel=channel)
                dg_comp.set_output(bool(outputs_on), channel=channel)

        # DirectAW 自动校相；没有额外相位扫描。
        def apply_phase_and_measure(phase_deg: float):
            upload_direct_aw(params.xy_calib_envelope_v, phase_deg, outputs_on=True)
            set_temp(False)
            try:
                _interruptible_sleep(1.0)
                return demod.read_demod_sample(hfi, demod_idx=params.demod0_idx)
            finally:
                set_temp(True)
                time.sleep(1.0)

        direct_aw_phase = calibrate_direct_aw_phase(
            params.xy_ctrl_phase,
            apply_phase_and_measure,
            DirectAWPhaseCalibrationConfig(
                tolerance_deg=params.xy_phase_cal_tol_deg,
                max_measurements=params.xy_phase_cal_max_iter,
                minimum_r_v=params.xy_phase_cal_min_r_v,
                minimum_r_ratio=params.xy_phase_cal_min_r_ratio,
            ),
            cancellation_check=check_cancelled,
        )
        final_xy_phase_deg = direct_aw_phase.final_phase_deg
        set_temp(True)

        # Demod3 初始配置与硬件实际值快照。
        initial_frequency = float(demod3_axis[0])
        demod.configure_oscillator(
            hfi,
            OscillatorConfig(osc_index=params.demod3_osc_idx, frequency=initial_frequency),
        )
        initial_tc = max(params.demod3_tc_min_s, params.demod3_tc_period_fraction / initial_frequency)
        actual_rate_d3 = demod.configure_demodulator(
            hfi,
            DemodulatorConfig(
                demod_index=params.demod3_idx,
                enable=True,
                rate=params.demod3_rate_sa_s,
                input_channel=params.demod3_adc_select,
                osc_select=params.demod3_osc_idx,
                harmonic=1,
                time_constant=initial_tc,
                order=params.demod3_order,
                phase=0.0,
            ),
        )
        demod3_path = hfi.demod_path(params.demod3_idx)
        demod3_r_path = f"{demod3_path}/sample.r"
        r_collector = daq.DAQCollector(hfi)
        r_collector.configure(
            DAQConfig(
                device=hf2_cfg["device_id"],
                trigger_type=0,
                duration=params.demod3_acquisition_duration_s,
                grid_cols=max(
                    1,
                    int(round(actual_rate_d3 * params.demod3_acquisition_duration_s)),
                ),
                grid_rows=1,
                grid_mode=2,
                signal_paths=["sample.r"],
            )
        )
        r_collector.subscribe(["sample.r"], demod_idx=params.demod3_idx)
        run_directory.update_config(
            actual_rates={
                "demod0_calibration_Sa_s": float(actual_rate_d0),
                "demod3_Sa_s": float(actual_rate_d3),
            },
            hf2_snapshot={
                "demod0_phase_shift_deg": float(demod0_phase.phase_shift_deg),
                "direct_aw_phase_deg": float(final_xy_phase_deg),
                "direct_aw_phase_converged": bool(direct_aw_phase.converged),
                "demod3_rate_Sa_s": float(hfi.get_double(f"{demod3_path}/rate")),
                "demod3_time_constant_s": float(hfi.get_double(f"{demod3_path}/timeconstant")),
                "demod3_adcselect": int(hfi.get_int(f"{demod3_path}/adcselect")),
                "auxout_outputselect": int(hfi.get_int(f"{auxout_path}/outputselect")),
                "auxout_scale": float(hfi.get_double(f"{auxout_path}/scale")),
                "auxout_offset_V": float(hfi.get_double(f"{auxout_path}/offset")),
            },
        )

        total_points = params.control_freq_points * params.demod3_freq_points
        progress = tqdm(total=total_points, desc="Demod3 R scan", unit="pt")
        started = time.monotonic()
        try:
            for control_idx, (control_frequency, envelope_v) in enumerate(
                zip(control_axis, envelope_axis, strict=True)
            ):
                check_cancelled()
                upload_direct_aw(float(envelope_v), final_xy_phase_deg, outputs_on=True)

                for batch in iter_frequency_batches(
                    params.demod3_freq_points,
                    params.temp_recovery_batch_points,
                ):
                    set_temp(False)
                    try:
                        for demod_idx in batch:
                            check_cancelled()
                            frequency = float(demod3_axis[demod_idx])
                            demod.configure_oscillator(
                                hfi,
                                OscillatorConfig(
                                    osc_index=params.demod3_osc_idx,
                                    frequency=frequency,
                                ),
                            )
                            tc_s = max(
                                params.demod3_tc_min_s,
                                params.demod3_tc_period_fraction / frequency,
                            )
                            actual_rate_d3 = demod.configure_demodulator(
                                hfi,
                                DemodulatorConfig(
                                    demod_index=params.demod3_idx,
                                    enable=True,
                                    rate=params.demod3_rate_sa_s,
                                    input_channel=params.demod3_adc_select,
                                    osc_select=params.demod3_osc_idx,
                                    harmonic=1,
                                    time_constant=tc_s,
                                    order=params.demod3_order,
                                    phase=0.0,
                                ),
                            )
                            _interruptible_sleep(params.demod3_freq_settle_time_s)
                            try:
                                r_mean = acquire_demod_r_mean(
                                    r_collector,
                                    demod3_r_path,
                                    params.demod3_acquisition_duration_s,
                                )
                            except Exception as exc:
                                tqdm.write(
                                    f"控制 {control_frequency:.1f} Hz / Demod3 {frequency:.1f} Hz 采集失败: {exc}"
                                )
                                r_mean = float("nan")
                            if not np.isfinite(r_mean):
                                tqdm.write(
                                    f"控制 {control_frequency:.1f} Hz / Demod3 {frequency:.1f} Hz 未得到有效 R"
                                )
                            r_mean_matrix[control_idx, demod_idx] = r_mean
                            progress.set_postfix_str(
                                f"Ctrl={control_frequency/1000:.2f}kHz D3={frequency/1000:.2f}kHz"
                            )
                            progress.update(1)
                    finally:
                        set_temp(True)
                        time.sleep(params.temp_recovery_time_s)
                        _save_raw_matrix(
                            raw_matrix_path,
                            r_mean_matrix,
                            control_axis,
                            envelope_axis,
                            demod3_axis,
                        )
        finally:
            progress.close()
            r_collector.close()
            set_temp(True)

        elapsed_s = time.monotonic() - started
        run_directory.update_config(
            elapsed_scan_s=float(elapsed_s),
            actual_rates={
                "demod0_calibration_Sa_s": float(actual_rate_d0),
                "demod3_Sa_s": float(actual_rate_d3),
            },
            completed_points=int(np.count_nonzero(np.isfinite(r_mean_matrix))),
        )
        print(f"扫描完成，用时 {elapsed_s / 3600:.2f} h，数据保存于 {run_directory.root}")
        return run_directory.root

    except Exception:
        if not connected:
            session.cleanup_connection_failure()
        raise
    finally:
        if connected:
            shutdown_report = run_safety_shutdown(
                dg_channels=(
                    DGChannelShutdown(locals().get("dg_comp"), 1, "X_magnetic_field", "X DirectAW"),
                    DGChannelShutdown(locals().get("dg_comp"), 2, "Y_magnetic_field", "Y DirectAW"),
                    DGChannelShutdown(locals().get("dg_trigger"), 1, "X_magnetic_field_AM", "X 触发"),
                    DGChannelShutdown(locals().get("dg_trigger"), 2, "Y_magnetic_field_AM", "Y 触发"),
                    DGChannelShutdown(locals().get("dg_sweep"), 1, "Z_magnetic_field", "Z 场"),
                    DGChannelShutdown(locals().get("dg_sweep"), 2, "Time_sequence_2", "时序通道 2"),
                    DGChannelShutdown(locals().get("dg_mod"), 2, "Time_sequence", "Pump 门控"),
                ),
                temperature_switch=TemperatureSwitchRestore(
                    locals().get("dg_temp"),
                    int(locals().get("dg_temp_cfg", {}).get("channel", 2)),
                ),
                disconnect_targets=(DisconnectTarget("TEC", locals().get("tec")),),
                preserved_outputs=STANDARD_PRESERVED_OUTPUTS,
            )
            if run_directory is not None:
                run_directory.update_config(safety_shutdown=shutdown_report.to_dict())
            if shutdown_report.errors:
                print("安全收尾警告: " + "；".join(shutdown_report.errors))


if __name__ == "__main__":
    run()
