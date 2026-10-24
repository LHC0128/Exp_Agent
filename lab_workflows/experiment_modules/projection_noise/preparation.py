"""无硬件预检、设备准备与安全恢复；Pump 载波/门控全程保持。"""
from __future__ import annotations
from dataclasses import asdict
import hashlib
from pathlib import Path
import time

from gs200 import GS200Instrument
from sds_acquisition import SDSInstrument, SDSAcquisition
from tec_controller import TECInstrument
from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled
from ...instrument_config import validate_mapping_usage
from ...steps import (
    DeviceSession, ScopeCaptureSettings, connect_signal_generator_routes,
    synchronize_connected_clocks, set_temperature_switch, configure_temperature_control,
    configure_fixed_rate_scope, run_safety_shutdown, DGChannelShutdown, ShutdownAction,
    DisconnectTarget, TemperatureSwitchRestore, SafetyShutdownReport, STANDARD_PRESERVED_OUTPUTS,
)
from .calibration import load_main_field_calibration
from .run_format import EXPERIMENT_ID, DATA_TYPE, RAW_FORMAT_VERSION, REQUIRED_MAPPING_KEYS

DG_ROUTES = {
    "z_field": "Z_magnetic_field", "x_field": "X_magnetic_field", "y_field": "Y_magnetic_field",
    "pump_laser": "Pump_laser_power", "probe_laser": "Probe_laser_power", "temp_switch": "Temp_Switch",
}


def _sleep(seconds):
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def preflight(params, root):
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    for key in REQUIRED_MAPPING_KEYS:
        if key not in mapping:
            raise ValueError(f"缺少物理量映射：{key}")
    for key, instrument in (("main_magnetic_field", "gs200"), ("scope_waveform", "sds_acquisition"),
                            ("temperature", "tec_controller")):
        if mapping[key]["instrument"] != instrument:
            raise ValueError(f"{key} 需要 {instrument}")
    for key in (*DG_ROUTES.values(), "Pump_modulation", "Time_sequence"):
        if mapping[key]["instrument"] != "signal_generator":
            raise ValueError(f"{key} 需要信号发生器通道")
    if str(mapping["main_magnetic_field"].get("source_function", "CURRent")).upper() not in ("CURR", "CURRENT"):
        raise ValueError("主磁场映射必须使用 GS200 电流模式")
    calibration = load_main_field_calibration(root, params.main_field_calibration_source_run)
    limits = load_safety_limits(root)
    for key, value in (("main_magnetic_field", calibration.current_for_frequency(params.target_larmor_frequency_hz)),
            ("Pump_laser_power", params.pump_laser_power_v), ("Probe_laser_power", params.probe_laser_power_v),
            ("temperature", params.temperature_c), ("X_magnetic_field", 0.0), ("Y_magnetic_field", 0.0),
            ("Z_magnetic_field", 0.0), ("Temp_Switch", 0.0), ("Temp_Switch", 5.0)):
        validate_safety_limit(key, value, limits)
    return params, mapping


def source_revision(params, root):
    source = load_main_field_calibration(root, params.main_field_calibration_source_run)
    return hashlib.sha256(source.analysis_path.read_bytes()).hexdigest()


def initial_state(params, root):
    calibration = load_main_field_calibration(root, params.main_field_calibration_source_run)
    current = calibration.current_for_frequency(params.target_larmor_frequency_hz)
    phases = ([dict(id="field_off", main_field_output="OFF", description="GS200 output-off background")]
              if params.measure_field_off_control else [])
    phases.append(dict(id="field_on", main_field_output="ON", main_field_current_ma=current,
                       predicted_larmor_frequency_hz=params.target_larmor_frequency_hz))
    return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode="typed_workflow",
        raw_format_version=RAW_FORMAT_VERSION, completion_status="running", acquisition_backend="sds_pd_raw",
        geometry=dict(main_field="Z controlled by GS200", probe="X",
            pump=f"{params.pump_laser_power_v:g} V DC, output {'ON' if params.pump_laser_power_v > 0 else 'OFF'}",
            pd_detection=f"SDS CH{params.scope_pd_channel} raw waveform"),
        measure_field_off_control=params.measure_field_off_control, phases=phases,
        main_field_calibration=dict(source_run=calibration.source_run, analysis_path=str(calibration.analysis_path),
            slope_hz_per_ma=calibration.slope_hz_per_ma, intercept_hz=calibration.intercept_hz,
            r_squared=calibration.r_squared, target_current_ma=current,
            source_sha256=source_revision(params, root)),
        temperature_gating=dict(scope="every_scope_attempt", during_acquisition="0 V DC + output ON",
            between_frames="5 V DC + output ON", off_settle_s=params.system_settle_s,
            on_settle_s=params.temp_switch_on_settle_s), analysis_during_acquisition=False,
        preserved_pump_outputs=["Pump_modulation", "Time_sequence"])


def _scope_settings(params):
    return ScopeCaptureSettings(sample_rate_sa_s=params.scope_sample_rate_sa_s, duration_s=params.scope_duration_s,
        pd_channel=params.scope_pd_channel, trigger_mode=params.scope_trigger_mode,
        initial_scale_v_div=params.scope_initial_scale_v_div, offset_v=params.scope_offset_v,
        vertical_divisions=params.scope_vertical_divisions, scale_min_v_div=params.scope_scale_min_v_div,
        scale_max_v_div=params.scope_scale_max_v_div, auto_range_low_fraction=params.scope_auto_range_low_fraction,
        auto_range_high_fraction=params.scope_auto_range_high_fraction,
        auto_offset_tolerance_fraction=params.scope_auto_offset_tolerance_fraction,
        auto_range_max_attempts=params.scope_auto_range_max_attempts, welch_nperseg=params.welch_nperseg,
        maximum_frequency_hz=params.target_larmor_frequency_hz + params.fit_half_width_hz,
        record_max_attempts=params.scope_record_max_attempts)


def _connect_devices(mapping, session: DeviceSession):
    devices, channels = {}, {}
    cfg = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect("gs200", cfg["resource"], lambda: GS200Instrument(cfg["resource"]))
    # 载波/门控仅参与占用保护，不连接其专用设备，也不写入参考时钟。
    for name, key in DG_ROUTES.items():
        connected, routed = connect_signal_generator_routes(session, name, {name: (key, mapping[key])})
        devices[name] = connected
        channels.update(routed)
    scope_cfg = mapping["scope_waveform"]
    devices["scope"] = session.connect("scope", scope_cfg["resource"], lambda: SDSInstrument(scope_cfg["resource"]))
    devices["acquirer"] = session.bind("acquirer", SDSAcquisition(devices["scope"]))
    tec_cfg = mapping["temperature"]
    devices["tec"] = session.connect_optional("tec", tec_cfg["resource"],
        lambda: TECInstrument(port=tec_cfg["resource"]), device_label="TEC103")
    return devices, channels


def _device_snapshot(mapping, main_field_state):
    return dict(mapping={key: mapping[key] for key in REQUIRED_MAPPING_KEYS},
                main_field_before_configuration=asdict(main_field_state))


def configure_clocks(devices, mapping):
    return synchronize_connected_clocks(devices, mapping, DG_ROUTES)


def configure_outputs(params, devices, channels, mapping):
    for name in ("x_field", "y_field", "z_field"):
        validate_safety_limit(DG_ROUTES[name], 0.0)
        device, channel = devices[name], channels[name]
        device.set_burst_state(False, channel=channel)
        device.set_mod_state(False, channel=channel)
        device.setup_dc(0.0, channel=channel)
        device.set_output(False, channel=channel)
    for name, value in (("pump_laser", params.pump_laser_power_v), ("probe_laser", params.probe_laser_power_v)):
        validate_safety_limit(DG_ROUTES[name], value)
        device, channel = devices[name], channels[name]
        device.setup_dc(value, channel=channel)
        device.set_output(name == "probe_laser" or value > 0, channel=channel)
    set_temperature_switch(devices["temp_switch"], True, channel=channels["temp_switch"])
    gs = devices["gs200"]
    gs.set_output(False)
    if source := mapping["main_magnetic_field"].get("source_function"):
        gs.set_source_function(source)
    gs.set_current_limit(float(load_safety_limits()["main_magnetic_field"]["max"]) / 1000)


def configure_scope(params, devices):
    return configure_fixed_rate_scope(_scope_settings(params), devices, sleep=_sleep)


def safe_shutdown(devices, channels, main_field_guard, *, disconnect_tec=True):
    dg_channels = [DGChannelShutdown(devices[name], channels[name], DG_ROUTES[name], name)
                   for name in ("x_field", "y_field", "z_field") if name in devices and name in channels]
    actions = [ShutdownAction("停止示波器触发失败", devices["scope"].trigger_stop)] if devices.get("scope") else []
    temp = (TemperatureSwitchRestore(devices["temp_switch"], channels["temp_switch"])
            if devices.get("temp_switch") is not None and "temp_switch" in channels else None)
    report = run_safety_shutdown(dg_channels=dg_channels, temperature_switch=temp, extra_actions=actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),) if disconnect_tec else (),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"))
    return SafetyShutdownReport(action_errors=(*report.action_errors, *main_field_guard.restore()),
        disconnect_errors=report.disconnect_errors, preserved_outputs=report.preserved_outputs)
