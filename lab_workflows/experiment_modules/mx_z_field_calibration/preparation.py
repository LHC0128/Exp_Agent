"""无硬件预检、连接复用、时钟回读、初始输出与标准收尾。"""
from __future__ import annotations
from typing import Any
from gs200 import GS200Instrument
from sds_acquisition import SDSAcquisition, SDSInstrument
from tec_controller import TECInstrument
from ...common import load_mapping, validate_safety_limit
from ...devices import create_lockin, create_signal_generator
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import (DeviceSession, initialize_lockin_inputs, connect_signal_generator_routes,
    synchronize_connected_clocks, DGChannelShutdown, ShutdownAction, TemperatureSwitchRestore,
    DisconnectTarget, STANDARD_PRESERVED_OUTPUTS, SafetyShutdownReport, run_safety_shutdown,
    set_temperature_switch, configure_temperature_control)
from .models import MxZFieldCalibrationParams
from .run_format import EXPERIMENT_ID, REQUIRED_MAPPING_KEYS
from .excitation import _set_z_dc_bias, _set_pump_gate_on, configure_pump, configure_response
from ._helpers import _sleep


def preflight(params, root):
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    return params, mapping

def _identity(device: Any) -> str | None:
    try:
        value = device.idn() if callable(getattr(device, "idn", None)) else device.idn
        return str(value)
    except Exception:
        return None


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
        lambda: create_lockin(hf_cfg),
    )
    initialize_lockin_inputs(devices["hf2"])

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
        settle_wait=_sleep,
    )
    return {
        name: {"target": str(record["target"]), "actual": str(record["actual"])}
        for name, record in records.items()
    }


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    params: MxZFieldCalibrationParams,
    *, disconnect_tec: bool = True,
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
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),) if disconnect_tec else (),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )


def configure_initial_outputs(params, devices, channels, mapping):
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
    validate_safety_limit("main_magnetic_field", 10.0)
    gs200.set_current_limit(0.01)
    validate_safety_limit("main_magnetic_field", params.main_magnetic_field_ma)
    gs200.set_current(params.main_magnetic_field_ma / 1000.0)
    gs200.set_output(True)
    set_temperature_switch(
        devices["temp_switch"], True, channel=channels["temp_switch"]
    )


def _configure_outputs(params, devices, channels, mapping):
    clocks = _configure_reference_clocks(devices, mapping)
    configure_initial_outputs(params, devices, channels, mapping)
    temperature = configure_temperature_control(devices.get("tec"), params.temperature_c, channel=1)
    configure_pump(params, devices, channels)
    rate = configure_response(params, devices, channels)
    return rate, temperature.actual_temperature_c, clocks
