"""Mx 高主场 Z 磁场频率标定采集工作流。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from gs200 import GS200Instrument
from lockin_amplifier import (
    DemodulatorConfig,
    HF2Instrument,
    OscillatorConfig,
    SignalInputConfig,
    demod,
)
from tec_controller import TECInstrument

from ...common import find_project_root, load_mapping, validate_safety_limit
from ...devices import create_signal_generator
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
    run_safety_shutdown,
    set_temperature_switch,
    synchronize_connected_clocks,
    wait_for_temperature_stable,
)
from ..mx_y_rf_sensitivity.acquisition import acquire_r, summarize_r
from .models import MxZFieldCalibrationParams
from .scan import (
    build_frequency_axis,
    build_z_axis,
    initial_rf_frequency_hz,
    predicted_center_hz,
)


EXPERIMENT_ID = "mx-z-field-calibration"
DATA_TYPE = "Mx_Z_Field_Calibration"
EXECUTION_MODE = "typed_workflow"


class _RuntimeCancellation:
    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


def _sleep(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _uncancellable_sleep(seconds: float) -> None:
    time.sleep(max(0.0, float(seconds)))


def _identity(device: Any) -> str | None:
    try:
        value = device.idn() if callable(getattr(device, "idn", None)) else device.idn
        return str(value)
    except Exception:
        return None


def _set_pump_gate_on(device: Any, channel: int, voltage_v: float) -> None:
    validate_safety_limit("Time_sequence", voltage_v)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_dc(voltage_v, channel=channel)
    device.set_output(True, channel=channel)


def _connect_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}

    gs_cfg = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect(
        "gs200", gs_cfg["resource"], lambda: GS200Instrument(gs_cfg["resource"])
    )

    z_cfg = mapping["Z_magnetic_field"]
    devices["z_field"] = session.connect(
        "z_field", z_cfg["resource"], lambda: create_signal_generator(z_cfg)
    )
    channels["z_field"] = int(z_cfg["channel"])

    x_cfg = mapping["X_magnetic_field"]
    rf_cfg = mapping["rf_coil"]
    if x_cfg["resource"] != rf_cfg["resource"]:
        raise ValueError("X_magnetic_field 与 rf_coil 必须位于同一台 XY 场信号源")
    devices["xy_field"] = session.connect(
        "xy_field", rf_cfg["resource"], lambda: create_signal_generator(rf_cfg)
    )
    channels["x_field"] = int(x_cfg["channel"])
    channels["y_rf"] = int(rf_cfg["channel"])

    pump_cfg = mapping["Pump_laser_power"]
    probe_cfg = mapping["Probe_laser_power"]
    if pump_cfg["resource"] != probe_cfg["resource"]:
        raise ValueError("Pump_laser_power 与 Probe_laser_power 必须位于同一设备")
    devices["laser"] = session.connect(
        "laser", pump_cfg["resource"], lambda: create_signal_generator(pump_cfg)
    )
    channels["pump_laser"] = int(pump_cfg["channel"])
    channels["probe_laser"] = int(probe_cfg["channel"])

    carrier_cfg = mapping["Pump_modulation"]
    gate_cfg = mapping["Time_sequence"]
    if carrier_cfg["resource"] != gate_cfg["resource"]:
        raise ValueError("Pump_modulation 与 Time_sequence 必须位于同一设备")
    devices["pump_rf"] = session.connect(
        "pump_rf", carrier_cfg["resource"], lambda: create_signal_generator(carrier_cfg)
    )
    channels["pump_carrier"] = int(carrier_cfg["channel"])
    channels["pump_gate"] = int(gate_cfg["channel"])

    temp_cfg = mapping["Temp_Switch"]
    devices["temp_switch"] = session.connect(
        "temp_switch", temp_cfg["resource"], lambda: create_signal_generator(temp_cfg)
    )
    channels["temp_switch"] = int(temp_cfg["channel"])

    hf_cfg = mapping["lockin_r"]
    devices["hf2"] = session.connect(
        "hf2",
        f"hf2://{hf_cfg.get('host', '127.0.0.1')}/{hf_cfg['device_id']}",
        lambda: HF2Instrument(
            host=hf_cfg.get("host", "127.0.0.1"),
            port=int(hf_cfg.get("port", 8005)),
            api_level=1,
            device_id=hf_cfg["device_id"],
        ),
    )

    tec_cfg = mapping["temperature"]
    devices["tec"] = session.connect_optional(
        "tec",
        tec_cfg["resource"],
        lambda: TECInstrument(port=tec_cfg["resource"]),
        device_label="TEC103",
    )
    return devices, channels


def _initial_state_snapshot(
    devices: dict[str, Any], channels: dict[str, int]
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "identity_readback": {
            name: identity
            for name, device in devices.items()
            if (identity := _identity(device)) is not None
        }
    }
    outputs: dict[str, Any] = {}
    for semantic, channel_name in (
        ("z_field", "z_field"),
        ("xy_field", "x_field"),
        ("xy_field", "y_rf"),
        ("laser", "pump_laser"),
        ("laser", "probe_laser"),
        ("pump_rf", "pump_carrier"),
        ("pump_rf", "pump_gate"),
        ("temp_switch", "temp_switch"),
    ):
        device = devices.get(semantic)
        channel = channels.get(channel_name)
        if device is None or channel is None:
            continue
        try:
            outputs[channel_name] = {"output_on": bool(device.get_output(channel=channel))}
        except Exception as exc:
            outputs[channel_name] = {"read_error": str(exc)}
    snapshot["output_state_before_configuration"] = outputs
    return snapshot


def _configure_reference_clocks(
    devices: dict[str, Any], mapping: dict[str, dict[str, Any]]
) -> dict[str, dict[str, str]]:
    records = synchronize_connected_clocks(
        devices,
        mapping,
        {
            "z_field": "Z_magnetic_field",
            "xy_field": "rf_coil",
            "laser": "Pump_laser_power",
            "pump_rf": "Pump_modulation",
            "temp_switch": "Temp_Switch",
            "hf2": "lockin_r",
        },
    )
    return {
        name: {"target": str(record["target"]), "actual": str(record["actual"])}
        for name, record in records.items()
    }


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    params: MxZFieldCalibrationParams,
) -> SafetyShutdownReport:
    dg_channels: list[DGChannelShutdown] = []
    if devices.get("z_field") is not None and "z_field" in channels:
        dg_channels.append(
            DGChannelShutdown(
                devices["z_field"], channels["z_field"], "Z_magnetic_field", "Z DC 偏置"
            )
        )
    if devices.get("xy_field") is not None:
        if "x_field" in channels:
            dg_channels.append(
                DGChannelShutdown(
                    devices["xy_field"], channels["x_field"], "X_magnetic_field", "X 磁场"
                )
            )
        if "y_rf" in channels:
            dg_channels.append(
                DGChannelShutdown(
                    devices["xy_field"], channels["y_rf"], "rf_coil", "Y RF 场"
                )
            )

    extra_actions: list[ShutdownAction] = []
    pump_rf = devices.get("pump_rf")
    if pump_rf is not None and "pump_gate" in channels:
        extra_actions.append(
            ShutdownAction(
                "保持 Pump RF 开关 5V 常开失败",
                lambda: _set_pump_gate_on(
                    pump_rf, channels["pump_gate"], params.pump_gate_voltage_v
                ),
            )
        )
    if pump_rf is not None and "pump_carrier" in channels:
        def keep_pump_carrier_on() -> None:
            validate_safety_limit("Pump_modulation", params.pump_carrier_amplitude_vpp)
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
    return run_safety_shutdown(
        dg_channels=dg_channels,
        temperature_switch=temperature_restore,
        extra_actions=extra_actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )


def _configure_outputs(
    params: MxZFieldCalibrationParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    mapping: dict[str, dict[str, Any]],
) -> tuple[float, float | None, dict[str, dict[str, str]]]:
    clock_sources = _configure_reference_clocks(devices, mapping)
    initial_frequency_hz = initial_rf_frequency_hz(params)

    z_field = devices["z_field"]
    validate_safety_limit("Z_magnetic_field", 0.0)
    z_field.set_burst_state(False, channel=channels["z_field"])
    z_field.set_mod_state(False, channel=channels["z_field"])
    z_field.setup_dc(0.0, channel=channels["z_field"])
    z_field.set_output(False, channel=channels["z_field"])

    xy_field = devices["xy_field"]
    validate_safety_limit("X_magnetic_field", 0.0)
    xy_field.set_burst_state(False, channel=channels["x_field"])
    xy_field.set_mod_state(False, channel=channels["x_field"])
    xy_field.setup_dc(0.0, channel=channels["x_field"])
    xy_field.set_output(False, channel=channels["x_field"])
    validate_safety_limit("rf_coil", params.y_rf_amplitude_vpp)
    xy_field.set_burst_state(False, channel=channels["y_rf"])
    xy_field.set_mod_state(False, channel=channels["y_rf"])
    xy_field.setup_sine(
        initial_frequency_hz,
        params.y_rf_amplitude_vpp,
        offset=0.0,
        phase=0.0,
        channel=channels["y_rf"],
    )
    xy_field.set_output(False, channel=channels["y_rf"])

    laser = devices["laser"]
    validate_safety_limit("Pump_laser_power", params.pump_laser_power_v)
    laser.setup_dc(params.pump_laser_power_v, channel=channels["pump_laser"])
    laser.set_output(True, channel=channels["pump_laser"])
    validate_safety_limit("Probe_laser_power", params.probe_laser_power_v)
    laser.setup_dc(params.probe_laser_power_v, channel=channels["probe_laser"])
    laser.set_output(True, channel=channels["probe_laser"])

    gs200 = devices["gs200"]
    gs_cfg = mapping["main_magnetic_field"]
    if gs_cfg.get("source_function"):
        gs200.set_source_function(gs_cfg["source_function"])
    gs200.set_current_limit(0.01)
    validate_safety_limit("main_magnetic_field", params.main_magnetic_field_ma)
    gs200.set_current(params.main_magnetic_field_ma / 1000.0)
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
    _set_pump_gate_on(pump_rf, channels["pump_gate"], params.pump_gate_voltage_v)

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
            frequency=initial_frequency_hz,
        ),
    )
    actual_rate = demod.configure_demodulator(
        hf2,
        DemodulatorConfig(
            demod_index=params.demod_idx,
            enable=True,
            rate=params.response_rate_sa_s,
            input_channel=0,
            osc_select=params.demod_osc_idx,
            harmonic=1,
            time_constant=params.response_time_constant_s,
            order=params.response_demod_order,
            phase=float(
                hf2.get_double(f"{hf2.demod_path(params.demod_idx)}/phaseshift")
            ),
        ),
    )
    temperature_text = (
        f"{actual_temperature:.2f} °C"
        if actual_temperature is not None
        else "由外部软件控制（未读取）"
    )
    print(
        f"Mx Z 标定工作点已配置，温度 {temperature_text}，"
        f"Demod0 实际速率 {float(actual_rate):.3f} Sa/s"
    )
    return float(actual_rate), actual_temperature, clock_sources


def _temperature_gated_acquire(
    params: MxZFieldCalibrationParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    acquire: Callable[[], dict[str, np.ndarray]],
) -> dict[str, np.ndarray]:
    """每次频点采集均关闭温控，完成后恢复并固定等待。"""
    check_cancelled()
    set_temperature_switch(
        devices["temp_switch"], False, channel=channels["temp_switch"]
    )
    try:
        _sleep(params.temp_switch_off_lead_s)
        _sleep(params.frequency_settle_time_s)
        return acquire()
    finally:
        set_temperature_switch(
            devices["temp_switch"], True, channel=channels["temp_switch"]
        )
        _uncancellable_sleep(params.temp_switch_on_lag_s)


def _save_point(
    path: Path,
    payload: dict[str, np.ndarray],
    metadata: dict[str, Any],
    actual_rate: float,
) -> dict[str, float]:
    summary = summarize_r(payload)
    np.savez(
        path,
        time_s=payload["time_s"],
        r_v=payload["r"],
        actual_rate_sa_s=np.float64(actual_rate),
        **metadata,
        **{
            key: np.float64(value) if isinstance(value, float) else value
            for key, value in summary.items()
        },
    )
    return summary


def _acquire_valid_r_point(
    params: MxZFieldCalibrationParams,
    point_dir: Path,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    file_stem: str,
    metadata: dict[str, Any],
    actual_rate: float,
    device_id: str,
) -> tuple[dict[str, float], int, str]:
    for attempt in range(params.r_point_max_attempts):
        payload = _temperature_gated_acquire(
            params,
            devices,
            channels,
            acquire=lambda: acquire_r(
                devices["hf2"],
                device_id=device_id,
                demod_idx=params.demod_idx,
                actual_rate_sa_s=actual_rate,
                duration_s=params.frequency_duration_s,
            ),
        )
        summary = summarize_r(payload)
        accepted = summary["r_std_v"] <= params.r_bad_point_std_threshold_v
        filename = f"{file_stem}_attempt_{attempt:02d}.npz"
        _save_point(
            point_dir / filename,
            payload,
            {
                **metadata,
                "attempt_index": np.int64(attempt),
                "quality_accepted": np.uint8(accepted),
                "r_std_threshold_v": np.float64(
                    params.r_bad_point_std_threshold_v
                ),
                "temp_switch_off_voltage_v": np.float64(0.0),
                "temp_switch_on_voltage_v": np.float64(5.0),
                "temp_switch_off_lead_s": np.float64(
                    params.temp_switch_off_lead_s
                ),
                "temp_switch_on_lag_s": np.float64(
                    params.temp_switch_on_lag_s
                ),
            },
            actual_rate,
        )
        print(
            f"  R 质量 {attempt + 1}/{params.r_point_max_attempts}: "
            f"std={summary['r_std_v']:.6g} V，"
            f"{'接受' if accepted else '拒绝并重采'}"
        )
        if accepted:
            return summary, attempt, filename
    raise RuntimeError(
        f"{file_stem} 连续 {params.r_point_max_attempts} 次 "
        f"std(R) > {params.r_bad_point_std_threshold_v:.6g} V"
    )


def _acquire_scan(
    params: MxZFieldCalibrationParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
) -> list[str]:
    z_axis = build_z_axis(params)
    z_field = devices["z_field"]
    rf = devices["xy_field"]
    hf2 = devices["hf2"]
    summary_files: list[str] = []
    predicted_centers: list[float] = []

    for z_index, z_bias in enumerate(z_axis):
        check_cancelled()
        predicted = predicted_center_hz(params, float(z_bias))
        frequency_axis = build_frequency_axis(params, float(z_bias))
        predicted_centers.append(predicted)
        print(
            f"Z 偏置 {z_index + 1}/{len(z_axis)}: {z_bias:+.3f} V，"
            f"预测中心 {predicted:.3f} Hz"
        )
        validate_safety_limit("Z_magnetic_field", float(z_bias))
        z_field.setup_dc(float(z_bias), channel=channels["z_field"])
        z_field.set_output(True, channel=channels["z_field"])

        point_dir = run_dir.raw / f"z_{z_index:03d}"
        point_dir.mkdir()
        summaries: list[dict[str, float]] = []
        accepted_attempts: list[int] = []
        accepted_files: list[str] = []
        for frequency_index, frequency in enumerate(frequency_axis):
            check_cancelled()
            print(
                f"  频率点 {frequency_index + 1}/{len(frequency_axis)}: "
                f"{frequency:.3f} Hz"
            )
            validate_safety_limit("rf_coil", params.y_rf_amplitude_vpp)
            rf.set_frequency(float(frequency), channel=channels["y_rf"])
            rf.set_phase_adjust(0.0, channel=channels["y_rf"])
            rf.set_amplitude(params.y_rf_amplitude_vpp, channel=channels["y_rf"])
            rf.set_output(True, channel=channels["y_rf"])
            demod.configure_oscillator(
                hf2,
                OscillatorConfig(
                    osc_index=params.demod_osc_idx, frequency=float(frequency)
                ),
            )
            summary, attempt, filename = _acquire_valid_r_point(
                params,
                point_dir,
                devices,
                channels,
                file_stem=f"frequency_{frequency_index:04d}",
                metadata={
                    "z_index": np.int64(z_index),
                    "z_bias_v": np.float64(z_bias),
                    "predicted_center_hz": np.float64(predicted),
                    "frequency_index": np.int64(frequency_index),
                    "frequency_hz": np.float64(frequency),
                    "rf_amplitude_vpp": np.float64(params.y_rf_amplitude_vpp),
                },
                actual_rate=actual_rate,
                device_id=device_id,
            )
            summaries.append(summary)
            accepted_attempts.append(attempt)
            accepted_files.append(filename)
        rf.set_output(False, channel=channels["y_rf"])

        summary_path = point_dir / "frequency_scan.npz"
        np.savez(
            summary_path,
            z_index=np.int64(z_index),
            z_bias_v=np.float64(z_bias),
            predicted_center_hz=np.float64(predicted),
            frequency_hz=frequency_axis,
            r_mean_v=[item["r_mean_v"] for item in summaries],
            r_scalar_mean_v=[item["r_scalar_mean_v"] for item in summaries],
            r_std_v=[item["r_std_v"] for item in summaries],
            accepted_attempt_index=accepted_attempts,
            accepted_file=np.asarray(accepted_files, dtype=str),
            actual_rate_sa_s=np.float64(actual_rate),
        )
        summary_files.append(str(summary_path.relative_to(run_dir.root)).replace("\\", "/"))

    rf.set_output(False, channel=channels["y_rf"])
    np.savez(
        run_dir.raw / "z_scan_index.npz",
        z_bias_v=z_axis,
        predicted_center_hz=np.asarray(predicted_centers, dtype=float),
        summary_file=np.asarray(summary_files, dtype=str),
    )
    return ["raw/z_scan_index.npz", *summary_files]


def run(params: MxZFieldCalibrationParams) -> Path:
    root = find_project_root()
    mapping = load_mapping(root)
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
            "main_field": "Z",
            "pump": "Z",
            "probe": "X",
            "rf_field": "Y",
            "z_dc_scan": "single_pass_negative_to_positive",
        },
        analysis_during_acquisition=False,
        acquisition_signals=["R"],
        r_point_quality={
            "criterion": "std(R) <= threshold",
            "std_threshold_v": params.r_bad_point_std_threshold_v,
            "max_attempts": params.r_point_max_attempts,
        },
        temperature_gating={
            "scope": "every_frequency_point",
            "off_voltage_v": 0.0,
            "on_voltage_v": 5.0,
            "off_lead_s": params.temp_switch_off_lead_s,
            "on_lag_s": params.temp_switch_on_lag_s,
        },
    )
    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    completion_status = "failed"
    failure_reason: str | None = None
    try:
        check_cancelled()
        try:
            devices, channels = _connect_devices(mapping, session)
        except Exception:
            session.cleanup_connection_failure()
            raise
        run_dir.update_config(device_snapshot=_initial_state_snapshot(devices, channels))
        actual_rate, actual_temperature, clock_sources = _configure_outputs(
            params, devices, channels, mapping
        )
        run_dir.update_config(
            actual_rates={"response_sa_s": actual_rate},
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
        print(f"Mx 高主场 Z 标定采集完成: {run_dir.root}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status="failed", failure_reason=failure_reason
            )
        raise
    finally:
        shutdown_report = safe_shutdown(devices, channels, params)
        if shutdown_report.errors:
            print("安全关闭警告: " + "；".join(shutdown_report.errors))
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=shutdown_report.to_dict(),
                device_disconnect={
                    "tec_disconnected": not shutdown_report.disconnect_errors,
                    "other_devices_preserved": True,
                    "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    params = load_runtime_params(MxZFieldCalibrationParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

