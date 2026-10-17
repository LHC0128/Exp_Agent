"""Mx Y RF 无硬件预检、设备连接、初始条件与安全收尾。"""
from __future__ import annotations
from typing import Any
from ...common import validate_safety_limit
from .models import MxYRFParams
from gs200 import GS200Instrument
from tec_controller import TECInstrument
from ...common import load_mapping
from ...devices import create_signal_generator, create_lockin
from ...lockin_compatibility import require_lockin_compatibility
from ...instrument_config import validate_mapping_usage
from ...steps import (DeviceSession, initialize_lockin_inputs, connect_signal_generator_routes,
    synchronize_connected_clocks, set_temperature_switch, configure_temperature_control,
    DGChannelShutdown, ShutdownAction, TemperatureSwitchRestore, DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS, SafetyShutdownReport, run_safety_shutdown)
from .excitation import _set_pump_gate_on, _set_y_rf_zero_off, configure_pump, configure_response
from ._helpers import _sleep
from .run_format import REQUIRED_MAPPING_KEYS


def preflight(params, root):
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    require_lockin_compatibility("mx-y-rf-sensitivity", params.to_external(), mapping=mapping)
    return params, mapping

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
            "z_field": "Z_magnetic_field",
            "xy_field": "rf_coil",
            "laser": "Pump_laser_power",
            "pump_rf": "Pump_modulation",
            "temp_switch": "Temp_Switch",
            "hf2": "lockin_r",
        },
        profile=profile,
        settle_wait=_sleep,
    )
    return {
        name: {"target": str(record["target"]), "actual": str(record["actual"])}
        for name, record in records.items()
    }

def _connect_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
    *,
    connect_tec: bool = True,
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

    if connect_tec:
        tec_cfg = mapping["temperature"]
        devices["tec"] = session.connect_optional(
            "tec",
            tec_cfg["resource"],
            lambda: TECInstrument(port=tec_cfg["resource"]),
            device_label="TEC103",
        )
    return devices, channels

def safe_shutdown(
    devices: dict[str, Any], channels: dict[str, int], params: MxYRFParams,
    *, disconnect_tec: bool = True,
) -> SafetyShutdownReport:
    """关闭 Z/XY 磁场，按调用方提供的已执行配置保持 Pump 连续常开。"""
    dg_channels: list[DGChannelShutdown] = []
    z_field = devices.get("z_field")
    if z_field is not None and "z_field" in channels:
        dg_channels.append(
            DGChannelShutdown(
                z_field,
                channels["z_field"],
                "Z_magnetic_field",
                "Z 辅助场",
            )
        )
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
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),) if disconnect_tec else (),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )

def _configure_temperature_control(
    params: MxYRFParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    control_tec: bool,
) -> float | None:
    """开启温控开关，并按实验选择是否连接和配置 TEC。"""
    set_temperature_switch(
        devices["temp_switch"],
        True,
        channel=channels["temp_switch"],
    )
    if not control_tec:
        print("温控开关已开启；当前实验不连接 TEC，也不等待温度稳定")
        return None

    status = configure_temperature_control(
        devices.get("tec"),
        params.temperature_c,
        channel=1,
    )
    return status.actual_temperature_c


def configure_initial_outputs(params, devices, channels, mapping):
    """归零辅助场并设置主场与光功率，保持温控开启。"""
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
    validate_safety_limit("rf_coil", 0.0)
    _set_y_rf_zero_off(xy_field, channels['y_rf'])

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

    set_temperature_switch(devices["temp_switch"], True, channel=channels["temp_switch"])


def _configure_outputs(params, devices, channels, mapping, *, control_tec=True):
    """保留旧工作点配置入口；完整与分步使用相同职责函数。"""
    clocks = _configure_reference_clocks(devices, mapping)
    configure_initial_outputs(params, devices, channels, mapping)
    configure_pump(params, devices, channels)
    _configure_temperature_control(params, devices, channels, control_tec=control_tec)
    return configure_response(params, devices, channels), clocks
