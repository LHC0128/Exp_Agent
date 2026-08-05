"""Mx 主磁场控制噪声谱采集工作流。"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import numpy as np
from lockin_amplifier import (
    DemodulatorConfig,
    OscillatorConfig,
    SignalInputConfig,
    demod,
)

from ...common import (
    find_project_root,
    load_mapping,
    load_safety_limits,
    validate_safety_limit,
)
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DGChannelShutdown,
    DeviceSession,
    DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS,
    SafetyShutdownReport,
    ShutdownAction,
    TemperatureSwitchRestore,
    configure_temperature_control,
    create_run_directory,
    restore_main_field_state,
    run_safety_shutdown,
    set_temperature_switch,
    snapshot_main_field_state,
    wait_for_temperature_stable,
)
from ...steps.state import StateGuard
from ..mx_y_rf_sensitivity.acquisition import acquire_r, summarize_r
from ..mx_z_field_calibration.workflow import (
    _configure_reference_clocks,
    _connect_devices,
    _initial_state_snapshot,
    _set_pump_gate_on,
)
from .models import MxMainFieldNoiseSpectrumParams
from .scan import build_scan_axes


EXPERIMENT_ID = "mx-main-field-noise-spectrum"
DATA_TYPE = "Mx_Main_Field_Noise_Spectrum"
EXECUTION_MODE = "typed_workflow"


class _RuntimeCancellation:
    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


def _sleep(seconds: float) -> None:
    """支持 GUI 取消的短等待。"""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _validate_calibrated_current(
    params: MxMainFieldNoiseSpectrumParams, current_ma: float
) -> float:
    """同时校验全局安全限值和实测标定有效范围。"""
    validate_safety_limit("main_magnetic_field", float(current_ma))
    if not (
        params.main_field_calibration_min_ma
        <= current_ma
        <= params.main_field_calibration_max_ma
    ):
        raise ValueError(
            f"主场电流 {current_ma:.9g} mA 超出标定有效范围 "
            f"[{params.main_field_calibration_min_ma}, "
            f"{params.main_field_calibration_max_ma}] mA"
        )
    return float(current_ma)


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    params: MxMainFieldNoiseSpectrumParams,
    main_field_guard: StateGuard | None = None,
) -> SafetyShutdownReport:
    """关闭辅助场、恢复温控并恢复 GS200 运行前状态。"""
    dg_channels: list[DGChannelShutdown] = []
    if devices.get("z_field") is not None and "z_field" in channels:
        dg_channels.append(
            DGChannelShutdown(
                devices["z_field"],
                channels["z_field"],
                "Z_magnetic_field",
                "Z 辅助场",
            )
        )
    if devices.get("xy_field") is not None:
        if "x_field" in channels:
            dg_channels.append(
                DGChannelShutdown(
                    devices["xy_field"],
                    channels["x_field"],
                    "X_magnetic_field",
                    "X 磁场",
                )
            )
        if "y_rf" in channels:
            dg_channels.append(
                DGChannelShutdown(
                    devices["xy_field"],
                    channels["y_rf"],
                    "Y_magnetic_field",
                    "Y 磁场",
                )
            )

    extra_actions: list[ShutdownAction] = []
    pump_rf = devices.get("pump_rf")
    if pump_rf is not None and "pump_gate" in channels:
        extra_actions.append(
            ShutdownAction(
                "保持 Pump RF 开关 5V 常开失败",
                lambda: _set_pump_gate_on(
                    pump_rf,
                    channels["pump_gate"],
                    params.pump_gate_voltage_v,
                ),
            )
        )
    if pump_rf is not None and "pump_carrier" in channels:

        def keep_pump_carrier_on() -> None:
            validate_safety_limit(
                "Pump_modulation", params.pump_carrier_amplitude_vpp
            )
            pump_rf.setup_sine(
                params.pump_carrier_frequency_hz,
                params.pump_carrier_amplitude_vpp,
                offset=0.0,
                phase=0.0,
                channel=channels["pump_carrier"],
            )
            pump_rf.set_output(True, channel=channels["pump_carrier"])

        extra_actions.append(
            ShutdownAction("保持 Pump 100MHz 载波开启失败", keep_pump_carrier_on)
        )

    temperature_restore = None
    if devices.get("temp_switch") is not None and "temp_switch" in channels:
        temperature_restore = TemperatureSwitchRestore(
            devices["temp_switch"], channels["temp_switch"]
        )
    report = run_safety_shutdown(
        dg_channels=dg_channels,
        temperature_switch=temperature_restore,
        extra_actions=extra_actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )
    guard_errors = tuple(main_field_guard.restore()) if main_field_guard else ()
    return SafetyShutdownReport(
        action_errors=(*report.action_errors, *guard_errors),
        disconnect_errors=report.disconnect_errors,
        preserved_outputs=report.preserved_outputs,
    )


def _configure_outputs(
    params: MxMainFieldNoiseSpectrumParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    mapping: dict[str, dict[str, Any]],
) -> tuple[float, float | None, dict[str, dict[str, str]], dict[str, Any]]:
    """配置 Mx 连续 Pump、首个主场点与固定 90 kHz Demod0。"""
    clock_sources = _configure_reference_clocks(devices, mapping)

    z_field = devices["z_field"]
    validate_safety_limit("Z_magnetic_field", 0.0)
    z_field.set_burst_state(False, channel=channels["z_field"])
    z_field.set_mod_state(False, channel=channels["z_field"])
    z_field.setup_dc(0.0, channel=channels["z_field"])
    z_field.set_output(False, channel=channels["z_field"])

    xy_field = devices["xy_field"]
    for channel_name, safety_key in (
        ("x_field", "X_magnetic_field"),
        ("y_rf", "Y_magnetic_field"),
    ):
        validate_safety_limit(safety_key, 0.0)
        channel = channels[channel_name]
        xy_field.set_burst_state(False, channel=channel)
        xy_field.set_mod_state(False, channel=channel)
        xy_field.setup_dc(0.0, channel=channel)
        xy_field.set_output(False, channel=channel)

    laser = devices["laser"]
    validate_safety_limit("Pump_laser_power", params.pump_laser_power_v)
    laser.setup_dc(params.pump_laser_power_v, channel=channels["pump_laser"])
    laser.set_output(True, channel=channels["pump_laser"])
    validate_safety_limit("Probe_laser_power", params.probe_laser_power_v)
    laser.setup_dc(params.probe_laser_power_v, channel=channels["probe_laser"])
    laser.set_output(True, channel=channels["probe_laser"])

    axes = build_scan_axes(params)
    initial_current_ma = _validate_calibrated_current(
        params, float(axes["main_field_current_ma"][0])
    )
    gs200 = devices["gs200"]
    gs_cfg = mapping["main_magnetic_field"]
    if gs_cfg.get("source_function"):
        gs200.set_source_function(gs_cfg["source_function"])
    limits = load_safety_limits()
    gs200.set_current_limit(limits["main_magnetic_field"]["max"] / 1000.0)
    gs200.set_current(initial_current_ma / 1000.0)
    gs200.set_output(True)

    pump_rf = devices["pump_rf"]
    validate_safety_limit("Pump_modulation", params.pump_carrier_amplitude_vpp)
    pump_rf.setup_sine(
        params.pump_carrier_frequency_hz,
        params.pump_carrier_amplitude_vpp,
        offset=0.0,
        phase=0.0,
        channel=channels["pump_carrier"],
    )
    pump_rf.set_output(True, channel=channels["pump_carrier"])
    _set_pump_gate_on(
        pump_rf,
        channels["pump_gate"],
        params.pump_gate_voltage_v,
    )

    set_temperature_switch(
        devices["temp_switch"], True, channel=channels["temp_switch"]
    )
    temperature_status = configure_temperature_control(
        devices.get("tec"),
        params.temperature_c,
        channel=1,
        tolerance_c=params.temperature_tolerance_c,
        stable_reads=params.temperature_stable_reads,
        poll_interval_s=params.temperature_poll_interval_s,
        timeout_s=params.temperature_timeout_s,
        cancellation=_RuntimeCancellation(),
        stability_waiter=wait_for_temperature_stable,
    )
    actual_temperature = temperature_status.actual_temperature_c

    hf2 = devices["hf2"]
    demod.configure_signal_input(
        hf2,
        SignalInputConfig(
            input_index=0,
            range=params.hf2_signal_range_v,
            ac_coupling=True,
            diff=False,
            impedance=50,
        ),
    )
    demod.configure_oscillator(
        hf2,
        OscillatorConfig(
            osc_index=params.demod_osc_idx,
            frequency=params.hf2_reference_frequency_hz,
        ),
    )
    phase_shift_deg = float(
        hf2.get_double(f"{hf2.demod_path(params.demod_idx)}/phaseshift")
    )
    actual_rate = float(
        demod.configure_demodulator(
            hf2,
            DemodulatorConfig(
                demod_index=params.demod_idx,
                enable=True,
                rate=params.requested_rate_sa_s,
                input_channel=0,
                osc_select=params.demod_osc_idx,
                harmonic=1,
                time_constant=params.demod_time_constant_s,
                order=params.demod_order,
                phase=phase_shift_deg,
            ),
        )
    )
    hf2_snapshot = {
        "demod_idx": params.demod_idx,
        "oscillator_idx": params.demod_osc_idx,
        "oscillator_frequency_hz": params.hf2_reference_frequency_hz,
        "requested_rate_sa_s": params.requested_rate_sa_s,
        "actual_rate_sa_s": actual_rate,
        "time_constant_s": params.demod_time_constant_s,
        "order": params.demod_order,
        "phase_shift_deg": phase_shift_deg,
        "signal_input": {
            "input_index": 0,
            "range_v": params.hf2_signal_range_v,
            "ac_coupling": True,
            "differential": False,
            "impedance_ohm": 50,
        },
    }
    temperature_text = (
        f"{actual_temperature:.2f} °C"
        if actual_temperature is not None
        else "由外部软件控制（未读取）"
    )
    print(
        f"Mx 主场噪声谱工作点已配置，温度 {temperature_text}，"
        f"Demod0 实际速率 {actual_rate:.6f} Sa/s"
    )
    return actual_rate, actual_temperature, clock_sources, hf2_snapshot


def _temperature_gated_acquire(
    params: MxMainFieldNoiseSpectrumParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    acquire: Callable[[], dict[str, np.ndarray]],
) -> dict[str, np.ndarray]:
    """关闭温控后采集，并在恢复温控后完成规定等待。"""
    check_cancelled()
    set_temperature_switch(
        devices["temp_switch"], False, channel=channels["temp_switch"]
    )
    try:
        _sleep(params.temp_switch_off_settle_s)
        return acquire()
    finally:
        set_temperature_switch(
            devices["temp_switch"],
            True,
            channel=channels["temp_switch"],
            settle_time=params.temp_switch_on_settle_s,
        )


def _save_point(
    path: Path,
    payload: dict[str, np.ndarray],
    *,
    actual_rate_sa_s: float,
    point_index: int,
    control_frequency_hz: float,
    signed_detuning_hz: float,
    resonance_frequency_hz: float,
    main_field_current_ma: float,
    temp_switch_off_settle_s: float,
    temp_switch_on_settle_s: float,
) -> None:
    summary = summarize_r(payload)
    np.savez(
        path,
        time_s=np.asarray(payload["time_s"], dtype=float),
        r_v=np.asarray(payload["r"], dtype=float),
        point_index=np.int64(point_index),
        control_frequency_hz=np.float64(control_frequency_hz),
        signed_detuning_hz=np.float64(signed_detuning_hz),
        resonance_frequency_hz=np.float64(resonance_frequency_hz),
        main_field_current_ma=np.float64(main_field_current_ma),
        actual_rate_sa_s=np.float64(actual_rate_sa_s),
        temp_switch_off_voltage_v=np.float64(0.0),
        temp_switch_on_voltage_v=np.float64(5.0),
        temp_switch_off_settle_s=np.float64(temp_switch_off_settle_s),
        temp_switch_on_settle_s=np.float64(temp_switch_on_settle_s),
        r_mean_v=np.float64(summary["r_mean_v"]),
        r_std_v=np.float64(summary["r_std_v"]),
        n_samples=np.int64(summary["n_samples"]),
    )


def _acquire_scan(
    params: MxMainFieldNoiseSpectrumParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate_sa_s: float,
    device_id: str,
) -> list[str]:
    axes = build_scan_axes(params)
    control_axis = axes["control_frequency_hz"]
    signed_axis = axes["signed_detuning_hz"]
    resonance_axis = axes["resonance_frequency_hz"]
    current_axis = axes["main_field_current_ma"]
    axes_path = run_dir.raw / "main_field_noise_scan_axes.npz"
    np.savez(
        axes_path,
        control_frequency_hz=control_axis,
        signed_detuning_hz=signed_axis,
        resonance_frequency_hz=resonance_axis,
        main_field_current_ma=current_axis,
        hf2_reference_frequency_hz=np.float64(
            params.hf2_reference_frequency_hz
        ),
        main_field_calibration_hz_per_ma=np.float64(
            params.main_field_calibration_hz_per_ma
        ),
        main_field_calibration_intercept_hz=np.float64(
            params.main_field_calibration_intercept_hz
        ),
    )

    gs200 = devices["gs200"]
    files = ["raw/main_field_noise_scan_axes.npz"]
    for index, (control, signed, resonance, current_ma) in enumerate(
        zip(
            control_axis,
            signed_axis,
            resonance_axis,
            current_axis,
            strict=True,
        )
    ):
        check_cancelled()
        print(
            f"主场噪声点 {index + 1}/{control_axis.size}: "
            f"Ω={control:.3f} Hz，Δf={signed:.3f} Hz，"
            f"I={current_ma:.9f} mA"
        )
        _validate_calibrated_current(params, float(current_ma))
        gs200.set_current(float(current_ma) / 1000.0)
        gs200.set_output(True)

        payload = _temperature_gated_acquire(
            params,
            devices,
            channels,
            acquire=lambda: acquire_r(
                devices["hf2"],
                device_id=device_id,
                demod_idx=params.demod_idx,
                actual_rate_sa_s=actual_rate_sa_s,
                duration_s=params.acquisition_duration_s,
            ),
        )
        relative = f"raw/waveform_I{index:04d}.npz"
        _save_point(
            run_dir.root / relative,
            payload,
            actual_rate_sa_s=actual_rate_sa_s,
            point_index=index,
            control_frequency_hz=float(control),
            signed_detuning_hz=float(signed),
            resonance_frequency_hz=float(resonance),
            main_field_current_ma=float(current_ma),
            temp_switch_off_settle_s=params.temp_switch_off_settle_s,
            temp_switch_on_settle_s=params.temp_switch_on_settle_s,
        )
        files.append(relative)
    return files


def run(params: MxMainFieldNoiseSpectrumParams) -> Path:
    """执行 500 点 GS200 主场控制噪声谱采集。"""
    root = find_project_root()
    mapping = load_mapping(root)
    axes = build_scan_axes(params)
    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        geometry={
            "main_field": "Z scanned by GS200",
            "pump": "Z",
            "probe": "X",
            "z_auxiliary_field": "0 V output OFF",
            "x_field_output": "OFF",
            "y_field_output": "OFF",
        },
        analysis_during_acquisition=False,
        acquisition_signals=["Demod0 R"],
        control_axis={
            "definition": "control_Hz = HF2_reference_Hz - (K_f_Hz_per_mA * current_mA + f_0mA_Hz)",
            "signed_detuning_definition": "signed_detuning_Hz = -control_Hz",
            "start_hz": params.control_frequency_start_hz,
            "stop_hz": params.control_frequency_stop_hz,
            "points": params.control_frequency_points,
            "main_field_current_start_ma": float(
                axes["main_field_current_ma"][0]
            ),
            "main_field_current_stop_ma": float(
                axes["main_field_current_ma"][-1]
            ),
        },
        main_field_calibration={
            "source_run": params.main_field_calibration_source_run,
            "slope_hz_per_ma": params.main_field_calibration_hz_per_ma,
            "intercept_hz": params.main_field_calibration_intercept_hz,
            "valid_current_min_ma": params.main_field_calibration_min_ma,
            "valid_current_max_ma": params.main_field_calibration_max_ma,
        },
        temperature_gating={
            "scope": "every_main_field_point",
            "off_voltage_v": 0.0,
            "on_voltage_v": 5.0,
            "off_settle_s": params.temp_switch_off_settle_s,
            "post_acquisition_wait_s": params.temp_switch_on_settle_s,
        },
        averaging={"records_per_main_field_point": 1, "enabled": False},
        estimated_scan_duration_s=params.control_frequency_points
        * (
            params.temp_switch_off_settle_s
            + params.acquisition_duration_s
            + params.temp_switch_on_settle_s
        ),
    )

    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    main_field_guard = StateGuard()
    completion_status = "failed"
    failure_reason: str | None = None
    try:
        check_cancelled()
        try:
            devices, channels = _connect_devices(mapping, session)
        except Exception:
            session.cleanup_connection_failure()
            raise
        main_field_state = snapshot_main_field_state(devices["gs200"])
        main_field_guard.add(
            "恢复 GS200 运行前状态",
            lambda: restore_main_field_state(devices["gs200"], main_field_state),
        )
        device_snapshot = _initial_state_snapshot(devices, channels)
        device_snapshot["main_field_before_configuration"] = asdict(
            main_field_state
        )
        run_dir.update_config(device_snapshot=device_snapshot)
        (
            actual_rate,
            actual_temperature,
            clock_sources,
            hf2_snapshot,
        ) = _configure_outputs(params, devices, channels, mapping)
        run_dir.update_config(
            actual_rates={"demod0_r_sa_s": actual_rate},
            initial_temperature_c=actual_temperature,
            temperature_control={
                "target_temperature_c": params.temperature_c,
                "actual_temperature_c": actual_temperature,
                "controlled_by_experiment": actual_temperature is not None,
                "control_source": (
                    "tec103" if actual_temperature is not None else "external_software"
                ),
            },
            clock_sources=clock_sources,
            hf2_configuration=hf2_snapshot,
        )
        data_files = _acquire_scan(
            params,
            run_dir,
            devices,
            channels,
            actual_rate,
            str(mapping["lockin_r"]["device_id"]),
        )
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=None,
            data_files=data_files,
        )
        print(f"Mx 主磁场控制噪声谱采集完成: {run_dir.root}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status="failed", failure_reason=failure_reason
            )
        raise
    finally:
        shutdown_report = safe_shutdown(
            devices, channels, params, main_field_guard
        )
        if shutdown_report.errors:
            print("安全关闭警告: " + "；".join(shutdown_report.errors))
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=shutdown_report.to_dict(),
                main_field_restored=not main_field_guard.restore_errors,
                device_disconnect={
                    "tec_disconnected": not shutdown_report.disconnect_errors,
                    "other_devices_preserved": True,
                    "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    params = load_runtime_params(MxMainFieldNoiseSpectrumParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
