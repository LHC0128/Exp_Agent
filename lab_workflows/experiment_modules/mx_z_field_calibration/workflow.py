"""Mx 高主场 Z 磁场频率标定采集工作流。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from gs200 import GS200Instrument
from sds_acquisition import SDSAcquisition, SDSInstrument
from lockin_amplifier import (
    DemodulatorConfig,
    HF2Instrument,
    OscillatorConfig,
    SignalInputConfig,
    demod,
)
from tec_controller import TECInstrument

from ...common import find_project_root, load_mapping, validate_safety_limit
from ...current_feedback import validate_current_power
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
from ..mx_y_rf_sensitivity.acquisition import acquire_r, summarize_r
from ...steps.scope_waveform import (
    ScopeAutoRangeState,
    ScopeCaptureSettings,
    acquire_autoranged_waveform,
    configure_fixed_rate_scope,
    read_complete_scope_record,
)
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


def _set_z_dc_bias(
    device: Any,
    channel: int,
    voltage_v: float,
    *,
    output: bool,
) -> float:
    """设置 Z DC 命令值，并验证波形模式和输出开关状态。"""
    target = float(validate_safety_limit("Z_magnetic_field", voltage_v))
    try:
        device.setup_dc(target, channel=channel)
        device.set_output(output, channel=channel)
        shape = str(device.get_shape(channel=channel)).strip().strip('"').upper()
        output_on = bool(device.get_output(channel=channel))
    except Exception:
        try:
            device.set_output(False, channel=channel)
        except Exception:
            pass
        raise
    if shape != "DC" or output_on is not bool(output):
        device.set_output(False, channel=channel)
        raise RuntimeError(
            "Z DC 配置验证失败："
            f"波形={shape!r}，输出={'ON' if output_on else 'OFF'}"
        )
    return target


def _connect_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
    *,
    include_main_field: bool = True,
    include_scope: bool = False,
) -> tuple[dict[str, Any], dict[str, int]]:
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}

    if include_main_field:
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
    devices["xy_field"], routed_channels = connect_signal_generator_routes(
        session,
        "xy_field",
        {
            "x_field": ("X_magnetic_field", x_cfg),
            "y_rf": ("rf_coil", rf_cfg),
        },
    )
    channels.update(routed_channels)

    pump_cfg = mapping["Pump_laser_power"]
    probe_cfg = mapping["Probe_laser_power"]
    devices["laser"], routed_channels = connect_signal_generator_routes(
        session,
        "laser",
        {
            "pump_laser": ("Pump_laser_power", pump_cfg),
            "probe_laser": ("Probe_laser_power", probe_cfg),
        },
    )
    channels.update(routed_channels)

    carrier_cfg = mapping["Pump_modulation"]
    gate_cfg = mapping["Time_sequence"]
    devices["pump_rf"], routed_channels = connect_signal_generator_routes(
        session,
        "pump_rf",
        {
            "pump_carrier": ("Pump_modulation", carrier_cfg),
            "pump_gate": ("Time_sequence", gate_cfg),
        },
    )
    channels.update(routed_channels)

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
    if include_scope:
        scope_cfg = mapping["scope_waveform"]
        scope = session.connect(
            "scope",
            str(scope_cfg["resource"]),
            lambda: SDSInstrument(str(scope_cfg["resource"])),
        )
        devices["scope"] = scope
        devices["acquirer"] = session.bind("acquirer", SDSAcquisition(scope))
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
    z_field.set_burst_state(False, channel=channels["z_field"])
    z_field.set_mod_state(False, channel=channels["z_field"])
    _set_z_dc_bias(
        z_field,
        channels["z_field"],
        0.0,
        output=False,
    )

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


def _configure_sense_scope(
    params: Any,
    devices: dict[str, Any],
) -> tuple[Any, ScopeCaptureSettings, ScopeAutoRangeState, dict[str, Any]] | None:
    """为电流耦合标定配置 SDS CH3 的自由触发采集。"""
    if "scope" not in devices:
        return None
    settings = ScopeCaptureSettings(
        sample_rate_sa_s=float(params.sense_scope_sample_rate_sa_s),
        duration_s=float(params.sense_scope_duration_s),
        pd_channel=int(params.sense_scope_channel),
        trigger_mode="AUTO",
        initial_scale_v_div=float(params.sense_scope_initial_scale_v_div),
        offset_v=0.0,
        vertical_divisions=int(params.sense_scope_vertical_divisions),
        scale_min_v_div=float(params.sense_scope_scale_min_v_div),
        scale_max_v_div=float(params.sense_scope_scale_max_v_div),
        auto_range_low_fraction=float(params.sense_scope_auto_range_low_fraction),
        auto_range_high_fraction=float(params.sense_scope_auto_range_high_fraction),
        auto_offset_tolerance_fraction=float(
            params.sense_scope_auto_offset_tolerance_fraction
        ),
        auto_range_max_attempts=int(params.sense_scope_auto_range_max_attempts),
        welch_nperseg=64,
        maximum_frequency_hz=1.0 / max(float(params.sense_scope_duration_s), 1e-6),
        auto_offset_enabled=True,
    )
    config, snapshot = configure_fixed_rate_scope(
        settings,
        devices,
        sleep=time.sleep,
    )
    auto_range = ScopeAutoRangeState(
        scale_v_div=float(snapshot["actual_initial_scale_v_div"]),
        offset_v=float(snapshot["actual_initial_offset_v"]),
        # 直流信号被 offset 居中后只剩噪声，默认不跨工作点缩小量程。
        allow_shrink=bool(
            getattr(params, "sense_scope_auto_range_allow_shrink", False)
        ),
    )
    return config, settings, auto_range, snapshot


def _capture_sense_point(
    params: Any,
    devices: dict[str, Any],
    scope_config: Any,
    settings: ScopeCaptureSettings,
    auto_range: ScopeAutoRangeState,
) -> dict[str, Any]:
    """采集一个 DC 工作点的采样电阻电压并换算平均电流。"""
    waveform = acquire_autoranged_waveform(
        settings,
        scope_config,
        devices["scope"],
        auto_range,
        capture=lambda: read_complete_scope_record(
            settings,
            devices,
            scope_config,
            check_cancelled=check_cancelled,
            sleep=time.sleep,
        ),
        check_cancelled=check_cancelled,
    )
    time_s = np.asarray(waveform["time_s"], dtype=float).reshape(-1)
    voltage_v = np.asarray(waveform["voltage_v"], dtype=float).reshape(-1)
    if voltage_v.size < 4 or not np.all(np.isfinite(voltage_v)):
        raise RuntimeError("SDS 采样电阻电压为空或包含非有限值")
    current_a = voltage_v / float(params.sense_resistor_ohm)
    display_edge_fraction = float(waveform["attempt_display_edge_fraction"][-1])
    if display_edge_fraction >= 0.98:
        attempt_scales = np.asarray(
            waveform["attempt_scales_v_div"], dtype=float
        ).tolist()
        raise RuntimeError(
            "SDS 采样电阻电压到达显示边界，无法排除饱和："
            f"最后量程={float(waveform['scale_used_v_div']):.6g} V/div，"
            f"offset={float(waveform['offset_used_v']):.6g} V，"
            f"电压范围=[{float(np.min(voltage_v)):.6g}, "
            f"{float(np.max(voltage_v)):.6g}] V，"
            f"贴边比例={display_edge_fraction:.6g}，"
            f"尝试量程={attempt_scales}"
        )
    if hasattr(params, "maximum_current_a"):
        validate_current_power(
            current_a,
            float(params.sense_resistor_ohm),
            float(params.sense_resistor_power_rating_w),
            derating_fraction=float(params.sense_resistor_power_derating),
            maximum_current_a=float(params.maximum_current_a),
        )
    return {
        "time_s": time_s,
        "sense_voltage_v": voltage_v,
        "current_a": current_a,
        "sense_voltage_mean_v": float(np.mean(voltage_v)),
        "sense_voltage_std_v": float(np.std(voltage_v)),
        "current_mean_a": float(np.mean(current_a)),
        "current_std_a": float(np.std(current_a)),
        "actual_rate_sa_s": float(1.0 / np.median(np.diff(time_s))),
        "scale_used_v_div": float(waveform["scale_used_v_div"]),
        "offset_used_v": float(waveform["offset_used_v"]),
        "auto_range_attempt_count": int(waveform["attempt_count"]),
        "display_edge_fraction": display_edge_fraction,
    }


def _acquire_scan(
    params: MxZFieldCalibrationParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
    sense_scope: tuple[
        Any,
        ScopeCaptureSettings,
        ScopeAutoRangeState,
        dict[str, Any],
    ] | None = None,
) -> list[str]:
    forward_axis = build_z_axis(params)
    if bool(getattr(params, "bidirectional_scan", False)):
        z_axis = np.concatenate((forward_axis, forward_axis[-2::-1]))
        scan_directions = np.asarray(
            ["forward"] * forward_axis.size
            + ["reverse"] * max(0, forward_axis.size - 1),
            dtype="U8",
        )
    else:
        z_axis = forward_axis
        scan_directions = np.asarray(["forward"] * forward_axis.size, dtype="U8")
    z_field = devices["z_field"]
    rf = devices["xy_field"]
    hf2 = devices["hf2"]
    summary_files: list[str] = []
    auxiliary_files: list[str] = []
    predicted_centers: list[float] = []
    commanded_z_biases: list[float] = []

    for z_index, z_bias in enumerate(z_axis):
        check_cancelled()
        predicted = predicted_center_hz(params, float(z_bias))
        frequency_axis = build_frequency_axis(params, float(z_bias))
        predicted_centers.append(predicted)
        print(
            f"Z 偏置 {z_index + 1}/{len(z_axis)}: {z_bias:+.3f} V，"
            f"预测中心 {predicted:.3f} Hz"
        )
        commanded_z_bias = _set_z_dc_bias(
            z_field,
            channels["z_field"],
            float(z_bias),
            output=True,
        )
        commanded_z_biases.append(commanded_z_bias)
        current_settle_time_s = float(getattr(params, "current_settle_time_s", 0.0))
        if current_settle_time_s > 0.0:
            _sleep(current_settle_time_s)

        point_dir = run_dir.raw / f"z_{z_index:03d}"
        point_dir.mkdir()
        sense_record: dict[str, Any] | None = None
        if sense_scope is not None:
            sense_config, sense_settings, sense_auto_range, _sense_snapshot = sense_scope
            sense_record = _capture_sense_point(
                params,
                devices,
                sense_config,
                sense_settings,
                sense_auto_range,
            )
            np.savez(
                point_dir / "sense_current.npz",
                time_s=sense_record["time_s"],
                sense_voltage_v=sense_record["sense_voltage_v"],
                current_a=sense_record["current_a"],
                sense_voltage_mean_v=np.float64(sense_record["sense_voltage_mean_v"]),
                sense_voltage_std_v=np.float64(sense_record["sense_voltage_std_v"]),
                current_mean_a=np.float64(sense_record["current_mean_a"]),
                current_std_a=np.float64(sense_record["current_std_a"]),
                actual_rate_sa_s=np.float64(sense_record["actual_rate_sa_s"]),
                scale_used_v_div=np.float64(sense_record["scale_used_v_div"]),
                offset_used_v=np.float64(sense_record["offset_used_v"]),
                auto_range_attempt_count=np.int64(
                    sense_record["auto_range_attempt_count"]
                ),
                display_edge_fraction=np.float64(
                    sense_record["display_edge_fraction"]
                ),
            )
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
                    "z_bias_commanded_v": np.float64(commanded_z_bias),
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
            z_bias_commanded_v=np.float64(commanded_z_bias),
            scan_direction=np.asarray(scan_directions[z_index]),
            predicted_center_hz=np.float64(predicted),
            frequency_hz=frequency_axis,
            r_mean_v=[item["r_mean_v"] for item in summaries],
            r_scalar_mean_v=[item["r_scalar_mean_v"] for item in summaries],
            r_std_v=[item["r_std_v"] for item in summaries],
            sense_voltage_mean_v=np.float64(
                sense_record["sense_voltage_mean_v"] if sense_record else np.nan
            ),
            sense_voltage_std_v=np.float64(
                sense_record["sense_voltage_std_v"] if sense_record else np.nan
            ),
            current_mean_a=np.float64(
                sense_record["current_mean_a"] if sense_record else np.nan
            ),
            current_std_a=np.float64(
                sense_record["current_std_a"] if sense_record else np.nan
            ),
            sense_scale_used_v_div=np.float64(
                sense_record["scale_used_v_div"] if sense_record else np.nan
            ),
            sense_offset_used_v=np.float64(
                sense_record["offset_used_v"] if sense_record else np.nan
            ),
            sense_display_edge_fraction=np.float64(
                sense_record["display_edge_fraction"] if sense_record else np.nan
            ),
            accepted_attempt_index=accepted_attempts,
            accepted_file=np.asarray(accepted_files, dtype=str),
            actual_rate_sa_s=np.float64(actual_rate),
        )
        summary_files.append(str(summary_path.relative_to(run_dir.root)).replace("\\", "/"))
        if sense_record is not None:
            auxiliary_files.append(
                str((point_dir / "sense_current.npz").relative_to(run_dir.root)).replace("\\", "/")
            )

    rf.set_output(False, channel=channels["y_rf"])
    np.savez(
        run_dir.raw / "z_scan_index.npz",
        z_bias_v=z_axis,
        z_bias_commanded_v=np.asarray(commanded_z_biases, dtype=float),
        predicted_center_hz=np.asarray(predicted_centers, dtype=float),
        scan_direction=scan_directions,
        summary_file=np.asarray(summary_files, dtype=str),
    )
    return ["raw/z_scan_index.npz", *summary_files, *auxiliary_files]


def run_calibration(
    params: MxZFieldCalibrationParams,
    *,
    experiment_id: str = EXPERIMENT_ID,
    data_type: str = DATA_TYPE,
    completion_label: str = "Mx 高主场 Z 标定",
    include_scope: bool = False,
) -> Path:
    root = find_project_root()
    mapping = load_mapping(root)
    run_dir = create_run_directory(
        data_type,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    run_dir.update_config(
        experiment_id=experiment_id,
        data_type=data_type,
        execution_mode=EXECUTION_MODE,
        geometry={
            "main_field": "Z",
            "pump": "Z",
            "probe": "X",
            "rf_field": "Y",
            "z_dc_scan": (
                "forward_then_reverse"
                if bool(getattr(params, "bidirectional_scan", False))
                else "single_pass_negative_to_positive"
            ),
        },
        analysis_during_acquisition=False,
        acquisition_signals=(
            ["R", "SDS CH3 low-side sense-resistor voltage"]
            if include_scope
            else ["R"]
        ),
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
            devices, channels = _connect_devices(
                mapping,
                session,
                include_scope=include_scope,
            )
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
        sense_scope = _configure_sense_scope(params, devices) if include_scope else None
        if sense_scope is not None:
            run_dir.update_config(
                sense_resistor={
                    "resistance_ohm": float(params.sense_resistor_ohm),
                    "tolerance_percent": float(params.sense_resistor_tolerance_percent),
                    "power_rating_w": float(params.sense_resistor_power_rating_w),
                    "scope_channel": int(params.sense_scope_channel),
                },
                sense_scope_configuration=sense_scope[3],
            )
        data_files = _acquire_scan(
            params,
            run_dir,
            devices,
            channels,
            actual_rate,
            str(mapping["lockin_r"]["device_id"]),
            sense_scope=sense_scope,
        )
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=None,
            data_files=data_files,
        )
        print(f"{completion_label}采集完成: {run_dir.root}")
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


def run(params: MxZFieldCalibrationParams) -> Path:
    """执行原有 Mx Z 电压标定实验。"""
    return run_calibration(params)


def main() -> int:
    params = load_runtime_params(MxZFieldCalibrationParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

