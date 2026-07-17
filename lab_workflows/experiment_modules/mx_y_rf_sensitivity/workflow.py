"""Mx 构型 Y 向 RF 场灵敏度采集工作流。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import yaml
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
    create_run_directory,
    run_safety_shutdown,
    set_temperature_switch,
    synchronize_connected_clocks,
    wait_for_temperature_stable,
)
from .acquisition import acquire_r, summarize_r
from .analysis_core import fit_lorentzian_response
from .models import MxYRFParams
from .scan import build_amplitude_axis, build_frequency_axis, signed_amplitude_hardware


EXPERIMENT_ID = "mx-y-rf-sensitivity"
DATA_TYPE = "Mx_Y_RF_Sensitivity"
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


def _initial_state_snapshot(devices: dict[str, Any], channels: dict[str, int]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "identity_readback": {
            name: identity
            for name, device in devices.items()
            if (identity := _identity(device)) is not None
        }
    }
    outputs: dict[str, Any] = {}
    for semantic, channel_name in (
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


def _set_pump_gate_on(device: Any, channel: int, voltage_v: float) -> None:
    """保持通道既有阻抗，用通用 DC 接口保持 Pump 门控常开。"""
    validate_safety_limit("Time_sequence", voltage_v)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_dc(voltage_v, channel=channel)
    device.set_output(True, channel=channel)


def _configure_reference_clocks(
    devices: dict[str, Any],
    mapping: dict[str, dict[str, Any]],
    *,
    profile: dict[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """同步本工作流使用的全部信号源和 HF2，并强制回读验证。"""
    records = synchronize_connected_clocks(
        devices,
        mapping,
        {
            "xy_field": "rf_coil",
            "laser": "Pump_laser_power",
            "pump_rf": "Pump_modulation",
            "temp_switch": "Temp_Switch",
            "hf2": "lockin_r",
        },
        profile=profile,
    )
    return {
        name: {"target": str(record["target"]), "actual": str(record["actual"])}
        for name, record in records.items()
    }


def _connect_devices(mapping: dict[str, dict[str, Any]], session: DeviceSession) -> tuple[dict[str, Any], dict[str, int]]:
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}

    gs_cfg = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect(
        "gs200", gs_cfg["resource"], lambda: GS200Instrument(gs_cfg["resource"])
    )

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
    devices["tec"] = session.connect(
        "tec", tec_cfg["resource"], lambda: TECInstrument(port=tec_cfg["resource"])
    )
    return devices, channels


def safe_shutdown(
    devices: dict[str, Any], channels: dict[str, int], params: MxYRFParams
) -> SafetyShutdownReport:
    """关闭 XY 磁场并强制保持 Pump 连续常开。"""
    dg_channels: list[DGChannelShutdown] = []
    xy_field = devices.get("xy_field")
    if xy_field is not None:
        if "x_field" in channels:
            dg_channels.append(DGChannelShutdown(xy_field, channels["x_field"], "X_magnetic_field", "X 磁场"))
        if "y_rf" in channels:
            dg_channels.append(DGChannelShutdown(xy_field, channels["y_rf"], "rf_coil", "Y RF 场"))

    extra_actions: list[ShutdownAction] = []
    pump_rf = devices.get("pump_rf")
    if pump_rf is not None and "pump_gate" in channels:
        gate_channel = channels["pump_gate"]

        def keep_pump_gate_on() -> None:
            _set_pump_gate_on(
                pump_rf,
                gate_channel,
                params.pump_gate_voltage_v,
            )

        extra_actions.append(ShutdownAction("保持 Pump RF 开关 5V 常开失败", keep_pump_gate_on))
    if pump_rf is not None and "pump_carrier" in channels:
        carrier_channel = channels["pump_carrier"]

        def keep_pump_carrier_on() -> None:
            validate_safety_limit(
                "Pump_modulation",
                params.pump_carrier_amplitude_vpp,
            )
            pump_rf.setup_sine(
                params.pump_carrier_frequency_hz,
                params.pump_carrier_amplitude_vpp,
                offset=0.0,
                phase=0.0,
                channel=carrier_channel,
            )
            pump_rf.set_output(True, channel=carrier_channel)

        extra_actions.append(
            ShutdownAction(
                "保持 Pump 100MHz 载波开启失败",
                keep_pump_carrier_on,
            )
        )

    temperature_restore = None
    if devices.get("temp_switch") is not None and "temp_switch" in channels:
        temperature_restore = TemperatureSwitchRestore(devices["temp_switch"], channels["temp_switch"])
    return run_safety_shutdown(
        dg_channels=dg_channels,
        temperature_switch=temperature_restore,
        extra_actions=extra_actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )


def _configure_outputs(
    params: MxYRFParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    mapping: dict[str, dict[str, Any]],
) -> tuple[float, dict[str, dict[str, str]]]:
    clock_sources = _configure_reference_clocks(devices, mapping)
    xy_field = devices["xy_field"]
    validate_safety_limit("X_magnetic_field", 0.0)
    xy_field.set_burst_state(False, channel=channels["x_field"])
    xy_field.set_mod_state(False, channel=channels["x_field"])
    xy_field.setup_dc(0.0, channel=channels["x_field"])
    xy_field.set_output(False, channel=channels["x_field"])
    validate_safety_limit("rf_coil", 0.0)
    xy_field.set_burst_state(False, channel=channels["y_rf"])
    xy_field.set_mod_state(False, channel=channels["y_rf"])
    initial_amplitude = max(
        params.frequency_rf_amplitude_vpp,
        1e-6,
    )
    validate_safety_limit("rf_coil", initial_amplitude)
    xy_field.setup_sine(params.y_rf_frequency_hz, initial_amplitude, offset=0.0, phase=0.0, channel=channels["y_rf"])
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
    _set_pump_gate_on(
        pump_rf,
        channels["pump_gate"],
        params.pump_gate_voltage_v,
    )

    set_temperature_switch(devices["temp_switch"], True, channel=channels["temp_switch"])
    validate_safety_limit("temperature", params.temperature_c)
    tec = devices["tec"]
    tec.set_target_temperature(params.temperature_c, channel=1)
    tec.set_enable(True, channel=1)
    actual_temperature = wait_for_temperature_stable(
        tec,
        params.temperature_c,
        channel=1,
        tolerance_c=params.temperature_tolerance_c,
        stable_reads=params.temperature_stable_reads,
        poll_interval_s=params.temperature_poll_interval_s,
        timeout_s=params.temperature_timeout_s,
        cancellation=_RuntimeCancellation(),
    )

    hf2 = devices["hf2"]
    demod.configure_signal_input(
        hf2,
        SignalInputConfig(input_index=0, range=params.hf2_signal_range_v, ac_coupling=True, diff=False, impedance=50),
    )
    demod.configure_oscillator(hf2, OscillatorConfig(osc_index=params.demod_osc_idx, frequency=params.y_rf_frequency_hz))
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
    print(f"Mx 工作点已配置，温度 {actual_temperature:.2f} °C，Demod0 实际速率 {actual_rate:.3f} Sa/s")
    return float(actual_rate), clock_sources


def _set_signed_rf(device: Any, channel: int, signed_amplitude_vpp: float, *, output_zero: bool = False) -> dict[str, Any]:
    amplitude, phase, output_on = signed_amplitude_hardware(signed_amplitude_vpp)
    validate_safety_limit("rf_coil", amplitude)
    if output_on:
        device.set_phase_adjust(phase, channel=channel)
        device.set_amplitude(amplitude, channel=channel)
        device.set_output(True, channel=channel)
    else:
        device.set_output(bool(output_zero), channel=channel)
    return {"hardware_amplitude_vpp": amplitude, "hardware_phase_deg": phase, "output_on": bool(output_on or output_zero)}


def _save_point(path: Path, payload: dict[str, np.ndarray], metadata: dict[str, Any], actual_rate: float) -> dict[str, float]:
    summary = summarize_r(payload)
    np.savez(
        path,
        time_s=payload["time_s"],
        r_v=payload["r"],
        actual_rate_sa_s=np.float64(actual_rate),
        **{key: value for key, value in metadata.items()},
        **{key: np.float64(value) if isinstance(value, float) else value for key, value in summary.items()},
    )
    return summary


def _temperature_gated_acquire(
    params: MxYRFParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    settle_time_s: float,
    acquire: Callable[[], dict[str, np.ndarray]],
) -> dict[str, np.ndarray]:
    check_cancelled()
    set_temperature_switch(devices["temp_switch"], False, channel=channels["temp_switch"])
    try:
        _sleep(params.temp_switch_off_lead_s)
        _sleep(settle_time_s)
        return acquire()
    finally:
        set_temperature_switch(devices["temp_switch"], True, channel=channels["temp_switch"])
        _uncancellable_sleep(params.temp_switch_on_lag_s)


def _acquire_valid_r_point(
    params: MxYRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    file_stem: str,
    metadata: dict[str, Any],
    settle_time_s: float,
    duration_s: float,
    actual_rate: float,
    device_id: str,
) -> tuple[dict[str, float], int, str]:
    """采集一个 R 点；标准差超限时完整保存并重新采集。"""
    hf2 = devices["hf2"]
    for attempt in range(params.r_point_max_attempts):
        payload = _temperature_gated_acquire(
            params,
            devices,
            channels,
            settle_time_s=settle_time_s,
            acquire=lambda: acquire_r(
                hf2,
                device_id=device_id,
                demod_idx=params.demod_idx,
                actual_rate_sa_s=actual_rate,
                duration_s=duration_s,
            ),
        )
        summary = summarize_r(payload)
        accepted = (
            summary["r_std_v"]
            <= params.r_bad_point_std_threshold_v
        )
        filename = f"{file_stem}_attempt_{attempt:02d}.npz"
        _save_point(
            run_dir.raw / filename,
            payload,
            {
                **metadata,
                "attempt_index": np.int64(attempt),
                "quality_accepted": np.uint8(accepted),
                "r_std_threshold_v": np.float64(
                    params.r_bad_point_std_threshold_v
                ),
            },
            actual_rate,
        )
        print(
            f"  R 质量 {attempt + 1}/{params.r_point_max_attempts}: "
            f"std={summary['r_std_v']:.6g} V, "
            f"{'接受' if accepted else '拒绝并重采'}"
        )
        if accepted:
            return summary, attempt, filename
    raise RuntimeError(
        f"{file_stem} 连续 {params.r_point_max_attempts} 次 "
        f"std(R) > {params.r_bad_point_std_threshold_v:.6g} V"
    )


def _acquire_frequency_scan(
    params: MxYRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
) -> dict[str, Any]:
    axis = build_frequency_axis(params)
    summaries: list[dict[str, float]] = []
    accepted_attempts: list[int] = []
    accepted_files: list[str] = []
    rf = devices["xy_field"]
    hf2 = devices["hf2"]
    for index, frequency in enumerate(axis):
        check_cancelled()
        print(f"模式1频率点 {index + 1}/{len(axis)}: {frequency:.3f} Hz")
        validate_safety_limit("rf_coil", params.frequency_rf_amplitude_vpp)
        rf.set_frequency(float(frequency), channel=channels["y_rf"])
        rf.set_phase_adjust(0.0, channel=channels["y_rf"])
        rf.set_amplitude(params.frequency_rf_amplitude_vpp, channel=channels["y_rf"])
        rf.set_output(True, channel=channels["y_rf"])
        demod.configure_oscillator(hf2, OscillatorConfig(osc_index=params.demod_osc_idx, frequency=float(frequency)))
        summary, attempt, filename = _acquire_valid_r_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=f"frequency_{index:04d}",
            metadata={
                "frequency_hz": np.float64(frequency),
                "rf_amplitude_vpp": np.float64(
                    params.frequency_rf_amplitude_vpp
                ),
            },
            settle_time_s=params.frequency_settle_time_s,
            duration_s=params.frequency_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        summaries.append(summary)
        accepted_attempts.append(attempt)
        accepted_files.append(filename)
    rf.set_output(False, channel=channels["y_rf"])
    r_values = np.asarray([item["r_scalar_mean_v"] for item in summaries])
    np.savez(
        run_dir.raw / "frequency_scan.npz",
        frequency_hz=axis,
        r_mean_v=r_values,
        r_scalar_mean_v=r_values,
        r_std_v=[item["r_std_v"] for item in summaries],
        accepted_attempt_index=accepted_attempts,
        accepted_file=accepted_files,
        actual_rate_sa_s=np.float64(actual_rate),
    )
    fit = fit_lorentzian_response(
        axis,
        r_values,
        r_squared_min=params.fit_r_squared_min,
        relative_gamma_uncertainty_max=params.fit_relative_gamma_uncertainty_max,
    )
    with (run_dir.results / "frequency_gate.yaml").open("w", encoding="utf-8") as stream:
        yaml.safe_dump(fit.to_dict(), stream, allow_unicode=True, sort_keys=False)
    if not fit.success:
        raise RuntimeError("模式1 R-Lorentzian 拟合质量不合格: " + "；".join(fit.rejection_reasons))
    return fit.to_dict()


def _acquire_amplitude_scan(
    params: MxYRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
) -> None:
    axis = build_amplitude_axis(params)
    summaries: list[dict[str, float]] = []
    hardware_records: list[dict[str, Any]] = []
    accepted_attempts: list[int] = []
    accepted_files: list[str] = []
    rf = devices["xy_field"]
    hf2 = devices["hf2"]
    rf.set_frequency(params.y_rf_frequency_hz, channel=channels["y_rf"])
    demod.configure_oscillator(hf2, OscillatorConfig(osc_index=params.demod_osc_idx, frequency=params.y_rf_frequency_hz))
    for index, amplitude in enumerate(axis):
        check_cancelled()
        print(f"Y RF 幅度点 {index + 1}/{len(axis)}: {amplitude:+.6f} Vpp")
        hardware = _set_signed_rf(rf, channels["y_rf"], float(amplitude))
        summary, attempt, filename = _acquire_valid_r_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=f"amplitude_{index:04d}",
            metadata={
                "signed_amplitude_vpp": np.float64(amplitude),
                **hardware,
            },
            settle_time_s=params.response_settle_time_s,
            duration_s=params.response_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        summaries.append(summary)
        hardware_records.append(hardware)
        accepted_attempts.append(attempt)
        accepted_files.append(filename)
    rf.set_output(False, channel=channels["y_rf"])
    np.savez(
        run_dir.raw / "amplitude_scan.npz",
        signed_amplitude_vpp=axis,
        hardware_amplitude_vpp=[item["hardware_amplitude_vpp"] for item in hardware_records],
        hardware_phase_deg=[item["hardware_phase_deg"] for item in hardware_records],
        output_on=[item["output_on"] for item in hardware_records],
        r_mean_v=[item["r_mean_v"] for item in summaries],
        r_scalar_mean_v=[item["r_scalar_mean_v"] for item in summaries],
        r_std_v=[item["r_std_v"] for item in summaries],
        accepted_attempt_index=accepted_attempts,
        accepted_file=accepted_files,
        r_std_threshold_v=np.float64(
            params.r_bad_point_std_threshold_v
        ),
        actual_rate_sa_s=np.float64(actual_rate),
    )


def _set_y_rf_zero_off(rf: Any, channel: int) -> None:
    """噪声采集前将 Y RF 安全切换为 0 V DC 且关闭输出。"""
    validate_safety_limit("rf_coil", 0.0)
    rf.set_output(False, channel=channel)
    rf.setup_dc(0.0, channel=channel)
    rf.set_output(False, channel=channel)


def _acquire_noise(
    params: MxYRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    device_id: str,
) -> float:
    rf = devices["xy_field"]
    hf2 = devices["hf2"]
    _set_y_rf_zero_off(rf, channels["y_rf"])
    actual_rate = demod.configure_demodulator(
        hf2,
        DemodulatorConfig(
            demod_index=params.demod_idx,
            enable=True,
            rate=params.noise_rate_sa_s,
            input_channel=0,
            osc_select=params.demod_osc_idx,
            harmonic=1,
            time_constant=params.noise_time_constant_s,
            order=params.noise_demod_order,
            phase=float(hf2.get_double(f"{hf2.demod_path(params.demod_idx)}/phaseshift")),
        ),
    )
    for index in range(params.noise_n_avg):
        check_cancelled()
        print(f"零 Y RF 噪声 {index + 1}/{params.noise_n_avg}")
        payload = _temperature_gated_acquire(
            params,
            devices,
            channels,
            settle_time_s=0.0,
            acquire=lambda: acquire_r(
                hf2,
                device_id=device_id,
                demod_idx=params.demod_idx,
                actual_rate_sa_s=float(actual_rate),
                duration_s=params.noise_duration_s,
            ),
        )
        _save_point(
            run_dir.raw / f"noise_{index:03d}.npz",
            payload,
            {
                "y_rf_dc_v": np.float64(0.0),
                "y_rf_output_on": np.uint8(0),
            },
            float(actual_rate),
        )
    return float(actual_rate)


def run(params: MxYRFParams) -> Path:
    root = find_project_root()
    mapping = load_mapping(root)
    run_dir = create_run_directory(DATA_TYPE, params.run_tag, params.to_external(), schema_version=params.schema_version, project_root=root)
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        geometry={"main_field": "Z", "pump": "Z", "probe": "X", "rf_field": "Y", "x_field_output": "OFF"},
        linewidth_mode=params.linewidth_mode,
        compatibility={"stable_data_type": DATA_TYPE, "schema_version": params.schema_version},
        acquisition_signals=["R"],
        r_point_quality={
            "criterion": "std(R) <= threshold",
            "std_threshold_v": params.r_bad_point_std_threshold_v,
            "max_attempts": params.r_point_max_attempts,
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
        actual_response_rate, clock_sources = _configure_outputs(
            params,
            devices,
            channels,
            mapping,
        )
        run_dir.update_config(clock_sources=clock_sources)
        device_id = str(mapping["lockin_r"]["device_id"])
        frequency_fit = None
        if params.linewidth_mode == "frequency_sweep":
            frequency_fit = _acquire_frequency_scan(params, run_dir, devices, channels, actual_response_rate, device_id)
        _acquire_amplitude_scan(params, run_dir, devices, channels, actual_response_rate, device_id)
        actual_noise_rate = _acquire_noise(params, run_dir, devices, channels, device_id)
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            actual_rates={"response_sa_s": actual_response_rate, "noise_sa_s": actual_noise_rate},
            frequency_gate_fit=frequency_fit,
            data_files=[
                "raw/amplitude_scan.npz",
                *( ["raw/frequency_scan.npz"] if params.linewidth_mode == "frequency_sweep" else [] ),
                *[f"raw/noise_{index:03d}.npz" for index in range(params.noise_n_avg)],
            ],
        )
        print(f"Mx Y RF 灵敏度采集完成: {run_dir.root}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(completion_status="failed", failure_reason=failure_reason)
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
    params = load_runtime_params(MxYRFParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
