"""离线预检、资源复用、初始条件和声明式安全收尾。"""
from __future__ import annotations
import math
from gs200 import GS200Instrument
from tec_controller import TECInstrument
from lockin_amplifier import lockin_capabilities
from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_lockin
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import (
    connect_signal_generator_routes, initialize_lockin_inputs, synchronize_connected_clocks,
    DGChannelShutdown, TemperatureSwitchRestore, DisconnectTarget, run_safety_shutdown,
)
from . import run_format

SIGNALS = tuple(key for key in run_format.REQUIRED_MAPPING_KEYS
                if key not in ("main_magnetic_field", "temperature", "lockin_r"))


def preflight(params, root):
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    validate_mapping_usage(run_format.REQUIRED_MAPPING_KEYS, root)
    mapping = load_mapping(root)
    require_lockin_compatibility(run_format.EXPERIMENT_ID, params.to_external(), mapping=mapping)
    limits = load_safety_limits(root)
    cfg = mapping["lockin_r"]
    caps = lockin_capabilities(cfg["model"], cfg.get("capabilities", {}).get("options", []))
    if params.demod3_freq_stop_hz > caps["max_frequency_hz"]:
        raise ValueError("Demod3 扫描频率超过锁相能力")
    key = f'{cfg["device_library_id"]}_aux_output_{params.auxout_index}'
    rule = limits.get(key, {})
    if rule.get("min") is None or rule.get("max") is None:
        raise ValueError(f"{key} 缺少明确安全电压上下限")
    bound = 180.0 if params.auxout_source_select == 3 else math.sqrt(2) * params.demod0_signal_range_v
    for value in (params.auxout_offset_v - abs(params.auxout_scale) * bound,
                  params.auxout_offset_v + abs(params.auxout_scale) * bound):
        validate_safety_limit(key, value, limits)
    for key, offset, amplitude in (
            ("Time_sequence", params.rf_gate_offset, params.rf_gate_amplitude),
            ("X_magnetic_field_AM", params.xy_trigger_offset, params.xy_trigger_amplitude),
            ("Y_magnetic_field_AM", params.xy_trigger_offset, params.xy_trigger_amplitude)):
        for value in (offset - amplitude / 2, offset + amplitude / 2):
            validate_safety_limit(key, value, limits)
    for value in (params.xy_calib_envelope_v,
                  (params.control_freq_start_hz - params.xy_ctrl_b_hz) / params.xy_ctrl_k_hz_per_v,
                  (params.control_freq_stop_hz - params.xy_ctrl_b_hz) / params.xy_ctrl_k_hz_per_v):
        for key in ("X_magnetic_field", "Y_magnetic_field"):
            validate_safety_limit(key, value, limits)
            validate_safety_limit(key, -value, limits)
    validate_safety_limit("PUMP_MOD_DUTY", params.pump_mod_duty, limits)
    return params, mapping


def connect(params, mapping, session, devices):
    cfg = mapping["main_magnetic_field"]
    devices["gs"] = session.connect("gs", str(cfg["resource"]), lambda: GS200Instrument(cfg["resource"]))
    devices["sg"], channels = connect_signal_generator_routes(session, "demod3",
        {key: (key, mapping[key]) for key in SIGNALS})
    cfg = mapping["temperature"]
    devices["tec"] = session.connect_optional("tec", str(cfg["resource"]),
        lambda: TECInstrument(port=cfg["resource"]), device_label="TEC103")
    cfg = mapping["lockin_r"]
    devices["lockin"] = session.connect("lockin", f'{cfg["model"]}://{cfg["device_id"]}@{cfg.get("host")}:{cfg.get("port")}',
        lambda: create_lockin(cfg))
    initialize_lockin_inputs(devices["lockin"])
    return channels


def clocks(devices, mapping):
    physical = {route.mapping_key: route.device for route in devices["sg"].iter_physical_routes()}
    physical["lockin_r"] = devices["lockin"]
    return synchronize_connected_clocks(physical, mapping, {key: key for key in physical})


def initial_outputs(params, devices, channels, mapping, root):
    limits = load_safety_limits(root)
    sg, gs = devices["sg"], devices["gs"]
    for key, value in (("Pump_laser_power", params.pump_laser_power), ("Probe_laser_power", params.probe_laser_power)):
        validate_safety_limit(key, value, limits)
        sg.setup_dc(value, channel=channels[key])
    validate_safety_limit("main_magnetic_field", params.main_magnetic_field, limits)
    gs.set_source_function(mapping["main_magnetic_field"]["source_function"])
    gs.set_current_limit(limits["main_magnetic_field"]["max"] / 1000)
    gs.set_current(params.main_magnetic_field / 1000)
    gs.set_output(True)
    for key in ("X_magnetic_field", "Y_magnetic_field", "X_magnetic_field_AM",
                "Y_magnetic_field_AM", "Z_magnetic_field", "Time_sequence_2"):
        validate_safety_limit(key, 0, limits)
        sg.setup_dc(0, channel=channels[key])
        sg.set_output(False, channel=channels[key])


def shutdown(devices, channels, *, disconnect_tec=True):
    sg = devices.get("sg")
    return run_safety_shutdown(
        dg_channels=tuple(DGChannelShutdown(sg, channels[key], key, key)
            for key in ("X_magnetic_field", "Y_magnetic_field", "X_magnetic_field_AM",
                        "Y_magnetic_field_AM", "Z_magnetic_field") if key in channels),
        temperature_switch=TemperatureSwitchRestore(sg, channels["Temp_Switch"]) if sg else None,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),) if disconnect_tec else (),
    )
