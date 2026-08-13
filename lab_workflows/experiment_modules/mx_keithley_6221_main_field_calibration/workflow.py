"""Mx Keithley 6221 主磁场频率标定采集工作流。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
from gs200 import GS200Instrument
from keithley_6221 import Keithley6221Instrument
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
    connect_signal_generator_routes,
    create_run_directory,
    run_safety_shutdown,
    set_temperature_switch,
    synchronize_connected_clocks,
    wait_for_temperature_stable,
)
from ..mx_z_field_calibration.workflow import (
    _acquire_valid_r_point,
    _set_pump_gate_on,
)
from .models import MxKeithley6221MainFieldCalibrationParams
from .scan import build_current_axis, build_frequency_axis, predicted_center_hz


EXPERIMENT_ID = "mx-keithley-6221-main-field-calibration"
DATA_TYPE = "Mx_Keithley_6221_Main_Field_Calibration"
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


def _identity(device: Any) -> str | None:
    try:
        value = device.idn() if callable(getattr(device, "idn", None)) else device.idn
        return str(value)
    except Exception:
        return None


def _connect_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
    devices: dict[str, Any],
    channels: dict[str, int],
) -> None:
    gs_cfg = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect(
        "gs200", gs_cfg["resource"], lambda: GS200Instrument(gs_cfg["resource"])
    )

    keithley_cfg = mapping["keithley_6221_main_field"]
    devices["keithley"] = session.connect(
        "keithley",
        keithley_cfg["resource"],
        lambda: Keithley6221Instrument(keithley_cfg["resource"]),
    )

    z_cfg = mapping["Z_magnetic_field"]
    devices["z_field"] = session.connect(
        "z_field", z_cfg["resource"], lambda: create_signal_generator(z_cfg)
    )
    channels["z_field"] = int(z_cfg["channel"])

    x_cfg = mapping["X_magnetic_field"]
    rf_cfg = mapping["rf_coil"]
    devices["xy_field"], routed = connect_signal_generator_routes(
        session,
        "xy_field",
        {
            "x_field": ("X_magnetic_field", x_cfg),
            "y_rf": ("rf_coil", rf_cfg),
        },
    )
    channels.update(routed)

    pump_cfg = mapping["Pump_laser_power"]
    probe_cfg = mapping["Probe_laser_power"]
    devices["laser"], routed = connect_signal_generator_routes(
        session,
        "laser",
        {
            "pump_laser": ("Pump_laser_power", pump_cfg),
            "probe_laser": ("Probe_laser_power", probe_cfg),
        },
    )
    channels.update(routed)

    carrier_cfg = mapping["Pump_modulation"]
    gate_cfg = mapping["Time_sequence"]
    devices["pump_rf"], routed = connect_signal_generator_routes(
        session,
        "pump_rf",
        {
            "pump_carrier": ("Pump_modulation", carrier_cfg),
            "pump_gate": ("Time_sequence", gate_cfg),
        },
    )
    channels.update(routed)

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


def _shutdown_6221(source: Any) -> None:
    validate_safety_limit("keithley_6221_main_field", 0.0)
    errors: list[str] = []

    def verify() -> None:
        current_a = float(source.get_current())
        output_on = bool(source.get_output())
        if abs(current_a) > 1e-12 or output_on:
            raise RuntimeError(
                f"回读为 {current_a:.9g} A、输出 {'ON' if output_on else 'OFF'}"
            )

    for label, action in (
        ("ABORT", source.abort_waveform),
        ("关闭输出", lambda: source.set_output(False)),
        ("归零", lambda: source.set_current(0.0)),
        ("回读确认", verify),
    ):
        try:
            action()
        except Exception as exc:
            errors.append(f"{label}: {exc}")
    if errors:
        raise RuntimeError("Keithley 6221 安全关断失败: " + "；".join(errors))


def _shutdown_gs200(gs200: Any) -> None:
    validate_safety_limit("main_magnetic_field", 0.0)
    errors: list[str] = []

    def verify() -> None:
        current_a = float(gs200.get_current())
        output_on = bool(gs200.get_output())
        if abs(current_a) > 1e-12 or output_on:
            raise RuntimeError(
                f"回读为 {current_a:.9g} A、输出 {'ON' if output_on else 'OFF'}"
            )

    for label, action in (
        ("关闭输出", lambda: gs200.set_output(False)),
        ("归零", lambda: gs200.set_current(0.0)),
        ("回读确认", verify),
    ):
        try:
            action()
        except Exception as exc:
            errors.append(f"{label}: {exc}")
    if errors:
        raise RuntimeError("GS200 安全关断失败: " + "；".join(errors))


def _assert_not_in_compliance(source: Any, context: str) -> None:
    if source.is_in_compliance():
        try:
            _shutdown_6221(source)
        except Exception as shutdown_exc:
            raise RuntimeError(
                f"Keithley 6221 在{context}进入 Compliance；关断同时失败: {shutdown_exc}"
            ) from shutdown_exc
        raise RuntimeError(f"Keithley 6221 在{context}进入 Compliance，实验已安全终止")


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    params: MxKeithley6221MainFieldCalibrationParams,
) -> SafetyShutdownReport:
    current_source_errors: list[str] = []
    for label, device, action in (
        ("Keithley 6221 归零关闭失败", devices.get("keithley"), _shutdown_6221),
        ("GS200 归零关闭失败", devices.get("gs200"), _shutdown_gs200),
    ):
        if device is None:
            continue
        try:
            action(device)
        except Exception as exc:
            current_source_errors.append(f"{label}: {exc}")

    dg_channels: list[DGChannelShutdown] = []
    if devices.get("z_field") is not None and "z_field" in channels:
        dg_channels.append(DGChannelShutdown(devices["z_field"], channels["z_field"], "Z_magnetic_field", "Z 辅助场"))
    if devices.get("xy_field") is not None:
        if "x_field" in channels:
            dg_channels.append(DGChannelShutdown(devices["xy_field"], channels["x_field"], "X_magnetic_field", "X 磁场"))
        if "y_rf" in channels:
            dg_channels.append(DGChannelShutdown(devices["xy_field"], channels["y_rf"], "rf_coil", "Y RF 场"))

    extra_actions: list[ShutdownAction] = []
    pump_rf = devices.get("pump_rf")
    if pump_rf is not None and "pump_gate" in channels:
        extra_actions.append(ShutdownAction("保持 Pump RF 开关 5V 常开失败", lambda: _set_pump_gate_on(pump_rf, channels["pump_gate"], params.pump_gate_voltage_v)))
    if pump_rf is not None and "pump_carrier" in channels:
        def keep_pump_carrier_on() -> None:
            validate_safety_limit("Pump_modulation", params.pump_carrier_amplitude_vpp)
            pump_rf.setup_sine(params.pump_carrier_frequency_hz, params.pump_carrier_amplitude_vpp, offset=0.0, phase=0.0, channel=channels["pump_carrier"])
            pump_rf.set_output(True, channel=channels["pump_carrier"])

        extra_actions.append(ShutdownAction("保持 Pump 100MHz 载波开启失败", keep_pump_carrier_on))

    temperature_restore = None
    if devices.get("temp_switch") is not None and "temp_switch" in channels:
        temperature_restore = TemperatureSwitchRestore(devices["temp_switch"], channels["temp_switch"])
    preserved = tuple(
        key for key in STANDARD_PRESERVED_OUTPUTS if key != "main_magnetic_field"
    )
    report = run_safety_shutdown(
        dg_channels=dg_channels,
        temperature_switch=temperature_restore,
        extra_actions=extra_actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=(*preserved, "Time_sequence"),
    )
    return SafetyShutdownReport(
        action_errors=(*current_source_errors, *report.action_errors),
        disconnect_errors=report.disconnect_errors,
        preserved_outputs=report.preserved_outputs,
    )


def _configure_outputs(
    params: MxKeithley6221MainFieldCalibrationParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    mapping: dict[str, dict[str, Any]],
) -> tuple[float, float | None, dict[str, dict[str, str]]]:
    gs200 = devices["gs200"]
    gs_cfg = mapping["main_magnetic_field"]
    gs200.set_output(False)
    if gs_cfg.get("source_function"):
        gs200.set_source_function(gs_cfg["source_function"])
    validate_safety_limit("main_magnetic_field", params.main_magnetic_field_ma)
    gs200.set_current(0.0)
    if abs(float(gs200.get_current())) > 1e-12 or bool(gs200.get_output()):
        raise RuntimeError("GS200 未能保持 0 mA 且输出关闭")

    source = devices["keithley"]
    validate_safety_limit("keithley_6221_main_field", 0.0)
    source.abort_waveform()
    source.set_output(False)
    source.set_current(0.0)
    source.set_autorange(False)
    source.set_current_range(params.keithley_current_range_ma / 1000.0)
    source.set_output_response("SLOW")
    source.set_analog_filter(False)
    source.set_compliance(params.keithley_compliance_v)
    source.set_compliance_test(True)
    source.raise_for_errors()
    if bool(source.get_output()) or abs(float(source.get_current())) > 1e-12:
        raise RuntimeError("Keithley 6221 初始化后未保持 0 mA 且输出关闭")

    clock_sources = _configure_reference_clocks(devices, mapping)
    initial_frequency_hz = predicted_center_hz(
        params, float(build_current_axis(params)[0])
    )

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
    xy_field.setup_sine(initial_frequency_hz, params.y_rf_amplitude_vpp, offset=0.0, phase=0.0, channel=channels["y_rf"])
    xy_field.set_output(False, channel=channels["y_rf"])

    laser = devices["laser"]
    validate_safety_limit("Pump_laser_power", params.pump_laser_power_v)
    laser.setup_dc(params.pump_laser_power_v, channel=channels["pump_laser"])
    laser.set_output(True, channel=channels["pump_laser"])
    validate_safety_limit("Probe_laser_power", params.probe_laser_power_v)
    laser.setup_dc(params.probe_laser_power_v, channel=channels["probe_laser"])
    laser.set_output(True, channel=channels["probe_laser"])

    pump_rf = devices["pump_rf"]
    validate_safety_limit("Pump_modulation", params.pump_carrier_amplitude_vpp)
    pump_rf.setup_sine(params.pump_carrier_frequency_hz, params.pump_carrier_amplitude_vpp, offset=0.0, phase=0.0, channel=channels["pump_carrier"])
    pump_rf.set_output(True, channel=channels["pump_carrier"])
    _set_pump_gate_on(pump_rf, channels["pump_gate"], params.pump_gate_voltage_v)

    set_temperature_switch(devices["temp_switch"], True, channel=channels["temp_switch"])
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
    demod.configure_signal_input(hf2, SignalInputConfig(input_index=0, range=params.hf2_signal_range_v, ac_coupling=True, diff=False, impedance=50))
    demod.configure_oscillator(hf2, OscillatorConfig(osc_index=params.demod_osc_idx, frequency=initial_frequency_hz))
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
            phase=float(hf2.get_double(f"{hf2.demod_path(params.demod_idx)}/phaseshift")),
        ),
    )
    return float(actual_rate), actual_temperature, clock_sources


def _acquire_scan(
    params: MxKeithley6221MainFieldCalibrationParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
) -> list[str]:
    current_axis = build_current_axis(params)
    source = devices["keithley"]
    rf = devices["xy_field"]
    hf2 = devices["hf2"]
    summary_files: list[str] = []
    predicted_centers: list[float] = []
    try:
        for current_index, current_ma in enumerate(current_axis):
            check_cancelled()
            predicted = predicted_center_hz(params, float(current_ma))
            frequency_axis = build_frequency_axis(params, float(current_ma))
            predicted_centers.append(predicted)
            print(f"6221 主场电流 {current_index + 1}/{len(current_axis)}: {current_ma:.3f} mA，预测中心 {predicted:.3f} Hz")
            validate_safety_limit("keithley_6221_main_field", float(current_ma))
            source.set_current(float(current_ma) / 1000.0)
            source.set_output(True)
            _sleep(params.keithley_current_settle_time_s)
            _assert_not_in_compliance(source, f"{current_ma:.3f} mA 稳定后")

            point_dir = run_dir.raw / f"current_{current_index:03d}"
            point_dir.mkdir()
            summaries: list[dict[str, float]] = []
            accepted_attempts: list[int] = []
            accepted_files: list[str] = []
            for frequency_index, frequency in enumerate(frequency_axis):
                check_cancelled()
                _assert_not_in_compliance(source, f"{current_ma:.3f} mA、{frequency:.3f} Hz 采集前")
                print(f"  频率点 {frequency_index + 1}/{len(frequency_axis)}: {frequency:.3f} Hz")
                validate_safety_limit("rf_coil", params.y_rf_amplitude_vpp)
                rf.set_frequency(float(frequency), channel=channels["y_rf"])
                rf.set_phase_adjust(0.0, channel=channels["y_rf"])
                rf.set_amplitude(params.y_rf_amplitude_vpp, channel=channels["y_rf"])
                rf.set_output(True, channel=channels["y_rf"])
                demod.configure_oscillator(hf2, OscillatorConfig(osc_index=params.demod_osc_idx, frequency=float(frequency)))
                summary, attempt, filename = _acquire_valid_r_point(
                    params,
                    point_dir,
                    devices,
                    channels,
                    file_stem=f"frequency_{frequency_index:04d}",
                    metadata={
                        "current_index": np.int64(current_index),
                        "keithley_current_ma": np.float64(current_ma),
                        "predicted_center_hz": np.float64(predicted),
                        "frequency_index": np.int64(frequency_index),
                        "frequency_hz": np.float64(frequency),
                        "rf_amplitude_vpp": np.float64(params.y_rf_amplitude_vpp),
                        "keithley_current_range_ma": np.float64(
                            params.keithley_current_range_ma
                        ),
                        "compliance_v": np.float64(params.keithley_compliance_v),
                    },
                    actual_rate=actual_rate,
                    device_id=device_id,
                )
                _assert_not_in_compliance(source, f"{current_ma:.3f} mA、{frequency:.3f} Hz 采集后")
                summaries.append(summary)
                accepted_attempts.append(attempt)
                accepted_files.append(filename)
            rf.set_output(False, channel=channels["y_rf"])

            summary_path = point_dir / "frequency_scan.npz"
            np.savez(
                summary_path,
                current_index=np.int64(current_index),
                keithley_current_ma=np.float64(current_ma),
                predicted_center_hz=np.float64(predicted),
                frequency_hz=frequency_axis,
                r_mean_v=[item["r_mean_v"] for item in summaries],
                r_scalar_mean_v=[item["r_scalar_mean_v"] for item in summaries],
                r_std_v=[item["r_std_v"] for item in summaries],
                accepted_attempt_index=accepted_attempts,
                accepted_file=np.asarray(accepted_files, dtype=str),
                actual_rate_sa_s=np.float64(actual_rate),
                keithley_current_range_ma=np.float64(
                    params.keithley_current_range_ma
                ),
                compliance_v=np.float64(params.keithley_compliance_v),
            )
            summary_files.append(str(summary_path.relative_to(run_dir.root)).replace("\\", "/"))
    finally:
        if devices.get("xy_field") is not None and "y_rf" in channels:
            try:
                rf.set_output(False, channel=channels["y_rf"])
            except Exception:
                pass

    np.savez(
        run_dir.raw / "keithley_main_field_scan_index.npz",
        keithley_current_ma=current_axis,
        predicted_center_hz=np.asarray(predicted_centers, dtype=float),
        keithley_current_range_ma=np.float64(params.keithley_current_range_ma),
        summary_file=np.asarray(summary_files, dtype=str),
    )
    return ["raw/keithley_main_field_scan_index.npz", *summary_files]


def run(params: MxKeithley6221MainFieldCalibrationParams) -> Path:
    root = find_project_root()
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    run_dir = create_run_directory(DATA_TYPE, params.run_tag, params.to_external(), schema_version=params.schema_version, project_root=root)
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        geometry={
            "main_field": "Z controlled exclusively by Keithley 6221",
            "gs200": "physically disconnected, 0 mA, output off",
            "pump": "Z",
            "probe": "X",
            "rf_field": "Y",
            "main_field_scan": "single_pass_increasing_current",
            "z_auxiliary_field": "0 V, output off",
        },
        source_configuration={
            "range_a": 0.02,
            "autorange": False,
            "response": "SLOW",
            "analog_filter": False,
            "compliance_v": params.keithley_compliance_v,
        },
        analysis_during_acquisition=False,
        acquisition_signals=["R"],
        gyromagnetic_ratio_hz_per_nt=params.gyromagnetic_ratio_hz_per_nt,
    )
    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    completion_status = "failed"
    failure_reason: str | None = None
    connection_complete = False
    try:
        check_cancelled()
        _connect_devices(mapping, session, devices, channels)
        connection_complete = True
        run_dir.update_config(
            device_snapshot={
                "identity_readback": {
                    name: identity
                    for name, device in devices.items()
                    if (identity := _identity(device)) is not None
                },
                "gs200_physically_disconnected_confirmed": params.confirm_gs200_disconnected,
            }
        )
        actual_rate, actual_temperature, clock_sources = _configure_outputs(params, devices, channels, mapping)
        run_dir.update_config(
            actual_rates={"response_sa_s": actual_rate},
            initial_temperature_c=actual_temperature,
            clock_sources=clock_sources,
        )
        data_files = _acquire_scan(params, run_dir, devices, channels, actual_rate, str(mapping["lockin_r"]["device_id"]))
        completion_status = "completed"
        run_dir.update_config(completion_status=completion_status, failure_reason=None, data_files=data_files)
        print(f"Mx Keithley 6221 主场标定采集完成: {run_dir.root}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(completion_status="failed", failure_reason=failure_reason)
        raise
    finally:
        shutdown_report = safe_shutdown(devices, channels, params)
        if not connection_complete:
            session.cleanup_connection_failure()
        if shutdown_report.errors:
            print("安全关闭警告: " + "；".join(shutdown_report.errors))
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=shutdown_report.to_dict(),
                current_sources_shutdown_target="0_mA_output_off",
                device_disconnect={
                    "tec_disconnected": not shutdown_report.disconnect_errors,
                    "other_devices_preserved": True,
                    "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    params = load_runtime_params(MxKeithley6221MainFieldCalibrationParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
